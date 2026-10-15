import { ApiError } from "./api";
import { authFetch, handleResponse } from "./api-authenticated";

export interface BenchCategory {
  name: string;
  pdfs: number;
  tests: number;
}

export interface BenchCategoriesResponse {
  modes: string[];
  defaults: { mode: string; per_category: number; seed: number; workers: number; max_per_category: number; max_workers: number };
  categories: BenchCategory[];
}

export interface BenchCounts {
  total: number;
  done: number;
  failed: number;
  pending: number;
}

export type BenchStatus = "created" | "running" | "completed" | "failed" | "cancelled" | "interrupted";

export interface BenchRunStatus {
  run_id: string;
  mode: string;
  score_kind: string;
  is_official: boolean;
  score_label: string;
  created_at: string;
  config: { categories: string[]; per_category: number; seed: number; workers: number };
  git: { sha: string | null; branch: string | null; dirty: boolean | null };
  totals: { documents: number; test_cases: number };
  status: BenchStatus;
  phase: string | null;
  counts: BenchCounts | null;
  elapsed_s: number;
  error: string | null;
  resumable: boolean;
  failures: { pdf: string; category: string; error: string | null }[];
}

export interface BenchHistoryItem {
  run_id: string;
  mode: string;
  is_official: boolean;
  status: BenchStatus;
  created_at: string;
  counts: BenchCounts | null;
  elapsed_s: number;
  config: BenchRunStatus["config"];
  git: BenchRunStatus["git"];
  overall_score_pct: number | null;
}

export interface BenchCategoryScore {
  pass_rate_pct: number;
  passed: number;
  total: number;
}

export interface BenchResults {
  run_id: string;
  is_official: boolean;
  score_label: string;
  scoring_error: string | null;
  overall_score_pct: number | null;
  confidence_interval: string | null;
  categories: Record<string, BenchCategoryScore>;
  documents: { total: number; succeeded: number; failed: number };
  timing: { per_page_ms: { mean: number | null; median: number | null; max: number | null }; total_extraction_ms: number };
  failed_tests: { id: string; pdf: string; page: number; type: string; category: string | null }[];
  failed_test_count: number;
  has_report: boolean;
}

export interface BenchComparison {
  a: string;
  b: string;
  same_documents: boolean;
  overall_a_pct: number | null;
  overall_b_pct: number | null;
  overall_delta_pct: number | null;
  categories: { category: string; a_pct: number | null; b_pct: number | null; delta_pct: number | null }[];
  note: string | null;
}

const json = { "Content-Type": "application/json" };

export async function getBenchCategories(): Promise<BenchCategoriesResponse> {
  return handleResponse(await authFetch("/api/benchmark/categories"));
}

export async function startBenchmark(body: { categories: string[]; per_category: number }): Promise<{ run_id: string }> {
  return handleResponse(await authFetch("/api/benchmark/runs", { method: "POST", headers: json, body: JSON.stringify({ mode: "quick", ...body }) }));
}

export async function listBenchRuns(): Promise<{ runs: BenchHistoryItem[] }> {
  return handleResponse(await authFetch("/api/benchmark/runs"));
}

export async function getBenchRun(id: string): Promise<BenchRunStatus> {
  return handleResponse(await authFetch(`/api/benchmark/runs/${encodeURIComponent(id)}`));
}

export async function getBenchResults(id: string): Promise<BenchResults> {
  return handleResponse(await authFetch(`/api/benchmark/runs/${encodeURIComponent(id)}/results`));
}

export async function getBenchLog(id: string): Promise<{ log: string }> {
  return handleResponse(await authFetch(`/api/benchmark/runs/${encodeURIComponent(id)}/log`));
}

export async function resumeBenchmark(id: string): Promise<void> {
  await handleResponse(await authFetch(`/api/benchmark/runs/${encodeURIComponent(id)}/resume`, { method: "POST" }));
}

export async function cancelBenchmark(id: string): Promise<void> {
  await handleResponse(await authFetch(`/api/benchmark/runs/${encodeURIComponent(id)}/cancel`, { method: "POST" }));
}

export async function compareBenchRuns(a: string, b: string): Promise<BenchComparison> {
  return handleResponse(await authFetch(`/api/benchmark/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`));
}

/** The HTML report needs the auth header, so fetch it and hand the page an object URL for a sandboxed iframe. */
export async function fetchBenchReportUrl(id: string): Promise<string> {
  const response = await authFetch(`/api/benchmark/runs/${encodeURIComponent(id)}/report`);
  if (!response.ok) throw new ApiError("REPORT_UNAVAILABLE", "No report is available for this run.");
  return URL.createObjectURL(await response.blob());
}
