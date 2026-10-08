"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Plus, RotateCcw, ShieldCheck, Sparkles, X, XCircle } from "lucide-react";
import FileDropzone from "@/components/FileDropzone";
import RecentDocuments from "@/components/RecentDocuments";
import ProcessingPipeline from "@/components/ProcessingPipeline";
import ResultView from "@/components/ResultView";
import {
  escalateValidation,
  getDocumentResult,
  promoteRecovery,
  uploadDocument,
  uploadDocumentStream,
} from "@/lib/api-authenticated";
import type { ValidationActions } from "@/components/ValidationPanel";
import { ApiError, parseDocument } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { initialPipeline, type PipelineState } from "@/lib/pipeline";
import { parseDocumentStream, type StreamHandlers } from "@/lib/stream";
import type { DocumentResponse } from "@/lib/types";

type TabStatus = "parsing" | "done" | "error";

interface DocTab {
  id: string;
  name: string;
  status: TabStatus;
  result: DocumentResponse | null;
  error: string | null;
  // Set when the result is saved in history: secondary validation needs the stored original.
  savedId: string | null;
  // Real progress reported by the backend while this file is processed (null for saved results opened from history).
  pipeline: PipelineState | null;
  // The uploaded file, kept so a failed document can be retried.
  file: File | null;
}

let nextTabId = 1;

// How many files to parse at once. The backend runs each parse in its own
// thread, so a small parallel batch keeps CPU usage sane for heavy files.
const PARSE_CONCURRENCY = 3;

// Saving is an add-on: if it is unavailable the document is still parsed the normal way.
const SAVE_UNAVAILABLE = new Set([
  "NETWORK_ERROR",
  "AUTH_REQUIRED",
  "INTERNAL_ERROR",
  "HTTP_401",
  "HTTP_500",
  "HTTP_502",
  "HTTP_503",
]);

