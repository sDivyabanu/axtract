"use client";

import { useEffect, useState } from "react";
import FileDropzone from "@/components/FileDropzone";
import ResultView from "@/components/ResultView";
import type { ValidationActions } from "@/components/ValidationPanel";
import { ApiError, parseDocument } from "@/lib/api";
import { escalateValidation, getDocumentResult, promoteRecovery, uploadDocument } from "@/lib/api-authenticated";
import { useAuth } from "@/lib/auth-context";
import type { DocumentResponse } from "@/lib/types";

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
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [result, setResult] = useState<DocumentResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Set when the result is saved in history: secondary validation needs the stored original.
  const [savedId, setSavedId] = useState<string | null>(null);
  const [verifyBusy, setVerifyBusy] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);

  // Opening a history item (/?doc=<id>) shows the saved result without parsing again.
  useEffect(() => {
    const docParam = new URLSearchParams(window.location.search).get("doc");
    if (!docParam || authLoading || !user) return;
    let cancelled = false;
    (async () => {
      setIsLoading(true);
      setError(null);
      try {
        const saved = await getDocumentResult(docParam);
        if (!cancelled) {
          setResult(saved.result);
          setSavedId(docParam);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? `[${err.code}] ${err.message}` : "Could not open the saved document.");
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authLoading, user]);

  function handleFileSelected(file: File) {
    setSelectedFile(file);
    setResult(null);
    setError(null);
    setNotice(null);
    setSavedId(null);
  }

  const validationActions: ValidationActions | undefined =
    savedId && user
      ? {
          busy: verifyBusy,
          error: verifyError,
          onEscalate: async () => {
            setVerifyBusy(true);
            setVerifyError(null);
            try {
              const out = await escalateValidation(savedId);
              setResult((r) => (r ? { ...r, validation: out.validation } : r));
            } catch (err) {
              setVerifyError(err instanceof Error ? err.message : "Secondary validation failed.");
            } finally {
              setVerifyBusy(false);
            }
          },
          onPromote: async (ids, reason) => {
            setVerifyBusy(true);
            setVerifyError(null);
            try {
              setResult((await promoteRecovery(savedId, ids, reason)).result);
            } catch (err) {
              setVerifyError(err instanceof Error ? err.message : "Could not accept the recovery.");
            } finally {
              setVerifyBusy(false);
            }
          },
        }
      : undefined;

  async function handleParse() {
    if (!selectedFile) return;
    setIsLoading(true);
    setResult(null);
    setError(null);
    setNotice(null);
    try {
      if (user) {
        try {
          const saved = await uploadDocument(selectedFile);
          setResult(saved.result);
          setSavedId(saved.document_id);
        } catch (err) {
          if (!(err instanceof ApiError) || !SAVE_UNAVAILABLE.has(err.code)) throw err;
          setNotice("This document could not be saved to your history, so it was parsed without saving.");
          setResult(await parseDocument(selectedFile));
        }
      } else {
        setResult(await parseDocument(selectedFile));
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`[${err.code}] ${err.message}`);
      } else {
        setError("Something went wrong while parsing the document.");
      }
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">AXTRACT</h1>
        <p className="text-sm text-gray-500">
          Universal document ingestion engine
        </p>
      </div>

      <FileDropzone
        selectedFile={selectedFile}
        onFileSelected={handleFileSelected}
        disabled={isLoading}
      />

      <div>
        <button
          type="button"
          onClick={handleParse}
          disabled={!selectedFile || isLoading}
          className="rounded-md bg-gray-900 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-gray-700 disabled:opacity-50"
        >
          {isLoading ? "Parsing…" : "Parse Document"}
        </button>
      </div>

      {isLoading && (
        <p className="text-sm text-gray-500">
          Parsing document, please wait…
        </p>
      )}

      {notice && (
        <div role="status" className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-800">
          {notice}
        </div>
      )}

      {error && (
        <div
          role="alert"
          className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-700"
        >
          {error}
        </div>
      )}

      {result && <ResultView result={result} validationActions={validationActions} />}
    </main>
  );
}
