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

export function isBBoxValid(bbox: BBox | null): bbox is BBox {
  if (!bbox) return false;
  const [x1, y1, x2, y2] = bbox;
  return (
    x1 >= 0 && y1 >= 0 && x2 > x1 && y2 > y1 && x2 <= 1 && y2 <= 1
  );
}
