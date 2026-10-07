"use client";

import { useState } from "react";
import FileDropzone from "@/components/FileDropzone";
import ResultView from "@/components/ResultView";
import { ApiError, parseDocument } from "@/lib/api";
import type { DocumentResponse } from "@/lib/types";

export default function Home() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [result, setResult] = useState<DocumentResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  function handleFileSelected(file: File) {
    setSelectedFile(file);
    setResult(null);
    setError(null);
  }

  async function handleParse() {
    if (!selectedFile) return;
    setIsLoading(true);
    setResult(null);
    setError(null);
    try {
      setResult(await parseDocument(selectedFile));
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
    <main className="mx-auto flex w-full max-w-5xl flex-col gap-6 p-6">
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

      {error && (
        <div
          role="alert"
          className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-700"
        >
          {error}
        </div>
      )}

      {result && <ResultView result={result} sourceFile={selectedFile} />}
    </main>
  );
}
