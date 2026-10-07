import type { BBox } from "./types";

export interface PixelRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export function bboxToPixelRect(
  bbox: BBox,
  containerWidth: number,
  containerHeight: number,
): PixelRect {
  const [x1, y1, x2, y2] = bbox;
  return {
    left: x1 * containerWidth,
    top: y1 * containerHeight,
    width: (x2 - x1) * containerWidth,
    height: (y2 - y1) * containerHeight,
  };
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

/** Clamp a bbox to the page. Returns null if it has no area (or is not finite). */
export function normalizeBBox(bbox: BBox | null | undefined): BBox | null {
  if (!bbox || bbox.some((v) => !Number.isFinite(v))) return null;
  const [x1, y1, x2, y2] = bbox.map(clamp01) as BBox;
  return x2 > x1 && y2 > y1 ? [x1, y1, x2, y2] : null;
}

export function isBBoxValid(bbox: BBox | null): bbox is BBox {
  return normalizeBBox(bbox) !== null;
}
