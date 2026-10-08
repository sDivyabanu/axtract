"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import katex from "katex";
import type { BlockPreview, DocumentBlock, DocumentResponse, ValidationIssue } from "@/lib/types";
import { resolveIssueTarget } from "@/lib/issueTarget";
import { generateFilteredMarkdown } from "@/lib/filters";
import { useExplorerState } from "@/lib/useExplorerState";
import FilterToolbar from "./FilterToolbar";
import ActiveFilters from "./ActiveFilters";
import MatchNavigator from "./MatchNavigator";
import PageNavigator from "./PageNavigator";
import SourceViewer, { previewTarget } from "./SourceViewer";
import ValidationPanel, { type ValidationActions } from "./ValidationPanel";

type Tab = "blocks" | "markdown" | "json";
const BLOCKS_PER_PAGE = 100;

interface ResultViewProps {
  result: DocumentResponse;
  /** Escalation / promotion handlers; only offered for results saved to history. */
  validationActions?: ValidationActions;
}

export default function ResultView({ result, validationActions }: ResultViewProps) {
  const [activeTab, setActiveTab] = useState<Tab>("blocks");
  const [selectedBlockId, setSelectedBlockId] = useState<string | null>(null);
  const [sourceViewerOpen, setSourceViewerOpen] = useState(false);
  const [focusIssue, setFocusIssue] = useState<(BlockPreview & { key: string }) | null>(null);
  const [activeIssueId, setActiveIssueId] = useState<string | null>(null);
  const explorer = useExplorerState(result);
  // Filters can shrink the match list under the current index: clamp it.
  const safeMatchIndex =
    explorer.totalMatches > 0 ? Math.min(explorer.currentMatchIndex, explorer.totalMatches - 1) : 0;

  const selectedBlock = useMemo(
    () => result.blocks.find((b) => b.id === selectedBlockId) ?? null,
    [result.blocks, selectedBlockId],
  );

  const canShowSource = result.preview_available === true && (result.preview_pages ?? 0) > 0;

  function handleSelectBlock(block: DocumentBlock) {
    setSelectedBlockId(block.id);
    setFocusIssue(null);
    setActiveIssueId(null);
    if (canShowSource) {
      setSourceViewerOpen(true);
    }
  }

  /** Open a validation issue at its original location in the existing source viewer. */
  function handleSelectIssue(issue: ValidationIssue) {
    const { block, target } = resolveIssueTarget(issue, result.blocks, previewTarget);
    if (block) {
      setSelectedBlockId(block.id);
      setActiveTab("blocks");
    }
    setActiveIssueId(issue.id);
    if (target && canShowSource) {
      setFocusIssue({ ...target, key: issue.id });
      setSourceViewerOpen(true);
    }
  }

  function handleCloseSource() {
    setSourceViewerOpen(false);
  }

  const tabs: { key: Tab; label: string; count?: number }[] = [
    { key: "blocks", label: "Blocks", count: explorer.filtered.length },
    { key: "markdown", label: "Markdown" },
    { key: "json", label: "JSON" },
  ];

  const explorerContent = (
    <section className="flex flex-col gap-3 min-w-0">
      {/* Document info */}
      <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-card">
        <h2 className="mb-2 font-display text-lg font-semibold text-gray-900">Document info</h2>
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
            {result.blocks.length.toLocaleString()}
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

      {/* AXTRACT Verify */}
      {result.validation && (
        <ValidationPanel
          validation={result.validation}
          canShowSource={canShowSource}
          activeIssueId={activeIssueId}
          onSelectIssue={handleSelectIssue}
          actions={validationActions}
        />
      )}

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

      {/* Filter toolbar */}
      <FilterToolbar
        query={explorer.filters.query}
        pageInput={explorer.filters.pageInput}
        selectedTypes={explorer.filters.types}
        selectedExtractors={explorer.filters.extractors}
        confidence={explorer.filters.confidence}
        review={explorer.filters.review}
        availableExtractors={explorer.extractors}
        pageCount={result.page_count}
        allBlocks={result.blocks}
        onQueryChange={explorer.setQuery}
        onPageInputChange={explorer.setPageInput}
        onToggleType={explorer.toggleType}
        onToggleExtractor={explorer.toggleExtractor}
        onConfidenceChange={explorer.setConfidence}
        onReviewChange={explorer.setReview}
        onClear={explorer.clearFilters}
      />

      {/* Active filters + match nav */}
      {explorer.active && (
        <div className="flex flex-col gap-2">
          <ActiveFilters
            filters={explorer.effectiveFilters}
            filteredCount={explorer.filtered.length}
            totalCount={result.blocks.length}
            onRemove={explorer.removeFilter}
            onClear={explorer.clearFilters}
          />
          {explorer.debouncedQuery && (
            <MatchNavigator
              currentIndex={safeMatchIndex}
              totalMatches={explorer.totalMatches}
              matchPages={explorer.matchPages}
              query={explorer.debouncedQuery}
              onPrev={explorer.prevMatch}
              onNext={explorer.nextMatch}
            />
          )}
        </div>
      )}

      {/* Page navigator */}
      {result.page_count > 1 && (
        <PageNavigator
          blocks={result.blocks}
          pageCount={result.page_count}
          currentPages={explorer.effectiveFilters.pages}
          onGoToPage={explorer.goToPage}
          onSetPageInput={explorer.setPageInput}
        />
      )}

      {/* Tabs */}
      <div role="tablist" aria-label="Result views" className="flex gap-1 overflow-x-auto border-b border-gray-200">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2 text-sm font-medium transition-colors ${
              activeTab === tab.key
                ? "border-b-2 border-blue-600 text-blue-700"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            {tab.label}
            {tab.count !== undefined && (
              <span className="ml-1.5 text-xs text-gray-400">
                ({tab.count.toLocaleString()})
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="min-h-[200px]">
        {activeTab === "blocks" && (
          <BlocksTab
            blocks={explorer.filtered}
            query={explorer.debouncedQuery}
            matchInfo={explorer.matchInfo}
            currentMatchIndex={safeMatchIndex}
            selectedBlockId={selectedBlockId}
            onSelectBlock={handleSelectBlock}
          />
        )}
        {activeTab === "markdown" && (
          <MarkdownTab
            blocks={explorer.filtered}
            fullMarkdown={result.markdown}
            query={explorer.debouncedQuery}
            hasFilters={explorer.active}
          />
        )}
        {activeTab === "json" && (
          <JsonTab
            result={result}
            filtered={explorer.filtered}
            hasFilters={explorer.active}
            query={explorer.debouncedQuery}
          />
        )}
      </div>
    </section>
  );

  const viewerOpen = sourceViewerOpen && canShowSource;

  return (
    <div className={viewerOpen ? "grid grid-cols-1 gap-4 lg:grid-cols-2" : ""}>
      <div className="min-w-0">
        {!canShowSource && result.preview_error && (
          <p className="mb-2 rounded border border-yellow-300 bg-yellow-50 p-2 text-xs text-yellow-800">
            Preview unavailable: {result.preview_error}
          </p>
        )}
        {explorerContent}
      </div>
      {viewerOpen && (
        <div className="ax-rise sticky top-20 hidden h-[calc(100vh-6rem)] min-w-0 lg:order-first lg:block">
          <SourceViewer
            documentId={result.document_id}
            pageCount={result.preview_pages ?? 1}
            selectedBlock={selectedBlock}
            focus={focusIssue}
            onClose={handleCloseSource}
          />
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Blocks Tab with pagination                                          */
/* ------------------------------------------------------------------ */

function BlocksTab({
  blocks,
  query,
  matchInfo,
  currentMatchIndex,
  selectedBlockId,
  onSelectBlock,
}: {
  blocks: DocumentBlock[];
  query: string;
  matchInfo: { blockIndex: number; positions: number[] }[];
  currentMatchIndex: number;
  selectedBlockId: string | null;
  onSelectBlock: (block: DocumentBlock) => void;
}) {
  const [page, setPage] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);

  const totalPages = Math.ceil(blocks.length / BLOCKS_PER_PAGE);
  const pageBlocks = useMemo(
    () => blocks.slice(page * BLOCKS_PER_PAGE, (page + 1) * BLOCKS_PER_PAGE),
    [blocks, page],
  );

  const [trackedLen, setTrackedLen] = useState(blocks.length);
  if (trackedLen !== blocks.length) {
    setTrackedLen(blocks.length);
    if (page !== 0) setPage(0);
  }

  const [scrollToBlockId, setScrollToBlockId] = useState<string | null>(null);
  const [trackedMatch, setTrackedMatch] = useState(currentMatchIndex);
  if (trackedMatch !== currentMatchIndex) {
    setTrackedMatch(currentMatchIndex);
    if (query && matchInfo.length > 0) {
      let cumulative = 0;
      for (const m of matchInfo) {
        if (cumulative + m.positions.length > currentMatchIndex) {
          const targetPage = Math.floor(m.blockIndex / BLOCKS_PER_PAGE);
          if (targetPage !== page) setPage(targetPage);
          setScrollToBlockId(blocks[m.blockIndex]?.id ?? null);
          break;
        }
        cumulative += m.positions.length;
      }
    }
  }

  useEffect(() => {
    if (!scrollToBlockId) return;
    const timer = setTimeout(() => {
      const el = document.getElementById(`block-${scrollToBlockId}`);
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      setScrollToBlockId(null);
    }, 50);
    return () => clearTimeout(timer);
  }, [scrollToBlockId]);

  if (blocks.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-gray-500">
        No matching blocks found.
      </div>
    );
  }

  const matchBlockIndices = new Set(matchInfo.map((m) => m.blockIndex));

  return (
    <div ref={containerRef} className="flex flex-col gap-3">
      {/* Pagination top */}
      {totalPages > 1 && (
        <PaginationControls
          page={page}
          totalPages={totalPages}
          totalItems={blocks.length}
          perPage={BLOCKS_PER_PAGE}
          onPageChange={setPage}
        />
      )}

      {pageBlocks.map((block, i) => {
        const globalIndex = page * BLOCKS_PER_PAGE + i;
        const isMatch = matchBlockIndices.has(globalIndex);
        return (
          <BlockCard
            key={block.id}
            block={block}
            query={query}
            highlight={isMatch}
            selected={block.id === selectedBlockId}
            onSelect={() => onSelectBlock(block)}
          />
        );
      })}

      {/* Pagination bottom */}
      {totalPages > 1 && (
        <PaginationControls
          page={page}
          totalPages={totalPages}
          totalItems={blocks.length}
          perPage={BLOCKS_PER_PAGE}
          onPageChange={setPage}
        />
      )}
    </div>
  );
}

function PaginationControls({
  page,
  totalPages,
  totalItems,
  perPage,
  onPageChange,
}: {
  page: number;
  totalPages: number;
  totalItems: number;
  perPage: number;
  onPageChange: (p: number) => void;
}) {
  const start = page * perPage + 1;
  const end = Math.min((page + 1) * perPage, totalItems);

  return (
    <div className="flex items-center justify-between text-xs text-gray-500">
      <span>
        {start.toLocaleString()}&ndash;{end.toLocaleString()} of{" "}
        {totalItems.toLocaleString()} blocks
      </span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => onPageChange(0)}
          disabled={page === 0}
          className="rounded border border-gray-300 px-2 py-1 hover:bg-gray-50 disabled:opacity-30"
        >
          First
        </button>
        <button
          type="button"
          onClick={() => onPageChange(page - 1)}
          disabled={page === 0}
          className="rounded border border-gray-300 px-2 py-1 hover:bg-gray-50 disabled:opacity-30"
        >
          &larr;
        </button>
        <span className="px-2">
          Page {page + 1} / {totalPages}
        </span>
        <button
          type="button"
          onClick={() => onPageChange(page + 1)}
          disabled={page >= totalPages - 1}
          className="rounded border border-gray-300 px-2 py-1 hover:bg-gray-50 disabled:opacity-30"
        >
          &rarr;
        </button>
        <button
          type="button"
          onClick={() => onPageChange(totalPages - 1)}
          disabled={page >= totalPages - 1}
          className="rounded border border-gray-300 px-2 py-1 hover:bg-gray-50 disabled:opacity-30"
        >
          Last
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Block Card                                                          */
/* ------------------------------------------------------------------ */

function BlockCard({
  block,
  query,
  highlight,
  selected,
  onSelect,
}: {
  block: DocumentBlock;
  query: string;
  highlight: boolean;
  selected: boolean;
  onSelect: () => void;
}) {
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

  let borderClass: string;
  if (selected) {
    borderClass = "border-blue-500 bg-blue-50/60 ring-2 ring-blue-300";
  } else if (highlight) {
    borderClass = "border-amber-400 bg-amber-50/60 ring-1 ring-amber-200";
  } else if (block.requires_review) {
    borderClass = "border-yellow-400 bg-yellow-50";
  } else {
    borderClass = "border-gray-200 bg-white";
  }

  return (
    <button
      type="button"
      id={`block-${block.id}`}
      onClick={onSelect}
      className={`w-full text-left rounded-xl border p-4 cursor-pointer transition-all shadow-xs hover:border-blue-300 hover:shadow-card focus:outline-none focus:ring-2 focus:ring-blue-400 text-gray-900 ${borderClass}`}
      aria-pressed={selected}
    >
      {/* Metadata row */}
      <div className="mb-2.5 flex flex-wrap items-center gap-2 text-xs">
        <span className={`rounded-md px-2 py-0.5 font-semibold text-xs ${tagColor}`}>
          {block.type}
        </span>
        <span className="text-gray-600 font-medium">p.{block.page}</span>
        <span className="text-gray-600">{block.extractor}</span>
        <span className="text-gray-400 font-mono text-[11px]">{block.id}</span>
        {block.confidence !== null && block.confidence !== undefined ? (
          <span
            className={`rounded px-1.5 py-0.5 font-medium ${
              block.confidence >= 0.85
                ? "bg-green-100 text-green-700"
                : block.confidence >= 0.60
                  ? "bg-yellow-100 text-yellow-700"
                  : "bg-red-100 text-red-700"
            }`}
          >
            {(block.confidence * 100).toFixed(1)}%
          </span>
        ) : (
          <span className="text-gray-400">Confidence: —</span>
        )}
        {block.requires_review && (
          <span className="rounded bg-yellow-200 px-1.5 py-0.5 font-medium text-yellow-800">
            review
          </span>
        )}
        {block.reading_order !== null && (
          <span className="text-gray-400 font-mono">#{block.reading_order}</span>
        )}
        {block.bbox && (
          <span className="text-gray-400" title="Has source provenance">
            <svg className="inline h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
            </svg>
          </span>
        )}
      </div>

      {/* Content */}
      {block.type === "table" ? (
        <TableRenderer block={block} query={query} />
      ) : block.type === "chart" ? (
        <ChartRenderer block={block} />
      ) : block.type === "equation" ? (
        <EquationRenderer block={block} />
      ) : (
        <div className="whitespace-pre-wrap text-sm text-gray-900 font-normal leading-relaxed">
          <HighlightedText text={block.content} query={query} />
        </div>
      )}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* Highlighted Text                                                    */
/* ------------------------------------------------------------------ */

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Highlights every case-insensitive occurrence of `query`. `activeIndex` marks the nth
 * match (0-based, counted from `startOffset`) as the current one.
 */
function HighlightedText({
  text,
  query,
  activeIndex = -1,
}: {
  text: string;
  query: string;
  activeIndex?: number;
}) {
  if (!query) return <span className="text-gray-900">{text}</span>;
  const parts = text.split(new RegExp(`(${escapeRegExp(query)})`, "gi"));
  return (
    <span className="text-gray-900">
      {parts.map((part, i) => {
        if (i % 2 === 0) return part;
        const active = (i - 1) / 2 === activeIndex; // matches sit at odd indices
        return (
          <mark
            key={i}
            id={active ? "active-text-match" : undefined}
            className={`rounded-sm px-0.5 ${active ? "bg-orange-400 text-white" : "bg-amber-200 text-gray-900"}`}
          >
            {part}
          </mark>
        );
      })}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Table Renderer                                                      */
/* ------------------------------------------------------------------ */

type MergedRegion = [number, number, number, number]; // row, col, rowspan, colspan

function TableRenderer({
  block,
  query,
}: {
  block: DocumentBlock;
  query: string;
}) {
  const rows = block.metadata?.rows as (string | null)[][] | undefined;

  if (!rows || rows.length === 0) {
    return (
      <pre className="text-sm">
        <HighlightedText text={block.content} query={query} />
      </pre>
    );
  }

  // Merged cells: the extractor reports the regions; covered cells arrive as null.
  const merged = (block.metadata?.merged_cells as { merged_regions?: MergedRegion[] } | undefined)
    ?.merged_regions ?? [];
  const origins = new Map<string, [number, number]>();
  const covered = new Set<string>();
  for (const [r, c, rs, cs] of merged) {
    origins.set(`${r},${c}`, [rs, cs]);
    for (let i = r; i < r + rs; i++)
      for (let j = c; j < c + cs; j++) if (i !== r || j !== c) covered.add(`${i},${j}`);
  }
  const headerRows = Math.max(
    1,
    Number((block.metadata?.multi_row_header as { header_row_count?: number } | undefined)?.header_row_count ?? 1),
  );

  const renderCell = (cell: string | null, ri: number, ci: number, header: boolean) => {
    if (covered.has(`${ri},${ci}`)) return null;
    const span = origins.get(`${ri},${ci}`);
    const Tag = header ? "th" : "td";
    return (
      <Tag
        key={ci}
        rowSpan={span?.[0]}
        colSpan={span?.[1]}
        className={`border border-gray-300 px-3 py-1.5 ${header ? "bg-gray-50 text-left font-medium" : ""}`}
      >
        <HighlightedText text={cell == null ? "" : String(cell)} query={query} />
      </Tag>
    );
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          {rows.slice(0, headerRows).map((row, ri) => (
            <tr key={ri}>{row.map((cell, ci) => renderCell(cell, ri, ci, true))}</tr>
          ))}
        </thead>
        <tbody>
          {rows.slice(headerRows).map((row, i) => {
            const ri = i + headerRows;
            return <tr key={ri}>{row.map((cell, ci) => renderCell(cell, ri, ci, false))}</tr>;
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Chart + Equation renderers                                          */
/* ------------------------------------------------------------------ */

interface ChartSeries {
  name: string;
  values: (number | null)[];
  x_values?: (number | null)[];
}
interface ChartData {
  title?: string;
  chart_type?: string;
  categories?: string[];
  series?: ChartSeries[];
  x_label?: string;
  y_label?: string;
  extraction_method?: string;
  values_estimated?: boolean;
}

const fmt = (v: number | null | undefined) => (v == null ? "—" : Number(v.toPrecision(7)).toString());

function ChartRenderer({ block }: { block: DocumentBlock }) {
  const data = block.metadata?.chart_data as ChartData | undefined;
  const flags = (block.metadata?.flags as string[] | undefined) ?? [];
  if (!data?.series?.length) {
    return <div className="text-sm text-gray-500">{block.content}</div>;
  }
  const scatter = data.series.some((s) => s.x_values?.length);
  const n = Math.max(...data.series.map((s) => s.values.length));
  return (
    <div className="text-sm">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <span className="font-medium">{data.title || "Chart"}</span>
        <span className="rounded bg-pink-50 px-1.5 py-0.5 text-xs text-pink-700">{data.chart_type}</span>
        <span className="text-xs text-gray-400">{data.extraction_method}</span>
        {data.values_estimated && (
          <span className="rounded bg-yellow-100 px-1.5 py-0.5 text-xs text-yellow-800">values estimated</span>
        )}
        {flags.map((f) => (
          <span key={f} className="rounded bg-red-50 px-1.5 py-0.5 text-xs text-red-700">{f}</span>
        ))}
      </div>
      {(data.x_label || data.y_label) && (
        <div className="mb-1 text-xs text-gray-500">
          {data.x_label && <>x: {data.x_label}</>} {data.y_label && <>y: {data.y_label}</>}
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="border-collapse text-xs">
          <thead>
            <tr>
              <th className="border border-gray-300 bg-gray-50 px-2 py-1 text-left">{scatter ? "x" : ""}</th>
              {data.series.map((s, i) => (
                <th key={i} className="border border-gray-300 bg-gray-50 px-2 py-1 text-left">{s.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: n }, (_, i) => (
              <tr key={i}>
                <td className="border border-gray-300 px-2 py-1 font-medium">
                  {scatter ? fmt(data.series?.[0].x_values?.[i]) : data.categories?.[i] || i + 1}
                </td>
                {data.series?.map((s, k) => (
                  <td key={k} className="border border-gray-300 px-2 py-1 tabular-nums">{fmt(s.values[i])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function EquationRenderer({ block }: { block: DocumentBlock }) {
  const latex = (block.metadata?.latex as string | undefined) ?? block.content;
  const flags = (block.metadata?.flags as string[] | undefined) ?? [];
  const html = useMemo(() => {
    try {
      return katex.renderToString(latex, { displayMode: true, throwOnError: false, trust: false });
    } catch {
      return null;
    }
  }, [latex]);
  return (
    <div className="text-sm">
      {html ? (
        <div className="overflow-x-auto" dangerouslySetInnerHTML={{ __html: html }} />
      ) : (
        <div className="text-red-600">Could not render this LaTeX.</div>
      )}
      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
        <code className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-700">{latex}</code>
        <span className="text-gray-400">{String(block.metadata?.latex_method ?? "")}</span>
        {flags.map((f) => (
          <span key={f} className="rounded bg-red-50 px-1.5 py-0.5 text-red-700">{f}</span>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Markdown Tab                                                        */
/* ------------------------------------------------------------------ */

function MarkdownTab({
  blocks,
  fullMarkdown,
  query,
  hasFilters,
}: {
  blocks: DocumentBlock[];
  fullMarkdown: string;
  query: string;
  hasFilters: boolean;
}) {
  const markdown = useMemo(() => {
    if (!hasFilters) return fullMarkdown;
    return generateFilteredMarkdown(blocks);
  }, [blocks, fullMarkdown, hasFilters]);

  if (!markdown) {
    return <p className="text-sm text-gray-500">No Markdown generated.</p>;
  }

  return (
    <SearchableText
      text={markdown}
      query={query}
      note={hasFilters ? "Showing filtered Markdown" : undefined}
      copyText={markdown}
      preClassName="max-h-[600px] overflow-auto rounded-lg bg-gray-50 p-4 text-sm whitespace-pre-wrap"
    />
  );
}

/* ------------------------------------------------------------------ */
/* JSON Tab                                                            */
/* ------------------------------------------------------------------ */

function JsonTab({
  result,
  filtered,
  hasFilters,
  query,
}: {
  result: DocumentResponse;
  filtered: DocumentBlock[];
  hasFilters: boolean;
  query: string;
}) {
  const [viewMode, setViewMode] = useState<"full" | "filtered">(
    hasFilters ? "filtered" : "full",
  );

  const [trackedHasFilters, setTrackedHasFilters] = useState(hasFilters);
  if (trackedHasFilters !== hasFilters) {
    setTrackedHasFilters(hasFilters);
    setViewMode(hasFilters ? "filtered" : "full");
  }

  const jsonStr = useMemo(() => {
    if (viewMode === "full") return JSON.stringify(result, null, 2);
    return JSON.stringify({ ...result, blocks: filtered }, null, 2);
  }, [result, filtered, viewMode]);

  const displayStr =
    jsonStr.length > 500000
      ? jsonStr.slice(0, 500000) + "\n\n... (truncated for display, use Copy for full JSON)"
      : jsonStr;

  const toggle = hasFilters ? (
    <div className="flex items-center gap-1 text-xs">
      {(["filtered", "full"] as const).map((mode) => (
        <button
          key={mode}
          type="button"
          onClick={() => setViewMode(mode)}
          className={`rounded px-2 py-1 ${
            viewMode === mode ? "bg-gray-900 text-white" : "border border-gray-300 hover:bg-gray-50"
          }`}
        >
          {mode === "filtered" ? "Filtered" : "Full"}
        </button>
      ))}
    </div>
  ) : null;

  return (
    <SearchableText
      text={displayStr}
      query={query}
      leading={toggle}
      copyText={jsonStr}
      preClassName="max-h-[600px] overflow-auto rounded-lg bg-gray-50 p-4 text-xs"
    />
  );
}

/* ------------------------------------------------------------------ */
/* Searchable text: highlights matches and really navigates between them */
/* ------------------------------------------------------------------ */

function SearchableText({
  text,
  query,
  copyText,
  preClassName,
  note,
  leading,
}: {
  text: string;
  query: string;
  copyText: string;
  preClassName: string;
  note?: string;
  leading?: React.ReactNode;
}) {
  const matchCount = useMemo(() => {
    if (!query) return 0;
    return text.split(new RegExp(escapeRegExp(query), "gi")).length - 1;
  }, [text, query]);

  const [rawIndex, setRawIndex] = useState(0);
  const [trackedQuery, setTrackedQuery] = useState(query);
  if (trackedQuery !== query) {
    setTrackedQuery(query);
    setRawIndex(0);
  }
  const index = matchCount > 0 ? Math.min(rawIndex, matchCount - 1) : 0;

  useEffect(() => {
    if (matchCount === 0) return;
    document.getElementById("active-text-match")?.scrollIntoView({ block: "center" });
  }, [index, matchCount, query]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {leading}
          {query && matchCount === 0 && <span className="text-xs text-gray-400">No matches</span>}
          {matchCount > 0 && (
            <div className="flex items-center gap-1 text-xs text-gray-500">
              <button
                type="button"
                onClick={() => setRawIndex((index - 1 + matchCount) % matchCount)}
                className="rounded border border-gray-300 px-2 py-0.5 hover:bg-gray-50"
                aria-label="Previous match"
              >
                &larr;
              </button>
              <span>
                {index + 1} / {matchCount}
              </span>
              <button
                type="button"
                onClick={() => setRawIndex((index + 1) % matchCount)}
                className="rounded border border-gray-300 px-2 py-0.5 hover:bg-gray-50"
                aria-label="Next match"
              >
                &rarr;
              </button>
            </div>
          )}
          {note && <span className="text-xs text-gray-400">{note}</span>}
        </div>
        <button
          type="button"
          onClick={() => navigator.clipboard?.writeText(copyText)}
          className="rounded border border-gray-300 px-3 py-1 text-xs hover:bg-gray-50"
        >
          Copy
        </button>
      </div>
      <pre className={preClassName}>
        <HighlightedText text={text} query={query} activeIndex={index} />
      </pre>
    </div>
  );
}
