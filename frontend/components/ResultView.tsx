"use client";

import { useState } from "react";
import type { DocumentBlock, DocumentResponse } from "@/lib/types";
import ProvenanceOverlay from "./ProvenanceOverlay";

type Tab = "blocks" | "markdown" | "json" | "provenance";

interface ResultViewProps {
  result: DocumentResponse;
}

export default function ResultView({ result }: ResultViewProps) {
  const [activeTab, setActiveTab] = useState<Tab>("blocks");

  const tabs: { key: Tab; label: string }[] = [
    { key: "blocks", label: "Blocks" },
    { key: "markdown", label: "Markdown" },
    { key: "json", label: "JSON" },
    { key: "provenance", label: "Provenance" },
  ];

  return (
    <section className="flex flex-col gap-4">
      {/* Document info */}
      <div className="rounded-lg border border-gray-200 p-4">
        <h2 className="mb-2 text-lg font-semibold">Document Info</h2>
        <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
          <div>
            <span className="text-gray-500">File:</span> {result.filename}
          </div>
          <div>
            <span className="text-gray-500">Type:</span> {result.file_type}
          </div>
          <div>
            <span className="text-gray-500">Pages:</span> {result.page_count}
          </div>
          <div>
            <span className="text-gray-500">Blocks:</span>{" "}
            {result.blocks.length}
          </div>
          <div>
            <span className="text-gray-500">Time:</span>{" "}
            {result.processing_time_ms} ms
          </div>
          <div>
            <span className="text-gray-500">Status:</span>{" "}
            <span
              className={
                result.status === "success"
                  ? "text-green-600"
                  : "text-yellow-600"
              }
            >
              {result.status}
            </span>
          </div>
        </div>
      </div>

      {/* Errors / warnings */}
      {result.errors.length > 0 && (
        <div className="rounded-lg border border-yellow-300 bg-yellow-50 p-4">
          <h3 className="mb-1 font-semibold text-yellow-800">Warnings</h3>
          <ul className="list-disc pl-5 text-sm text-yellow-700">
            {result.errors.map((err, i) => (
              <li key={i}>
                {err.page != null && `Page ${err.page}: `}[{err.code}]{" "}
                {err.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Health report */}
      {result.health_report && Object.keys(result.health_report).length > 0 && (
        <HealthReportCard healthReport={result.health_report} />
      )}

      {/* Cost summary */}
      {result.cost_summary && Object.keys(result.cost_summary).length > 0 && (
        <CostSummaryCard costSummary={result.cost_summary} />
      )}

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-200">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2 text-sm font-medium transition-colors ${
              activeTab === tab.key
                ? "border-b-2 border-gray-900 text-gray-900"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="min-h-[200px]">
        {activeTab === "blocks" && <BlocksTab blocks={result.blocks} />}
        {activeTab === "markdown" && <MarkdownTab markdown={result.markdown} />}
        {activeTab === "json" && <JsonTab result={result} />}
        {activeTab === "provenance" && <ProvenanceTab blocks={result.blocks} />}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Blocks Tab                                                          */
/* ------------------------------------------------------------------ */

function BlocksTab({ blocks }: { blocks: DocumentBlock[] }) {
  if (blocks.length === 0) {
    return <p className="text-sm text-gray-500">No blocks extracted.</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      {blocks.map((block) => (
        <BlockCard key={block.id} block={block} />
      ))}
    </div>
  );
}

function BlockCard({ block }: { block: DocumentBlock }) {
  const typeColors: Record<string, string> = {
    heading: "bg-blue-100 text-blue-800",
    paragraph: "bg-gray-100 text-gray-700",
    list: "bg-green-100 text-green-800",
    table: "bg-purple-100 text-purple-800",
    figure: "bg-orange-100 text-orange-800",
    chart: "bg-pink-100 text-pink-800",
    equation: "bg-red-100 text-red-800",
    header: "bg-yellow-100 text-yellow-800",
    footer: "bg-yellow-100 text-yellow-800",
    unknown: "bg-gray-100 text-gray-600",
  };

  const tagColor = typeColors[block.type] ?? "bg-gray-100 text-gray-600";

  return (
    <div
      className={`rounded-lg border p-3 ${
        block.requires_review
          ? "border-yellow-400 bg-yellow-50"
          : "border-gray-200"
      }`}
    >
      {/* Metadata row */}
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <span className={`rounded px-2 py-0.5 font-medium ${tagColor}`}>
          {block.type}
        </span>
        <span className="text-gray-400">p.{block.page}</span>
        <span className="text-gray-400">{block.extractor}</span>
        {block.confidence !== null && (
          <span
            className={`rounded px-1.5 py-0.5 ${
              block.confidence >= 0.8
                ? "bg-green-100 text-green-700"
                : block.confidence >= 0.5
                  ? "bg-yellow-100 text-yellow-700"
                  : "bg-red-100 text-red-700"
            }`}
          >
            {(block.confidence * 100).toFixed(1)}%
          </span>
        )}
        {block.requires_review && (
          <span className="rounded bg-yellow-200 px-1.5 py-0.5 text-yellow-800">
            ⚠ review
          </span>
        )}
        {block.reading_order !== null && (
          <span className="text-gray-300">#{block.reading_order}</span>
        )}
      </div>

      {/* Content */}
      {block.type === "table" ? (
        <TableRenderer block={block} />
      ) : (
        <p className="whitespace-pre-wrap text-sm">{block.content}</p>
      )}
    </div>
  );
}

function TableRenderer({ block }: { block: DocumentBlock }) {
  const rows = block.metadata?.rows as string[][] | undefined;

  if (!rows || rows.length === 0) {
    return <pre className="text-sm">{block.content}</pre>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr>
            {rows[0].map((cell, i) => (
              <th
                key={i}
                className="border border-gray-300 bg-gray-50 px-3 py-1.5 text-left font-medium"
              >
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(1).map((row, ri) => (
            <tr key={ri}>
              {row.map((cell, ci) => (
                <td key={ci} className="border border-gray-300 px-3 py-1.5">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Markdown Tab                                                        */
/* ------------------------------------------------------------------ */

function MarkdownTab({ markdown }: { markdown: string }) {
  if (!markdown) {
    return <p className="text-sm text-gray-500">No Markdown generated.</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => navigator.clipboard?.writeText(markdown)}
          className="rounded border border-gray-300 px-3 py-1 text-xs hover:bg-gray-50"
        >
          Copy
        </button>
      </div>
      <pre className="max-h-[600px] overflow-auto rounded-lg bg-gray-50 p-4 text-sm whitespace-pre-wrap">
        {markdown}
      </pre>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* JSON Tab                                                            */
/* ------------------------------------------------------------------ */

function JsonTab({ result }: { result: DocumentResponse }) {
  const jsonStr = JSON.stringify(result, null, 2);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => navigator.clipboard?.writeText(jsonStr)}
          className="rounded border border-gray-300 px-3 py-1 text-xs hover:bg-gray-50"
        >
          Copy
        </button>
      </div>
      <pre className="max-h-[600px] overflow-auto rounded-lg bg-gray-50 p-4 text-xs">
        {jsonStr}
      </pre>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Provenance Tab                                                      */
/* ------------------------------------------------------------------ */

function ProvenanceTab({ blocks }: { blocks: DocumentBlock[] }) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-gray-600">
        Click on any block in the list to highlight its bounding box on the page.
        Click on the canvas to select blocks by location.
      </p>
      <ProvenanceOverlay blocks={blocks} />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Health Report Card                                                  */
/* ------------------------------------------------------------------ */

function HealthReportCard({
  healthReport,
}: {
  healthReport: Record<string, unknown>;
}) {
  const overallHealth = healthReport.overall_health as string;
  const flaggedItems = healthReport.flagged_items_count as number;
  const hiddenWarnings = healthReport.hidden_content_warnings as unknown[];
  const promptWarnings = healthReport.prompt_injection_warnings as unknown[];

  const healthColors: Record<string, string> = {
    healthy: "bg-green-100 text-green-800 border-green-300",
    warning: "bg-yellow-100 text-yellow-800 border-yellow-300",
    critical: "bg-red-100 text-red-800 border-red-300",
  };

  return (
    <div
      className={`rounded-lg border p-4 ${
        healthColors[overallHealth] || healthColors.warning
      }`}
    >
      <h3 className="mb-2 font-semibold">Document Health Report</h3>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <div>
          <span className="font-medium">Overall Health:</span>{" "}
          <span className="capitalize">{overallHealth}</span>
        </div>
        <div>
          <span className="font-medium">Flagged Items:</span> {flaggedItems}
        </div>
        <div>
          <span className="font-medium">Pages Scanned:</span>{" "}
          {healthReport.scanned_page_count as number}
        </div>
        <div>
          <span className="font-medium">Blocks Requiring Review:</span>{" "}
          {healthReport.blocks_requiring_review as number}
        </div>
      </div>

      {hiddenWarnings.length > 0 && (
        <div className="mt-3">
          <h4 className="mb-1 font-medium">Hidden Content Warnings:</h4>
          <ul className="list-disc pl-5 text-xs">
            {hiddenWarnings.slice(0, 3).map((warning, i) => (
              <li key={i}>{JSON.stringify(warning)}</li>
            ))}
            {hiddenWarnings.length > 3 && (
              <li>...and {hiddenWarnings.length - 3} more</li>
            )}
          </ul>
        </div>
      )}

      {promptWarnings.length > 0 && (
        <div className="mt-3">
          <h4 className="mb-1 font-medium text-red-700">
            ⚠️ Prompt Injection Warnings:
          </h4>
          <ul className="list-disc pl-5 text-xs text-red-600">
            {promptWarnings.map((warning, i) => (
              <li key={i}>{JSON.stringify(warning)}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Cost Summary Card                                                   */
/* ------------------------------------------------------------------ */

function CostSummaryCard({
  costSummary,
}: {
  costSummary: Record<string, unknown>;
}) {
  const costUsd = costSummary.cost_estimate_usd as number;
  const throughput = costSummary.throughput_pages_per_second as number;
  const costPerPage = costSummary.cost_per_page as number;
  const fileSizeMb = costSummary.file_size_mb as number;

  return (
    <div className="rounded-lg border border-blue-300 bg-blue-50 p-4">
      <h3 className="mb-2 font-semibold text-blue-900">Processing Cost Summary</h3>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <div>
          <span className="font-medium">Estimated Cost:</span> ${costUsd.toFixed(6)}
        </div>
        <div>
          <span className="font-medium">Cost per Page:</span> ${costPerPage.toFixed(6)}
        </div>
        <div>
          <span className="font-medium">Throughput:</span> {throughput.toFixed(2)} pages/sec
        </div>
        <div>
          <span className="font-medium">File Size:</span> {fileSizeMb} MB
        </div>
        <div>
          <span className="font-medium">Processing Time:</span>{" "}
          {costSummary.processing_time_ms as number}ms
        </div>
        <div>
          <span className="font-medium">Blocks Extracted:</span>{" "}
          {costSummary.blocks_extracted as number}
        </div>
      </div>
    </div>
  );
}
