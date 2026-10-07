"use client";

import { useRef, useState } from "react";
import type { BlockType } from "@/lib/types";
import type { ConfidenceFilter, ReviewFilter } from "@/lib/filters";

const BLOCK_TYPES: { value: BlockType; label: string }[] = [
  { value: "heading", label: "Heading" },
  { value: "paragraph", label: "Paragraph" },
  { value: "list", label: "List" },
  { value: "table", label: "Table" },
  { value: "figure", label: "Figure" },
  { value: "chart", label: "Chart" },
  { value: "equation", label: "Equation" },
  { value: "header", label: "Header" },
  { value: "footer", label: "Footer" },
  { value: "unknown", label: "Unknown" },
];

const CONFIDENCE_OPTIONS: { value: ConfidenceFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "high", label: "High (≥80%)" },
  { value: "medium", label: "Medium (50–80%)" },
  { value: "low", label: "Low (<50%)" },
  { value: "none", label: "No confidence" },
];

const REVIEW_OPTIONS: { value: ReviewFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "review", label: "Requires review" },
  { value: "clean", label: "Clean" },
];

interface FilterToolbarProps {
  query: string;
  pageInput: string;
  selectedTypes: Set<BlockType>;
  selectedExtractors: Set<string>;
  confidence: ConfidenceFilter;
  review: ReviewFilter;
  availableExtractors: string[];
  pageCount: number;
  onQueryChange: (q: string) => void;
  onPageInputChange: (p: string) => void;
  onToggleType: (t: BlockType) => void;
  onToggleExtractor: (e: string) => void;
  onConfidenceChange: (c: ConfidenceFilter) => void;
  onReviewChange: (r: ReviewFilter) => void;
  onClear: () => void;
}

export default function FilterToolbar({
  query,
  pageInput,
  selectedTypes,
  selectedExtractors,
  confidence,
  review,
  availableExtractors,
  pageCount,
  onQueryChange,
  onPageInputChange,
  onToggleType,
  onToggleExtractor,
  onConfidenceChange,
  onReviewChange,
  onClear,
}: FilterToolbarProps) {
  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50/50 p-3">
      {/* Row 1: Search + Page */}
      <div className="flex flex-wrap gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <svg
            className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
          <input
            type="text"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder="Search document..."
            className="w-full rounded border border-gray-300 bg-white py-1.5 pl-8 pr-8 text-sm focus:border-gray-500 focus:outline-none"
          />
          {query && (
            <button
              type="button"
              onClick={() => onQueryChange("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
              aria-label="Clear search"
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>

        <div className="flex items-center gap-1">
          <label className="text-xs text-gray-500 whitespace-nowrap">Page:</label>
          <input
            type="text"
            value={pageInput}
            onChange={(e) => onPageInputChange(e.target.value)}
            placeholder={`1-${pageCount}`}
            className="w-24 rounded border border-gray-300 bg-white px-2 py-1.5 text-sm focus:border-gray-500 focus:outline-none"
          />
        </div>
      </div>

      {/* Row 2: Filters */}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Dropdown
          label="Type"
          active={selectedTypes.size > 0}
          summary={selectedTypes.size > 0 ? `${selectedTypes.size} selected` : "All"}
        >
          {BLOCK_TYPES.map((bt) => (
            <label key={bt.value} className="flex items-center gap-2 px-3 py-1 text-sm hover:bg-gray-50 cursor-pointer">
              <input
                type="checkbox"
                checked={selectedTypes.has(bt.value)}
                onChange={() => onToggleType(bt.value)}
                className="rounded"
              />
              {bt.label}
            </label>
          ))}
        </Dropdown>

        <Dropdown
          label="Extractor"
          active={selectedExtractors.size > 0}
          summary={selectedExtractors.size > 0 ? `${selectedExtractors.size} selected` : "All"}
        >
          {availableExtractors.map((ext) => (
            <label key={ext} className="flex items-center gap-2 px-3 py-1 text-sm hover:bg-gray-50 cursor-pointer">
              <input
                type="checkbox"
                checked={selectedExtractors.has(ext)}
                onChange={() => onToggleExtractor(ext)}
                className="rounded"
              />
              {ext}
            </label>
          ))}
        </Dropdown>

        <Dropdown
          label="Confidence"
          active={confidence !== "all"}
          summary={CONFIDENCE_OPTIONS.find((o) => o.value === confidence)?.label ?? "All"}
        >
          {CONFIDENCE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => onConfidenceChange(opt.value)}
              className={`w-full text-left px-3 py-1.5 text-sm hover:bg-gray-50 ${
                confidence === opt.value ? "font-medium bg-gray-100" : ""
              }`}
            >
              {opt.label}
            </button>
          ))}
        </Dropdown>

        <Dropdown
          label="Review"
          active={review !== "all"}
          summary={REVIEW_OPTIONS.find((o) => o.value === review)?.label ?? "All"}
        >
          {REVIEW_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => onReviewChange(opt.value)}
              className={`w-full text-left px-3 py-1.5 text-sm hover:bg-gray-50 ${
                review === opt.value ? "font-medium bg-gray-100" : ""
              }`}
            >
              {opt.label}
            </button>
          ))}
        </Dropdown>

        <button
          type="button"
          onClick={onClear}
          className="ml-auto text-xs text-gray-500 hover:text-gray-700"
        >
          Reset filters
        </button>
      </div>
    </div>
  );
}

function Dropdown({
  label,
  active,
  summary,
  children,
}: {
  label: string;
  active: boolean;
  summary: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  function handleBlur(e: React.FocusEvent) {
    if (ref.current && !ref.current.contains(e.relatedTarget as Node)) {
      setOpen(false);
    }
  }

  return (
    <div className="relative" ref={ref} onBlur={handleBlur}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={`flex items-center gap-1 rounded border px-2.5 py-1 text-xs ${
          active
            ? "border-gray-900 bg-gray-900 text-white"
            : "border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
        }`}
      >
        <span>{label}:</span>
        <span className={active ? "font-medium" : ""}>{summary}</span>
        <svg className="h-3 w-3 ml-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="absolute left-0 top-full z-20 mt-1 min-w-[160px] rounded border border-gray-200 bg-white py-1 shadow-lg">
          {children}
        </div>
      )}
    </div>
  );
}
