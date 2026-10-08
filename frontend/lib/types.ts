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
  // AXTRACT Verify report. Advisory: `status` above is the extraction's status, not a trust verdict.
  validation?: ValidationReport | null;
}

export type ValidationStatus =
  | "verified"
  | "recovered"
  | "review_required"
  | "failed"
  | "not_verifiable";
export type IssueSeverity = "info" | "low" | "medium" | "high" | "critical";

export interface ValidationUnitRef {
  type: string;
  index: number;
  label?: string | null;
}

export interface ValidationIssue {
  id: string;
  code: string;
  severity: IssueSeverity;
  layer: string;
  message: string;
  locator: {
    unit: ValidationUnitRef;
    page?: number | null;
    bbox?: BBox | null;
    block_ids: string[];
    preview?: { page: number; bbox: BBox | null; approximate?: boolean } | null;
  };
  evidence: {
    kind: string;
    engine: string;
    source?: string | null;
    extracted?: string | null;
    detail?: Record<string, unknown>;
  }[];
  recommended_action: string;
  recovery_id?: string | null;
}

export interface ValidationCheck {
  check: string;
  outcome: string;
  evidence_kind: string;
  engine?: string | null;
  summary: string;
}

export interface ValidationUnitResult {
  unit: ValidationUnitRef;
  status: ValidationStatus;
  checks: ValidationCheck[];
  issue_ids: string[];
  recovery_ids: string[];
  status_reasons: string[];
  verified_by: string[];
  gaps: string[];
}

export interface ValidationRecovery {
  id: string;
  issue_id: string;
  unit: ValidationUnitRef;
  provider: string;
  decision: "candidate_only" | "promoted" | "rejected";
  decided_by?: string | null;
  reason: string;
  original: { content: string; block_ids: string[] };
  candidate: { content: string; provider: string };
  comparison: { metric: string; value: number | string | null; note?: string }[];
  promoted_block_ids: string[];
}

export interface ValidationReport {
  status: ValidationStatus;
  status_reason: string;
  summary: {
    units_total: number;
    verified: number;
    recovered: number;
    review_required: number;
    failed: number;
    not_verifiable: number;
    evidence_coverage: number | null;
    agreement_rate: number | null;
    checks_decided: number;
    checks_agreed: number;
    issues_total: number;
    issues_open: number;
    definitions: Record<string, string>;
  };
  checks: Record<string, { status: string; passed: number; failed: number; not_verifiable: number }>;
  units: ValidationUnitResult[];
  issues: ValidationIssue[];
  recoveries: ValidationRecovery[];
  providers: { name: string; tier: string; available: boolean; note: string }[];
  timings_ms: Record<string, number>;
  failure?: { stage: string; error_type: string; message: string } | null;
  truncated?: string;
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
