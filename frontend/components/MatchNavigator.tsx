"use client";

interface MatchNavigatorProps {
  currentIndex: number;
  totalMatches: number;
  matchPages: number;
  query: string;
  onPrev: () => void;
  onNext: () => void;
}

export default function MatchNavigator({
  currentIndex,
  totalMatches,
  matchPages,
  query,
  onPrev,
  onNext,
}: MatchNavigatorProps) {
  if (!query || totalMatches === 0) return null;

  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="text-gray-600">
        {totalMatches} match{totalMatches !== 1 ? "es" : ""} across {matchPages}{" "}
        page{matchPages !== 1 ? "s" : ""}
      </span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={onPrev}
          className="rounded border border-gray-300 px-2 py-0.5 text-xs hover:bg-gray-50"
          aria-label="Previous match"
        >
          &larr;
        </button>
        <span className="min-w-[60px] text-center text-xs text-gray-500">
          {currentIndex + 1} / {totalMatches}
        </span>
        <button
          type="button"
          onClick={onNext}
          className="rounded border border-gray-300 px-2 py-0.5 text-xs hover:bg-gray-50"
          aria-label="Next match"
        >
          &rarr;
        </button>
      </div>
    </div>
  );
}
