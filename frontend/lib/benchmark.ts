import { ApiError } from "./api";
import { localFetch, handleResponse } from "./api-documents";

export interface BenchCategory {
  name: string;
  pdfs: number;
  tests: number;
}

export interface BenchCategoriesResponse {
  modes: string[];
  defaults: { mode: string; per_category: number; seed: number; workers: number; timeout_s: number; max_per_category: number; max_workers: number };
  categories: BenchCategory[];
}

export interface BenchEnvironment {
  dataset_available: boolean;
  scorer_available: boolean;
  extractor_available: boolean;
  official_benchmark_active: boolean;
  active_run_id: string | null;
  free_memory_mb: number | null;
  limits: { max_workers: number; max_per_category: number; min_timeout_s: number; max_timeout_s: number };
}

export interface BenchCounts {
  total: number;
  done: number;
  failed: number;
  timed_out: number;
  empty: number;
  pending: number;
}

export type BenchStatus = "created" | "running" | "completed" | "failed" | "cancelled" | "interrupted" | "imported";
export type ScoreKind = "quick_sample" | "full_pending" | "full_official" | "full_incomplete" | "imported_invalid" | "imported_official";

export interface GitInfo {
  sha: string | null;
  branch: string | null;
  dirty: boolean | null;
}

export interface BenchRunStatus {
  run_id: string;
  mode: string;
  score_kind: ScoreKind;
  is_official: boolean;
  score_label: string;
  created_at: string;
  config: { categories: string[]; per_category: number | null; seed: number | null; workers: number | null; timeout_s: number | null };
  git: GitInfo;
  totals: { documents: number; test_cases: number };
  status: BenchStatus;
  phase: string | null;
  counts: BenchCounts | null;
  elapsed_s: number;
  error: string | null;
  resumable: boolean;
  cancel_requested?: boolean;
  sample_id: string | null;
  dataset_id: string | null;
  failures: { pdf: string; category: string; error: string | null; timed_out: boolean }[];
  failure_count: number;
}

export interface BenchHistoryItem {
  run_id: string;
  mode: string;
  score_kind: ScoreKind;
  is_official: boolean;
  score_label: string;
  status: BenchStatus;
  created_at: string;
  counts: BenchCounts | null;
  elapsed_s: number;
  config: BenchRunStatus["config"];
  git: GitInfo;
  sample_id: string | null;
  dataset_id: string | null;
  imported: { source: string; official_status: string } | null;
  validity: "valid" | "invalid" | "error" | null;
  completeness: "complete" | "incomplete" | null;
  overall_score_pct: number | null;
  diagnostic_score_pct: number | null;
  category_scores: Record<string, number>;
  median_ms_per_page: number | null;
}

export interface BenchCategoryScore {
  pass_rate_pct: number;
  passed: number;
  total: number;
  basis: "official" | "diagnostic";
}

export interface BenchResults {
  run_id: string;
  is_official: boolean;
  score_kind: ScoreKind;
  score_label: string;
  validity: "valid" | "invalid" | "error";
  completeness: "complete" | "incomplete";
  scoring_error: string | null;
  notes: string[];
  official_status?: string;
  overall_score_pct: number | null;
  confidence_interval: string | null;
  diagnostic_score_pct: number | null;
  categories: Record<string, BenchCategoryScore>;
  documents: { total: number; succeeded: number; failed: number; timed_out: number; empty: number };
  cases: { evaluated: number; missing_output: number | null; failed: number | null };
  timing: { per_page_ms: { mean: number | null; median: number | null; max: number | null }; total_extraction_ms: number | null };
  has_report: boolean;
  scorer_error_count: number;
  scorer_errors: string[];
}

export interface CaseItem {
  id: string;
  pdf: string;
  page: number;
  type: string;
  category: string | null;
  passed: boolean;
  expected: string;
  explanation: string;
}

