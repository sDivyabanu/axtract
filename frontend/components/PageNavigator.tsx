"use client";

import { useMemo, useState } from "react";
import type { DocumentBlock } from "@/lib/types";
import { getPageSummaries } from "@/lib/filters";

interface PageNavigatorProps {
  blocks: DocumentBlock[];
  pageCount: number;
  currentPages: number[] | null;
  onGoToPage: (page: number) => void;
  onSetPageInput: (input: string) => void;
}

export default function PageNavigator({
  blocks,
  pageCount,
  currentPages,
  onGoToPage,
  onSetPageInput,
}: PageNavigatorProps) {
  const [goInput, setGoInput] = useState("");
  const [showIndex, setShowIndex] = useState(false);

  const currentPage =
    currentPages && currentPages.length === 1 ? currentPages[0] : null;

  const pageSummaries = useMemo(
    () => getPageSummaries(blocks, pageCount),
    [blocks, pageCount],
  );

  function handleGo() {
    const n = parseInt(goInput, 10);
    if (!isNaN(n) && n >= 1 && n <= pageCount) {
      onGoToPage(n);
      setGoInput("");
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      {/* Direct page jump */}
      <div className="flex items-center gap-1">
        <label className="text-xs text-gray-500">Go to page:</label>
        <input
          type="number"
          min={1}
          max={pageCount}
          value={goInput}
          onChange={(e) => setGoInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleGo()}
          placeholder="#"
          className="w-16 rounded border border-gray-300 px-2 py-1 text-xs focus:border-gray-500 focus:outline-none"
        />
        <button
          type="button"
          onClick={handleGo}
          className="rounded border border-gray-300 px-2 py-1 text-xs hover:bg-gray-50"
        >
          Go
        </button>
      </div>

      {/* Prev/Next page */}
      {currentPage !== null && (
        <div className="flex items-center gap-1 text-xs">
          <button
            type="button"
            onClick={() => onGoToPage(currentPage - 1)}
            disabled={currentPage <= 1}
            className="rounded border border-gray-300 px-2 py-1 hover:bg-gray-50 disabled:opacity-30"
          >
            &larr;
          </button>
          <span className="text-gray-600 min-w-[80px] text-center">
            Page {currentPage} / {pageCount}
          </span>
          <button
            type="button"
            onClick={() => onGoToPage(currentPage + 1)}
            disabled={currentPage >= pageCount}
            className="rounded border border-gray-300 px-2 py-1 hover:bg-gray-50 disabled:opacity-30"
          >
            &rarr;
          </button>
        </div>
      )}

      {/* Show all pages / clear */}
      {currentPages !== null && (
        <button
          type="button"
          onClick={() => onSetPageInput("")}
          className="text-xs text-gray-500 hover:text-gray-700 underline"
        >
          All pages
        </button>
      )}

      {/* Page index dropdown */}
      <div className="relative ml-auto">
        <button
          type="button"
          onClick={() => setShowIndex(!showIndex)}
          className="rounded border border-gray-300 px-2 py-1 text-xs hover:bg-gray-50"
        >
          Page index
        </button>
        {showIndex && (
          <div className="absolute right-0 top-full z-20 mt-1 max-h-64 w-56 overflow-y-auto rounded border border-gray-200 bg-white py-1 shadow-lg">
            {Array.from({ length: pageCount }, (_, i) => i + 1).map((p) => {
              const tags = pageSummaries.get(p);
              const tagStr = tags && tags.size > 0 ? Array.from(tags).join(", ") : "";
              return (
                <button
                  key={p}
                  type="button"
                  onClick={() => {
                    onGoToPage(p);
                    setShowIndex(false);
                  }}
                  className={`w-full text-left px-3 py-1 text-xs hover:bg-gray-50 ${
                    currentPage === p ? "bg-gray-100 font-medium" : ""
                  }`}
                >
                  <span>Page {p}</span>
                  {tagStr && (
                    <span className="ml-2 text-gray-400">{tagStr}</span>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
