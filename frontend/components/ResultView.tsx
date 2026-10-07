"use client";

import { useState } from "react";
import type { DocumentResponse } from "@/lib/types";

interface ResultViewProps {
  result: DocumentResponse;
}

export default function ResultView({ result }: ResultViewProps) {
  const [showJson, setShowJson] = useState(false);

  return (
    <section className="flex flex-col gap-6">
      <div>
        <h2 className="text-xl font-semibold">Document info</h2>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
          <dt>Filename</dt>
          <dd>{result.filename}</dd>
          <dt>File type</dt>
          <dd>{result.file_type}</dd>
          <dt>Pages</dt>
          <dd>{result.page_count}</dd>
          <dt>Blocks</dt>
          <dd>{result.blocks.length}</dd>
          <dt>Processing time</dt>
          <dd>{result.processing_time_ms} ms</dd>
          <dt>Status</dt>
          <dd>{result.status}</dd>
          <dt>Document ID</dt>
          <dd className="font-mono">{result.document_id}</dd>
        </dl>
      </div>

      {result.errors.length > 0 && (
        <div>
          <h2 className="text-xl font-semibold">Warnings</h2>
          <ul className="list-disc pl-6 text-sm">
            {result.errors.map((error, index) => (
              <li key={index}>
                {error.page != null ? `Page ${error.page}: ` : ""}
                [{error.code}] {error.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <h2 className="text-xl font-semibold">Extracted blocks</h2>
        {result.blocks.length === 0 ? (
          <p className="text-sm">No text blocks were found in this document.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {result.blocks.map((block) => (
              <li key={block.id} className="rounded border border-gray-300 p-3">
                <p className="mb-1 text-xs text-gray-600">
                  {block.type} · page {block.page} · {block.extractor} · id{" "}
                  {block.id} · bbox{" "}
                  {block.bbox ? block.bbox.map((n) => n.toFixed(1)).join(", ") : "none"} ·
                  confidence {block.confidence ?? "n/a"}
                </p>
                <p className="whitespace-pre-wrap text-sm">{block.content}</p>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <button
          type="button"
          onClick={() => setShowJson((value) => !value)}
          className="rounded border border-gray-500 px-4 py-2 text-sm"
        >
          {showJson ? "Hide JSON" : "Show JSON"}
        </button>
        {showJson && (
          <pre className="mt-3 max-h-[600px] overflow-auto rounded bg-gray-100 p-3 text-xs">
            {JSON.stringify(result, null, 2)}
          </pre>
        )}
      </div>
    </section>
  );
}