export interface CasePage {
  total: number;
  offset: number;
  limit: number;
  items: CaseItem[];
  run_totals: { failed: number; passed: number };
}

export interface CaseDetail {
  id: string;
  pdf: string;
  page: number;
  type: string;
  category: string | null;
  passed: boolean;
  failure_reason: "timeout" | "extraction_error" | "no_output" | "empty_output" | "content_mismatch" | null;
  expected: string;
  scorer_feedback: string | null;
  document: { status: string; pages: number; total_ms: number; per_page_ms: number | null; empty: boolean; timed_out: boolean; error: string | null } | null;
  actual_output: { available: boolean; reason?: string; text?: string; size_bytes?: number; truncated?: boolean };
  logs: string[];
}

export interface RunSummary {
  run_id: string;
  created_at: string;
  git: GitInfo;
  mode: string;
  score_kind: ScoreKind;
  score_label: string;
  validity: string | null;
  completeness: string | null;
  overall_score_pct: number | null;
  diagnostic_score_pct: number | null;
  categories_tested: string[];
  documents: BenchResults["documents"];
  elapsed_s: number;
  median_ms_per_page: number | null;
}

export interface BenchComparison {
  a: RunSummary;
  b: RunSummary;
  comparable: boolean;
  partial: boolean;
  blockers: string[];
  warnings: string[];
  categories: { category: string; a_pct: number | null; b_pct: number | null; delta_pct: number | null; a_passed: number | null; a_total: number | null; b_passed: number | null; b_total: number | null }[];
  regressions: { category: string; delta_pct: number }[];
  improvements: { category: string; delta_pct: number }[];
  cases: null | { compared: number; newly_passing: number; newly_failing: number; still_passing: number; still_failing: number; newly_passing_list: { id: string; pdf: string; category: string | null }[]; newly_failing_list: { id: string; pdf: string; category: string | null }[] };
  latency: null | { a_median_ms_per_page: number | null; b_median_ms_per_page: number | null; delta_ms: number | null; delta_pct: number | null };
  overall_delta_pct: number | null;
  overall_basis?: "official" | "diagnostic";
}

export interface TrendPoint {
  run_id: string;
  created_at: string;
  sha: string | null;
  dirty: boolean | null;
  mode: string;
  score_kind: ScoreKind;
  validity: string | null;
  sample_id: string | null;
  overall_pct: number | null;
  diagnostic_pct: number | null;
  categories: Record<string, number>;
  elapsed_s: number;
  median_ms_per_page: number | null;
  imported: boolean;
}

export interface TrendResponse {
  sample_id: string | null;
  points: TrendPoint[];
  samples: { sample_id: string; runs: number }[];
  note: string | null;
}

export interface ChangeRecommendation {
  base: { ref: string; sha: string; source: string };
  head: { sha: string };
  changed_files: number;
  truncated: boolean;
  files: { path: string; status: string; component: string; categories: string[]; reason: string; confidence: string }[];
  recommended_categories: string[];
  needs_benchmark: boolean;
  low_confidence: string[];
  summary: string;
}

export interface StartOptions {
  mode: "quick" | "full";
  categories: string[];
  per_category: number | null;
  workers?: number;
  timeout_s?: number;
  confirm_full?: boolean;
}

const json = { "Content-Type": "application/json" };
const id = encodeURIComponent;

export const getBenchCategories = (): Promise<BenchCategoriesResponse> => localFetch("/api/benchmark/categories").then(handleResponse<BenchCategoriesResponse>);
export const getBenchEnvironment = (): Promise<BenchEnvironment> => localFetch("/api/benchmark/environment").then(handleResponse<BenchEnvironment>);
export const startBenchmark = (o: StartOptions): Promise<{ run_id: string }> =>
  localFetch("/api/benchmark/runs", { method: "POST", headers: json, body: JSON.stringify(o) }).then(handleResponse<{ run_id: string }>);