export default function Home() {
  const { user, loading: authLoading } = useAuth();
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [tabs, setTabs] = useState<DocTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);
  const [isParsing, setIsParsing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [showUpload, setShowUpload] = useState(false);
  const controllers = useRef(new Map<string, AbortController>());
  const [verifyBusy, setVerifyBusy] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);

  function handleFilesSelected(incoming: File[]) {
    setPendingFiles((prev) => [...prev, ...incoming]);
  }

  // Opening a history item (/?doc=<id>) shows the saved result without parsing again.
  useEffect(() => {
    const savedId = new URLSearchParams(window.location.search).get("doc");
    if (!savedId || authLoading || !user) return;
    let cancelled = false;
    (async () => {
      setIsParsing(true);
      try {
        const saved = await getDocumentResult(savedId);
        if (!cancelled) {
          setTabs((prev) => [
            ...prev,
            {
              id: `tab-${nextTabId++}`,
              name: saved.result.filename ?? "Saved document",
              status: "done",
              result: saved.result,
              error: null,
              savedId,
              pipeline: null,
              file: null,
            },
          ]);
          setActiveTabId((current) => current ?? `tab-${nextTabId - 1}`);
        }
      } catch (err) {
        if (!cancelled) {
          setTabs((prev) => [
            ...prev,
            {
              id: `tab-${nextTabId++}`,
              name: "Saved document",
              status: "error",
              result: null,
              savedId: null,
              pipeline: null,
              file: null,
              error:
                err instanceof ApiError
                  ? `[${err.code}] ${err.message}`
                  : "Could not open the saved document.",
            },
          ]);
          setActiveTabId((current) => current ?? `tab-${nextTabId - 1}`);
        }
      } finally {
        if (!cancelled) setIsParsing(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authLoading, user]);

  const parseAll = useCallback(async () => {
    if (pendingFiles.length === 0 || isParsing) return;
    setIsParsing(true);
    setNotice(null);
    setShowUpload(false);

    const newTabs: DocTab[] = pendingFiles.map((file) => ({
      file,
      id: `tab-${nextTabId++}`,
      name: file.name,
      status: "parsing",
      result: null,
      error: null,
      savedId: null,
      pipeline: initialPipeline(),
    }));
    setTabs((prev) => [...prev, ...newTabs]);
    setActiveTabId((current) => current ?? newTabs[0].id);
    setPendingFiles([]);

    // Map tab id -> file so completion updates the right tab.
    const queue: { tabId: string; file: File }[] = newTabs.map((tab, i) => ({
      tabId: tab.id,
      file: pendingFiles[i],
    }));

    async function updateTab(tabId: string, patch: Partial<DocTab>) {
      setTabs((prev) =>
        prev.map((t) => (t.id === tabId ? { ...t, ...patch } : t)),
      );
    }

    // Use the live-progress endpoint; an older backend without it gets the plain request instead.
    async function streamOrPlain<T>(tabId: string, streamed: () => Promise<T>, plain: () => Promise<T>): Promise<T> {
      try {
        return await streamed();
      } catch (err) {
        if (err instanceof ApiError && err.code === "STREAM_UNAVAILABLE") {
          // No live events from this server: show an honest indeterminate state, not stages that never reported.
          setTabs((prev) => prev.map((t) => (t.id === tabId ? { ...t, pipeline: null } : t)));
          return plain();
        }
        throw err;
      }
    }

    async function parseOne(tabId: string, file: File) {
      const controller = new AbortController();
      controllers.current.set(tabId, controller);
      const handlers: StreamHandlers = {
        signal: controller.signal,
        onPipeline: (update) =>
          setTabs((prev) =>
            prev.map((t) => (t.id === tabId ? { ...t, pipeline: update(t.pipeline ?? initialPipeline()) } : t)),
          ),
      };
      // Signed in: upload through the authenticated endpoint so the document
      // and its parse result are stored in the database (visible in History).
      // Otherwise fall back to the anonymous parse endpoint.
      try {
        let result: DocumentResponse;
        let savedDocId: string | null = null;
        if (user) {
          try {
            const saved = await streamOrPlain(
              tabId,
              () => uploadDocumentStream(file, handlers),
              () => uploadDocument(file),
            );
            result = saved.result;
            savedDocId = saved.document_id;
          } catch (err) {
            if (!(err instanceof ApiError) || !SAVE_UNAVAILABLE.has(err.code)) throw err;
            setNotice(
              "Documents could not be saved to your history, so they were parsed without saving.",
            );
            result = await streamOrPlain(
              tabId,
              () => parseDocumentStream(file, handlers),
              () => parseDocument(file),
            );
          }
        } else {
          result = await streamOrPlain(
            tabId,
            () => parseDocumentStream(file, handlers),
            () => parseDocument(file),
          );
        }
        await updateTab(tabId, { status: "done", result, error: null, savedId: savedDocId });
      } catch (err) {
        const message =
          err instanceof ApiError
            ? err.code === "STREAM_ABORTED"
              ? "Processing was cancelled."
              : `[${err.code}] ${err.message}`
            : "Something went wrong while parsing the document.";
        await updateTab(tabId, { status: "error", error: message });
      } finally {
        controllers.current.delete(tabId);
      }
    }

    // Run with limited concurrency; each file gets its own DB entry.
    const workers = Array.from(
      { length: Math.min(PARSE_CONCURRENCY, queue.length) },
      async () => {
        for (;;) {
          const item = queue.shift();
          if (!item) return;
          await parseOne(item.tabId, item.file);
        }
      },
    );
    await Promise.all(workers);

    setIsParsing(false);
  }, [pendingFiles, isParsing, user]);

  function closeTab(id: string) {
    controllers.current.get(id)?.abort(); // stops the server-side work for this file
    setTabs((prev) => {
      const index = prev.findIndex((t) => t.id === id);
      const next = prev.filter((t) => t.id !== id);
      setActiveTabId((current) => {
        if (current !== id) return current;
        if (next.length === 0) return null;
        return next[Math.min(index, next.length - 1)].id;
      });
      return next;
    });
  }

  const activeTab = tabs.find((t) => t.id === activeTabId) ?? null;

  const validationActions: ValidationActions | undefined =
    activeTab?.savedId && user
      ? {
          busy: verifyBusy,
          error: verifyError,
          onEscalate: async () => {
            const { id, savedId } = activeTab;
            setVerifyBusy(true);
            setVerifyError(null);
            try {
              const out = await escalateValidation(savedId!);
              setTabs((prev) =>
                prev.map((t) =>
                  t.id === id && t.result ? { ...t, result: { ...t.result, validation: out.validation } } : t,
                ),
              );
            } catch (err) {
              setVerifyError(err instanceof Error ? err.message : "Secondary validation failed.");
            } finally {
              setVerifyBusy(false);
            }
          },
          onPromote: async (ids, reason) => {
            const { id, savedId } = activeTab;
            setVerifyBusy(true);
            setVerifyError(null);
            try {
              const out = await promoteRecovery(savedId!, ids, reason);
              setTabs((prev) => prev.map((t) => (t.id === id ? { ...t, result: out.result } : t)));
            } catch (err) {
              setVerifyError(err instanceof Error ? err.message : "Could not accept the recovery.");
            } finally {
              setVerifyBusy(false);
            }
          },
        }
      : undefined;

  function removePending(index: number) {
    setPendingFiles((prev) => prev.filter((_, i) => i !== index));
  }

  function retryTab(tab: DocTab) {
    if (!tab.file) return;
    setPendingFiles((prev) => [...prev, tab.file as File]);
    closeTab(tab.id);
  }

  return (
    <main className="mx-auto flex w-full max-w-[1500px] flex-col gap-6 px-4 py-6 sm:px-6">
      {tabs.length === 0 && (
        <section className="ax-rise relative overflow-hidden rounded-2xl border border-gray-200 bg-white px-6 py-10 shadow-card sm:px-12 sm:py-14">
          <div
            aria-hidden
            className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full opacity-[0.14] blur-3xl"
            style={{ background: "var(--ai-gradient)" }}
          />
          <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-3 py-1 text-xs font-medium text-blue-800">
            <Sparkles size={14} /> Evidence-backed document intelligence
          </span>
          <h2 className="mt-4 max-w-3xl font-display text-3xl font-semibold leading-tight tracking-tight text-gray-900 sm:text-5xl">
            Turn any document into <span className="ax-ai-text">trusted, structured intelligence.</span>
          </h2>
          <p className="mt-4 max-w-2xl text-base text-gray-600 sm:text-lg">
            Extract text, tables, figures and more — with evidence-backed validation built into every step.
          </p>
          <ul className="mt-6 flex flex-wrap gap-x-6 gap-y-2 text-sm text-gray-600">
            {["Security scan before extraction", "Independent check against the original file", "Click any block to see its source"].map((t) => (
              <li key={t} className="flex items-center gap-2">
                <ShieldCheck size={16} className="text-blue-600" aria-hidden /> {t}
              </li>
            ))}
          </ul>
        </section>
      )}

      {tabs.length === 0 || pendingFiles.length > 0 || showUpload ? (
        <FileDropzone
          files={pendingFiles}
          onFilesSelected={handleFilesSelected}
          onRemoveFile={removePending}
          disabled={isParsing}
        />
      ) : (
        <div>
          <button
            type="button"
            onClick={() => setShowUpload(true)}
            className="inline-flex items-center gap-2 rounded-full border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-800 shadow-card transition-colors hover:bg-gray-50"
          >
            <Plus size={16} /> Add more documents
          </button>
        </div>
      )}

      {pendingFiles.length > 0 && (
        <div>
          <button
            type="button"
            onClick={parseAll}
            disabled={isParsing}
            className="inline-flex items-center gap-2 rounded-full bg-blue-600 px-6 py-2.5 text-sm font-medium text-white shadow-card transition-colors hover:bg-blue-700 disabled:opacity-50"
          >
            {isParsing && <Loader2 size={16} className="animate-spin" />}
            {isParsing
              ? "Processing…"
              : `Start extraction · ${pendingFiles.length} ${pendingFiles.length === 1 ? "document" : "documents"}`}
          </button>
        </div>
      )}

      {notice && (
        <div role="status" className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          <AlertTriangle size={18} className="mt-0.5 shrink-0" aria-hidden />
          {notice}
        </div>
      )}

      {tabs.length === 0 && user && <RecentDocuments />}

      {tabs.length > 0 && (
        <div role="tablist" aria-label="Documents" className="flex gap-1.5 overflow-x-auto pb-1">
          {tabs.map((tab) => {
            const active = tab.id === activeTabId;
            const vstatus = tab.result?.validation?.status;
            const dot =
              vstatus === "verified" || vstatus === "recovered"
                ? "text-green-600"
                : vstatus === "failed"
                  ? "text-red-600"
                  : vstatus
                    ? "text-amber-600"
                    : "text-gray-400";
            return (
              <div
                key={tab.id}
                className={`group flex max-w-[260px] min-w-[150px] shrink-0 items-center gap-2 rounded-full border px-3.5 py-2 text-sm transition-all ${
                  active
                    ? "border-blue-200 bg-blue-50 font-medium text-blue-900 shadow-card"
                    : "border-gray-200 bg-white text-gray-600 hover:bg-gray-50"
                }`}
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => setActiveTabId(tab.id)}
                  className="flex min-w-0 flex-1 items-center gap-2 text-left"
                  title={tab.name}
                >
                  {tab.status === "parsing" && <Loader2 size={15} className="shrink-0 animate-spin text-blue-600" aria-label="Processing" />}
                  {tab.status === "error" && <XCircle size={15} className="shrink-0 text-red-600" aria-label="Failed" />}
                  {tab.status === "done" && (
                    <CheckCircle2 size={15} className={`shrink-0 ${dot}`} aria-label={vstatus ? `Validation: ${vstatus.replace(/_/g, " ")}` : "Done"} />
                  )}
                  <span className="truncate">{tab.name}</span>
                </button>
                <button
                  type="button"
                  onClick={() => closeTab(tab.id)}
                  aria-label={`Close ${tab.name}`}
                  className="shrink-0 rounded-full p-0.5 text-gray-400 transition-colors hover:bg-gray-200 hover:text-gray-700"
                >
                  <X size={14} />
                </button>
              </div>
            );
          })}
        </div>
      )}

      {activeTab && (
        <div className="ax-rise min-w-0" key={activeTab.id}>
          {activeTab.pipeline && (
            <div className="mb-4">
              <ProcessingPipeline
                key={activeTab.id}
                fileName={activeTab.name}
                pipeline={activeTab.pipeline}
                compact={activeTab.status === "done"}
                onCancel={() => controllers.current.get(activeTab.id)?.abort()}
              />
            </div>
          )}
          {activeTab.status === "parsing" && !activeTab.pipeline && (
            <div className="flex items-center gap-3 rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-600">
              <Loader2 size={18} className="animate-spin text-blue-600" aria-hidden />
              Processing “{activeTab.name}”…
            </div>
          )}
          {activeTab.status === "error" && (
            <div role="alert" className="flex flex-wrap items-center gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
              <XCircle size={18} className="shrink-0" aria-hidden />
              <span className="min-w-0 flex-1">{activeTab.error}</span>
              {activeTab.file && (
                <button
                  type="button"
                  onClick={() => retryTab(activeTab)}
                  className="inline-flex items-center gap-1.5 rounded-full border border-red-300 bg-white px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-50"
                >
                  <RotateCcw size={14} /> Try again
                </button>
              )}
            </div>
          )}
          {activeTab.status === "done" && activeTab.result && (
            <ResultView key={activeTab.id} result={activeTab.result} validationActions={validationActions} />
          )}
        </div>
      )}
    </main>
  );
}
