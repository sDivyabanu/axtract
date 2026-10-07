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
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-6">
      <h1 className="text-3xl font-bold">ParseAnything</h1>

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
          className="rounded bg-black px-4 py-2 text-white disabled:opacity-50"
        >
          {isLoading ? "Parsing..." : "Parse Document"}
        </button>
      </div>

      {isLoading && <p>Parsing document, please wait...</p>}

      {error && (
        <p role="alert" className="rounded border border-red-600 p-3 text-red-700">
          {error}
        </p>
      )}

      {result && <ResultView result={result} />}
    </main>
  );
}