export const listBenchRuns = (): Promise<{ runs: BenchHistoryItem[] }> => localFetch("/api/benchmark/runs").then(handleResponse<{ runs: BenchHistoryItem[] }>);
export const getActiveBenchRun = (): Promise<{ run: BenchRunStatus | null }> => localFetch("/api/benchmark/runs/active").then(handleResponse<{ run: BenchRunStatus | null }>);
export const getBenchRun = (runId: string): Promise<BenchRunStatus> => localFetch(`/api/benchmark/runs/${id(runId)}`).then(handleResponse<BenchRunStatus>);
export const getBenchResults = (runId: string): Promise<BenchResults> => localFetch(`/api/benchmark/runs/${id(runId)}/results`).then(handleResponse<BenchResults>);
export const getBenchLog = (runId: string): Promise<{ log: string }> => localFetch(`/api/benchmark/runs/${id(runId)}/log`).then(handleResponse<{ log: string }>);
export const resumeBenchmark = (runId: string): Promise<unknown> => localFetch(`/api/benchmark/runs/${id(runId)}/resume`, { method: "POST" }).then(handleResponse);
export const cancelBenchmark = (runId: string): Promise<unknown> => localFetch(`/api/benchmark/runs/${id(runId)}/cancel`, { method: "POST" }).then(handleResponse);
export const getChangeRecommendation = (base?: string): Promise<ChangeRecommendation> =>
  localFetch(`/api/benchmark/changes${base ? `?base=${id(base)}` : ""}`).then(handleResponse<ChangeRecommendation>);
export const compareBenchRuns = (a: string, b: string, allowPartial = false): Promise<BenchComparison> =>
  localFetch(`/api/benchmark/compare?a=${id(a)}&b=${id(b)}${allowPartial ? "&allow_partial=true" : ""}`).then(handleResponse<BenchComparison>);
export const getBenchTrends = (sampleId?: string): Promise<TrendResponse> =>
  localFetch(`/api/benchmark/trends${sampleId ? `?sample_id=${id(sampleId)}` : ""}`).then(handleResponse<TrendResponse>);
export const listBenchCases = (
  runId: string,
  q: { outcome: "failed" | "passed" | "all"; category?: string; type?: string; q?: string; offset: number; limit: number },
): Promise<CasePage> => {
  const p = new URLSearchParams({ outcome: q.outcome, offset: String(q.offset), limit: String(q.limit) });
  if (q.category) p.set("category", q.category);
  if (q.type) p.set("type", q.type);
  if (q.q) p.set("q", q.q);
  return localFetch(`/api/benchmark/runs/${id(runId)}/cases?${p}`).then(handleResponse<CasePage>);
};
export const getBenchCase = (runId: string, caseId: string): Promise<CaseDetail> =>
  localFetch(`/api/benchmark/runs/${id(runId)}/case?case_id=${id(caseId)}`).then(handleResponse<CaseDetail>);
export interface ImportSource {
  source: string;
  official_status: string | null;
  source_commit: string;
  cases: number | null;
}
export const listImportable = (): Promise<{ sources: ImportSource[] }> =>
  localFetch("/api/benchmark/imports").then(handleResponse<{ sources: ImportSource[] }>);
export const importBenchmark = (source: string): Promise<{ run_id: string; already_imported: boolean }> =>
  localFetch("/api/benchmark/imports", { method: "POST", headers: json, body: JSON.stringify({ source }) }).then(handleResponse<{ run_id: string; already_imported: boolean }>);

/** Fetch through the local API and hand the page an object URL for a sandboxed iframe. */
export async function fetchBenchReportUrl(runId: string): Promise<string> {
  const response = await localFetch(`/api/benchmark/runs/${id(runId)}/report`);
  if (!response.ok) throw new ApiError("REPORT_UNAVAILABLE", "No report is available for this run.");
  return URL.createObjectURL(await response.blob());
}
