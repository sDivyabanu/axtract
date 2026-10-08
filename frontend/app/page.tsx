"use client";

import { useCallback, useEffect, useState } from "react";
import FileDropzone from "@/components/FileDropzone";
import ResultView from "@/components/ResultView";
import {
  escalateValidation,
  getDocumentResult,
  promoteRecovery,
  uploadDocument,
} from "@/lib/api-authenticated";
import type { ValidationActions } from "@/components/ValidationPanel";
import { ApiError, parseDocument } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
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

    const newTabs: DocTab[] = pendingFiles.map((file) => ({
      id: `tab-${nextTabId++}`,
      name: file.name,
      status: "parsing",
      result: null,
      error: null,
      savedId: null,
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

    async function parseOne(tabId: string, file: File) {
      // Signed in: upload through the authenticated endpoint so the document
      // and its parse result are stored in the database (visible in History).
      // Otherwise fall back to the anonymous parse endpoint.
      try {
        let result: DocumentResponse;
        let savedDocId: string | null = null;
        if (user) {
          try {
            const saved = await uploadDocument(file);
            result = saved.result;
            savedDocId = saved.document_id;
          } catch (err) {
            if (!(err instanceof ApiError) || !SAVE_UNAVAILABLE.has(err.code)) throw err;
            setNotice(
              "Documents could not be saved to your history, so they were parsed without saving.",
            );
            result = await parseDocument(file);
          }
        } else {
          result = await parseDocument(file);
        }
        await updateTab(tabId, { status: "done", result, error: null, savedId: savedDocId });
      } catch (err) {
        const message =
          err instanceof ApiError
            ? `[${err.code}] ${err.message}`
            : "Something went wrong while parsing the document.";
        await updateTab(tabId, { status: "error", error: message });
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

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">AXTRACT</h1>
        <p className="text-sm text-gray-500">
          Universal document ingestion engine
        </p>
      </div>

      <FileDropzone
        files={pendingFiles}
        onFilesSelected={handleFilesSelected}
        disabled={isParsing}
      />

      {pendingFiles.length > 0 && (
        <div>
          <button
            type="button"
            onClick={parseAll}
            disabled={isParsing}
            className="rounded-md bg-gray-900 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-gray-700 disabled:opacity-50"
          >
            {isParsing
              ? "Parsing…"
              : `Parse ${pendingFiles.length} ${
                  pendingFiles.length === 1 ? "Document" : "Documents"
                }`}
          </button>
        </div>
      )}

      {notice && (
        <div
          role="status"
          className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-800"
        >
          {notice}
        </div>
      )}

      {tabs.length > 0 && (
        <div className="flex gap-1 overflow-x-auto border-b border-gray-200">
          {tabs.map((tab) => (
            <div
              key={tab.id}
              className={`group flex max-w-[220px] min-w-[140px] shrink-0 items-center gap-2 rounded-t-lg border border-b-0 px-3 py-2 text-sm transition-colors ${
                tab.id === activeTabId
                  ? "border-gray-200 bg-white font-medium text-gray-900"
                  : "border-transparent bg-gray-100 text-gray-500 hover:bg-gray-200"
              }`}
            >
              <button
                type="button"
                onClick={() => setActiveTabId(tab.id)}
                className="flex min-w-0 flex-1 items-center gap-2 text-left"
                title={tab.name}
              >
                {tab.status === "parsing" && (
                  <span className="inline-block h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-gray-300 border-t-gray-600" />
                )}
                {tab.status === "error" && (
                  <span className="shrink-0 text-red-500" aria-hidden>
                    !
                  </span>
                )}
                <span className="truncate">{tab.name}</span>
              </button>
              <button
                type="button"
                onClick={() => closeTab(tab.id)}
                aria-label={`Close ${tab.name}`}
                className="shrink-0 rounded px-1 text-gray-400 opacity-0 transition-opacity hover:bg-gray-200 hover:text-gray-700 group-hover:opacity-100"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}

      {activeTab && (
        <div className="min-w-0">
          {activeTab.status === "parsing" && (
            <p className="text-sm text-gray-500">
              Parsing “{activeTab.name}”, please wait…
            </p>
          )}
          {activeTab.status === "error" && (
            <div
              role="alert"
              className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-700"
            >
              {activeTab.error}
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
