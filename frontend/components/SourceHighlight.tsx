"use client";

import type { BBox } from "@/lib/types";
import { bboxToPixelRect, normalizeBBox } from "@/lib/bbox";

interface SourceHighlightProps {
  bbox: BBox | null;
  containerWidth: number;
  containerHeight: number;
  label?: string;
}

export default function SourceHighlight({
  bbox,
  containerWidth,
  containerHeight,
  label,
}: SourceHighlightProps) {
  const box = normalizeBBox(bbox);
  if (!box || containerWidth <= 0 || containerHeight <= 0) {
    return null;
  }

  const rect = bboxToPixelRect(box, containerWidth, containerHeight);

  return (
    <div
      className="absolute pointer-events-none border-2 border-blue-500 bg-blue-500/15 rounded-sm transition-all duration-200"
      style={{
        left: `${rect.left}px`,
        top: `${rect.top}px`,
        width: `${rect.width}px`,
        height: `${rect.height}px`,
      }}
    >
      {label && (
        <span className="absolute -top-5 left-0 rounded bg-blue-600 px-1.5 py-0.5 text-[10px] font-medium text-white whitespace-nowrap">
          {label}
        </span>
      )}
    </div>
  );
}
