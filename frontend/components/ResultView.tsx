"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { DocumentBlock, DocumentResponse } from "@/lib/types";
import { generateFilteredMarkdown } from "@/lib/filters";
import { useExplorerState } from "@/lib/useExplorerState";
import FilterToolbar from "./FilterToolbar";
import ActiveFilters from "./ActiveFilters";
import MatchNavigator from "./MatchNavigator";
import PageNavigator from "./PageNavigator";
import SourceViewer from "./SourceViewer";

type Tab = "blocks" | "markdown" | "json";
const BLOCKS_PER_PAGE = 100;

interface ResultViewProps {
  result: DocumentResponse;
  sourceFile?: File | null;
}

export default function ResultView({ result, sourceFile }: ResultViewProps) {
  const [activeTab, setActiveTab] = useState<Tab>("blocks");
  const [selectedBlockId, setSelectedBlockId] = useState<string | null>(null);
  const [sourceViewerOpen, setSourceViewerOpen] = useState(false);
  const explorer = useExplorerState(result);

  const selectedBlock = useMemo(
    () => result.blocks.find((b) => b.id === selectedBlockId) ?? null,
    [result.blocks, selectedBlockId],
  );

  const canShowSource = sourceFile != null && (result.file_type === "jpg" || result.file_type === "jpeg" || result.file_type === "png");

  function handleSelectBlock(block: DocumentBlock) {
    setSelectedBlockId(block.id);
    if (canShowSource) {
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
              currentIndex={explorer.currentMatchIndex}
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
            currentMatchIndex={explorer.currentMatchIndex}
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

  if (sourceViewerOpen && sourceFile && canShowSource) {
    return (
      <div className="flex gap-0 -mx-6 px-6" style={{ width: "calc(100vw - 2rem)", maxWidth: "100vw" }}>
        <div className="w-1/2 min-w-0 pr-3 overflow-y-auto" style={{ maxHeight: "calc(100vh - 120px)" }}>
          {explorerContent}
        </div>
        <div className="w-1/2 min-w-0 sticky top-0" style={{ height: "calc(100vh - 120px)" }}>
          <SourceViewer
            file={sourceFile}
            fileType={result.file_type}
            pageCount={result.page_count}
            selectedBlock={selectedBlock}
            onClose={handleCloseSource}
          />
        </div>
      </div>
    );
  }

  return explorerContent;
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
    borderClass = "border-blue-500 bg-blue-50/50 ring-2 ring-blue-300";
  } else if (highlight) {
    borderClass = "border-amber-400 bg-amber-50/50 ring-1 ring-amber-200";
  } else if (block.requires_review) {
    borderClass = "border-yellow-400 bg-yellow-50";
  } else {
    borderClass = "border-gray-200";
  }

  return (
    <button
      type="button"
      id={`block-${block.id}`}
      onClick={onSelect}
      className={`w-full text-left rounded-lg border p-3 cursor-pointer transition-colors hover:border-blue-300 hover:bg-blue-50/30 focus:outline-none focus:ring-2 focus:ring-blue-400 ${borderClass}`}
      aria-pressed={selected}
    >
      {/* Metadata row */}
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <span className={`rounded px-2 py-0.5 font-medium ${tagColor}`}>
          {block.type}
        </span>
        <span className="text-gray-400">p.{block.page}</span>
        <span className="text-gray-400">{block.extractor}</span>
        <span className="text-gray-300">{block.id}</span>
        {block.confidence !== null && block.confidence !== undefined ? (
          <span
            className={`rounded px-1.5 py-0.5 ${
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
          <span className="text-gray-300">Confidence: —</span>
        )}
        {block.requires_review && (
          <span className="rounded bg-yellow-200 px-1.5 py-0.5 text-yellow-800">
            review
          </span>
        )}
        {block.reading_order !== null && (
          <span className="text-gray-300">#{block.reading_order}</span>
        )}
        {block.bbox && (
          <span className="text-gray-300" title="Has source provenance">
            <svg className="inline h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
            </svg>
          </span>
        )}
      </div>

      {/* Content */}
      {block.type === "table" ? (
        <TableRenderer block={block} query={query} />
      ) : (
        <div className="whitespace-pre-wrap text-sm">
          <HighlightedText text={block.content} query={query} />
        </div>
      )}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* Highlighted Text                                                    */
/* ------------------------------------------------------------------ */

function HighlightedText({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;

  const lower = text.toLowerCase();
  const lowerQ = query.toLowerCase();
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  let pos = lower.indexOf(lowerQ);

  while (pos !== -1) {
    if (pos > lastIndex) parts.push(text.slice(lastIndex, pos));
    parts.push(
      <mark key={pos} className="bg-amber-200 rounded-sm px-0.5">
        {text.slice(pos, pos + query.length)}
      </mark>,
    );
    lastIndex = pos + query.length;
    pos = lower.indexOf(lowerQ, lastIndex);
  }

  if (lastIndex < text.length) parts.push(text.slice(lastIndex));
  return <>{parts}</>;
}

/* ------------------------------------------------------------------ */
/* Table Renderer                                                      */
/* ------------------------------------------------------------------ */

function TableRenderer({
  block,
  query,
}: {
  block: DocumentBlock;
  query: string;
}) {
  const rows = block.metadata?.rows as string[][] | undefined;

  if (!rows || rows.length === 0) {
    return (
      <pre className="text-sm">
        <HighlightedText text={block.content} query={query} />
      </pre>
    );
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
                <HighlightedText text={String(cell)} query={query} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(1).map((row, ri) => (
            <tr key={ri}>
              {row.map((cell, ci) => (
                <td key={ci} className="border border-gray-300 px-3 py-1.5">
                  <HighlightedText text={String(cell)} query={query} />
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
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <TextMatchNav text={markdown} query={query} />
          {hasFilters && (
            <span className="text-xs text-gray-400">
              Showing filtered Markdown
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={() => navigator.clipboard?.writeText(markdown)}
          className="rounded border border-gray-300 px-3 py-1 text-xs hover:bg-gray-50"
        >
          Copy
        </button>
      </div>
      <pre className="max-h-[600px] overflow-auto rounded-lg bg-gray-50 p-4 text-sm whitespace-pre-wrap">
        <HighlightedText text={markdown} query={query} />
      </pre>
    </div>
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

  const displayStr = jsonStr.length > 500000 ? jsonStr.slice(0, 500000) + "\n\n... (truncated for display, use Copy for full JSON)" : jsonStr;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {hasFilters && (
            <div className="flex items-center gap-1 text-xs">
              <button
                type="button"
                onClick={() => setViewMode("filtered")}
                className={`rounded px-2 py-1 ${
                  viewMode === "filtered"
                    ? "bg-gray-900 text-white"
                    : "border border-gray-300 hover:bg-gray-50"
                }`}
              >
                Filtered
              </button>
              <button
                type="button"
                onClick={() => setViewMode("full")}
                className={`rounded px-2 py-1 ${
                  viewMode === "full"
                    ? "bg-gray-900 text-white"
                    : "border border-gray-300 hover:bg-gray-50"
                }`}
              >
                Full
              </button>
            </div>
          )}
          <TextMatchNav text={displayStr} query={query} />
        </div>
        <button
          type="button"
          onClick={() => navigator.clipboard?.writeText(jsonStr)}
          className="rounded border border-gray-300 px-3 py-1 text-xs hover:bg-gray-50"
        >
          Copy
        </button>
      </div>
      <pre className="max-h-[600px] overflow-auto rounded-lg bg-gray-50 p-4 text-xs">
        {query ? (
          <HighlightedText text={displayStr} query={query} />
        ) : (
          displayStr
        )}
      </pre>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Text Match Navigator (reusable for Markdown + JSON)                 */
/* ------------------------------------------------------------------ */

function TextMatchNav({ text, query }: { text: string; query: string }) {
  if (!query) return null;

  const lower = text.toLowerCase();
  const lowerQ = query.toLowerCase();
  let matchCount = 0;
  let pos = lower.indexOf(lowerQ);
  while (pos !== -1) {
    matchCount++;
    pos = lower.indexOf(lowerQ, pos + 1);
  }

  if (matchCount === 0) {
    return <span className="text-xs text-gray-400">No matches</span>;
  }

  return <TextMatchNavInner key={`${query}|${text.length}`} matchCount={matchCount} />;
}

function TextMatchNavInner({ matchCount }: { matchCount: number }) {
  const [matchIndex, setMatchIndex] = useState(0);

  return (
    <div className="flex items-center gap-1 text-xs text-gray-500">
      <button
        type="button"
        onClick={() =>
          setMatchIndex((i) => (i - 1 + matchCount) % matchCount)
        }
        className="rounded border border-gray-300 px-2 py-0.5 hover:bg-gray-50"
      >
        &larr;
      </button>
      <span>
        {matchIndex + 1} / {matchCount}
      </span>
      <button
        type="button"
        onClick={() => setMatchIndex((i) => (i + 1) % matchCount)}
        className="rounded border border-gray-300 px-2 py-0.5 hover:bg-gray-50"
      >
        &rarr;
      </button>
    </div>
  );
}
