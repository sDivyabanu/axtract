"use client";

import type { FilterState } from "@/lib/filters";
import { formatPageRange } from "@/lib/filters";

interface ActiveFiltersProps {
  filters: FilterState;
  filteredCount: number;
  totalCount: number;
  onRemove: (key: string) => void;
  onClear: () => void;
}

export default function ActiveFilters({
  filters,
  filteredCount,
  totalCount,
  onRemove,
  onClear,
}: ActiveFiltersProps) {
  const chips: { key: string; label: string }[] = [];

  if (filters.query) {
    chips.push({ key: "query", label: `"${filters.query}"` });
  }
  if (filters.pages) {
    chips.push({ key: "pages", label: `Pages ${formatPageRange(filters.pages)}` });
  }
  if (filters.types.size > 0) {
    chips.push({
      key: "types",
      label: Array.from(filters.types).join(", "),
    });
  }
  if (filters.extractors.size > 0) {
    chips.push({
      key: "extractors",
      label: Array.from(filters.extractors).join(", "),
    });
  }
  if (filters.confidence !== "all") {
    chips.push({ key: "confidence", label: `Confidence: ${filters.confidence}` });
  }
  if (filters.review !== "all") {
    chips.push({
      key: "review",
      label: filters.review === "review" ? "Requires review" : "Clean",
    });
  }

  if (chips.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-gray-500">
        Showing {filteredCount.toLocaleString()} of{" "}
        {totalCount.toLocaleString()} blocks
      </span>
      {chips.map((chip) => (
        <span
          key={chip.key}
          className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2.5 py-0.5 text-xs text-gray-700"
        >
          {chip.label}
          <button
            type="button"
            onClick={() => onRemove(chip.key)}
            className="ml-0.5 text-gray-400 hover:text-gray-600"
            aria-label={`Remove ${chip.label} filter`}
          >
            &times;
          </button>
        </span>
      ))}
      <button
        type="button"
        onClick={onClear}
        className="text-xs text-gray-500 hover:text-gray-700 underline"
      >
        Clear all
      </button>
    </div>
  );
}
