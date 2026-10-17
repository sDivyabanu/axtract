// Pure helpers for the Benchmark dashboard (kept free of React so they can be unit tested).
import type { BenchStatus, ScoreKind } from "./benchmark";

/** Category selection: an empty set means "all categories". Selecting every category normalises back to all. */
export interface Selection {
  all: boolean;
  chosen: string[];
}

export const ALL_SELECTION: Selection = { all: true, chosen: [] };

export function toggleCategory(sel: Selection, name: string, available: string[]): Selection {
  const base = sel.all ? [] : sel.chosen;
  const chosen = base.includes(name) ? base.filter((c) => c !== name) : [...base, name];
  if (chosen.length === 0 || chosen.length === available.length) return ALL_SELECTION;
  return { all: false, chosen: available.filter((c) => chosen.includes(c)) };
}

export function selectOnly(names: string[], available: string[]): Selection {
  const chosen = available.filter((c) => names.includes(c));
  return chosen.length === 0 || chosen.length === available.length ? ALL_SELECTION : { all: false, chosen };
}

export const selectedNames = (sel: Selection, available: string[]): string[] => (sel.all ? available : sel.chosen);

export function estimatePdfs(sel: Selection, categories: { name: string; pdfs: number }[], perCategory: number): number {
  const names = new Set(selectedNames(sel, categories.map((c) => c.name)));
  return categories.filter((c) => names.has(c.name)).reduce((n, c) => n + Math.min(perCategory, c.pdfs), 0);
}

export const isActive = (s: BenchStatus | undefined): boolean => s === "created" || s === "running";

export interface Badge {
  label: string;
  tone: "amber" | "green" | "red" | "gray" | "blue";
  detail: string;
}

/** Quick and incomplete results must never look like an official score. */
export function scoreBadge(kind: ScoreKind): Badge {
  switch (kind) {
    case "quick_sample":
      return { label: "Quick sample", tone: "amber", detail: "A small sample. Not a full official score." };
    case "full_official":
      return { label: "Full official", tone: "green", detail: "Complete run scored by the official scorer." };
    case "full_pending":
      return { label: "Full run", tone: "blue", detail: "A full benchmark that has not finished scoring." };
    case "full_incomplete":
      return { label: "Incomplete full run", tone: "red", detail: "Some outputs are missing. Diagnostic only, not an official score." };
    case "imported_invalid":
      return { label: "Imported, official FAILED", tone: "red", detail: "The official scorer rejected this run. Percentages are diagnostic only." };
    case "imported_official":
      return { label: "Imported official", tone: "green", detail: "Imported from a valid official run." };
  }
}

/** The headline number and how to describe it. Diagnostic numbers are never shown as an accuracy. */
export function headline(overall: number | null, diagnostic: number | null): { value: string; caption: string } {
  if (overall !== null) return { value: `${overall.toFixed(1)}%`, caption: "score" };
  if (diagnostic !== null) return { value: `${diagnostic.toFixed(1)}%`, caption: "diagnostic only, not a valid score" };
  return { value: "n/a", caption: "no score" };
}

export const shortId = (runId: string): string => runId.replace(/^(qk|fl|im)-/, "").replace(/T(\d{2})(\d{2})(\d{2})Z-/, " $1:$2:$3 ").slice(0, 24);

export function fmtDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : m ? `${m}m ${s % 60}s` : `${s}s`;
}

export const fmtPct = (v: number | null | undefined, digits = 1): string => (v === null || v === undefined ? "n/a" : `${v.toFixed(digits)}%`);
export const fmtDelta = (v: number | null | undefined): string => (v === null || v === undefined ? "n/a" : `${v > 0 ? "+" : ""}${v}`);

export function processed(counts: { done: number; failed: number; partial?: number } | null): number {
  return counts ? counts.done + counts.failed + (counts.partial ?? 0) : 0;
}

export function progressPercent(counts: { total: number; done: number; failed: number; partial?: number } | null): number {
  return counts && counts.total ? Math.min(100, (processed(counts) / counts.total) * 100) : 0;
}

/** Which run the dashboard should show after a refresh: the active one, else the newest. */
export function pickRunToRestore(active: string | null, history: { run_id: string }[]): string | null {
  return active ?? history[0]?.run_id ?? null;
}

export interface SeriesPoint {
  x: number;
  y: number;
  runId: string;
}

/** Map trend values to SVG coordinates. Missing values leave gaps; a single point is centred. */
export function buildSeries(values: (number | null)[], runIds: string[], width: number, height: number, pad = 6): SeriesPoint[][] {
  const segments: SeriesPoint[][] = [];
  let current: SeriesPoint[] = [];
  const n = values.length;
  values.forEach((v, i) => {
    if (v === null) {
      if (current.length) segments.push(current);
      current = [];
      return;
    }
    const x = n === 1 ? width / 2 : pad + (i * (width - 2 * pad)) / (n - 1);
    const y = pad + (height - 2 * pad) * (1 - Math.max(0, Math.min(100, v)) / 100);
    current.push({ x, y, runId: runIds[i] });
  });
  if (current.length) segments.push(current);
  return segments;
}

export const failureReasonLabel: Record<string, string> = {
  timeout: "Extraction timed out",
  extraction_error: "Extraction failed",
  no_output: "No output produced",
  empty_output: "Output was empty",
  content_mismatch: "Output did not match the expected content",
};

/** Turn a backend error code into something a person can act on. */
export function friendlyError(code: string | undefined, fallback: string): string {
  const map: Record<string, string> = {
    OFFICIAL_BENCHMARK_ACTIVE: "The official benchmark is running. Start a benchmark after it finishes.",
    RUN_CONFLICT: "Another benchmark is already running. Wait for it to finish or cancel it.",
    DATASET_UNAVAILABLE: "The benchmark dataset is not available on this server.",
    LOW_MEMORY: "There is not enough free memory to start a benchmark right now.",
    FULL_NOT_CONFIRMED: "Confirm the full benchmark before starting it.",
    NOT_RESUMABLE: "This run cannot be resumed.",
    NOT_RUNNING: "This run is not running on this server.",
    HTTP_403: "Local benchmark access was rejected. Check the loopback launcher settings.",
    AUTH_REQUIRED: "Start both local servers with their loopback launchers.",
  };
  return (code && map[code]) || fallback;
}
