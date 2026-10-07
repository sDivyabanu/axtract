// Mirrors backend/models/document.py and backend/models/errors.py.
// Keep these in sync when the backend schema changes.

export type BlockType =
  | "heading"
  | "paragraph"
  | "list"
  | "table"
  | "figure"
  | "chart"
  | "equation"
  | "header"
  | "footer"
  | "unknown";

// [x1, y1, x2, y2] normalized 0.0–1.0, origin at top-left of the page.
export type BBox = [number, number, number, number];

export interface DocumentBlock {
  id: string;
  type: BlockType;
  content: string;
  page: number;
  bbox: BBox | null;
  confidence: number | null;
  extractor: string;
  reading_order: number | null;
  requires_review: boolean;
  metadata: Record<string, unknown>;
}

export interface DocumentError {
  code: string;
  message: string;
  page?: number | null;
}

export interface DocumentResponse {
  document_id: string;
  filename: string;
  file_type: string;
  page_count: number;
  processing_time_ms: number;
  status: "success" | "partial";
  blocks: DocumentBlock[];
  markdown: string;
  errors: DocumentError[];
  // Optional preview info. A failed preview never fails the parse.
  preview_available?: boolean;
  preview_pages?: number;
  preview_error?: string | null;
}

// Where a block sits in the preview pages (set for Office formats).
export interface BlockPreview {
  page: number;
  bbox: BBox | null;
}

export interface ErrorResponse {
  status: "error";
  error: {
    code: string;
    message: string;
  };
}
