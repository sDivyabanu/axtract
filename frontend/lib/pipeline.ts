// Live processing pipeline state. Pure functions only: the backend decides every transition,
// this module just records what it was told. Nothing here estimates progress or fills in a stage.

export type StageState = "pending" | "running" | "completed" | "warning" | "failed" | "skipped";

export interface StageInfo {
  id: string;
  label: string;
  description: string;
}

export interface StageDetail {
  findings?: number;
  issues?: number;
  blocks?: number;
  pages?: number;
  errors?: number;
  by_severity?: Record<string, number>;
  codes?: Record<string, number>;
  reason?: string;
  error_type?: string;
  status?: string;
}

export interface StageEvent {
  id: string;
  state: StageState;
  seq: number;
  elapsed_ms: number;
  detail?: StageDetail;
}

export interface StageProgress {
  state: StageState;
  detail: StageDetail | null;
  elapsedMs: number | null;
}

export interface PipelineState {
  stages: StageInfo[];
  progress: Record<string, StageProgress>;
  lastSeq: number;
  /** "streaming" while events may still arrive; "done" after the result; "error" after an error or lost connection. */
  phase: "streaming" | "done" | "error";
  verifyEnabled: boolean;
}

// Labels shown before the server's own `start` event arrives (the server's list replaces them).
export const DEFAULT_STAGES: StageInfo[] = [
  { id: "security", label: "Security scanning", description: "Checks the file for hidden content, active links and unsafe markup." },
  { id: "extraction", label: "Document extraction", description: "Reads text, tables, figures and charts into structured blocks." },
  { id: "inventory", label: "Source inventory", description: "Independently counts what the original file contains." },
  { id: "checks", label: "Integrity & completeness", description: "Confirms nothing in the source went missing." },
  { id: "content", label: "Content correctness", description: "Compares extracted words, numbers and cells with the source." },
  { id: "structure", label: "Structure & reading order", description: "Checks tables, headings and reading order." },
  { id: "verdict", label: "Validation verdict", description: "Combines the evidence into a verdict and report." },
];

export function initialPipeline(stages: StageInfo[] = DEFAULT_STAGES, verifyEnabled = true): PipelineState {
  const progress: Record<string, StageProgress> = {};
  for (const s of stages) progress[s.id] = { state: "pending", detail: null, elapsedMs: null };
  return { stages, progress, lastSeq: 0, phase: "streaming", verifyEnabled };
}

export function applyStart(state: PipelineState, stages: StageInfo[], verifyEnabled: boolean): PipelineState {
  return { ...initialPipeline(stages, verifyEnabled), phase: state.phase };
}

/** Apply one server event. Out-of-order or unknown events are ignored; the server's `seq` is the order. */
export function applyStage(state: PipelineState, ev: StageEvent): PipelineState {
  if (ev.seq <= state.lastSeq || !(ev.id in state.progress)) return state;
  return {
    ...state,
    lastSeq: ev.seq,
    progress: { ...state.progress, [ev.id]: { state: ev.state, detail: ev.detail ?? null, elapsedMs: ev.elapsed_ms } },
  };
}

/** The stream ended. A stage left `running` did not finish: show it failed, never completed. */
export function finish(state: PipelineState, outcome: "done" | "error"): PipelineState {
  if (outcome === "done") return { ...state, phase: "done" };
  const progress = { ...state.progress };
  for (const [id, p] of Object.entries(progress)) {
    if (p.state === "running") progress[id] = { ...p, state: "failed" };
  }
  return { ...state, progress, phase: "error" };
}

export const isTerminal = (s: StageState) =>
  s === "completed" || s === "warning" || s === "failed" || s === "skipped";

export function currentStageId(state: PipelineState): string | null {
  const running = state.stages.find((s) => state.progress[s.id]?.state === "running");
  return running ? running.id : null;
}

/** How many stages have a final outcome: a real count of reported events, not an estimate. */
export function settledCount(state: PipelineState): number {
  return state.stages.filter((s) => isTerminal(state.progress[s.id]?.state ?? "pending")).length;
}

const SKIP_REASON: Record<string, string> = {
  disabled: "Validation is turned off on this server.",
  time_budget: "Skipped: the validation time budget was used up.",
  upstream_failed: "Skipped: an earlier check could not run.",
  not_run: "Not run.",
  cancelled: "Cancelled.",
  unavailable: "Validation was unavailable.",
};

/** One short, plain sentence for a stage's outcome, built only from the counts the server sent. */
export function describeOutcome(id: string, p: StageProgress): string {
  const d = p.detail ?? {};
  if (p.state === "pending") return "Waiting";
  if (p.state === "running") return "Running…";
  if (p.state === "skipped") return SKIP_REASON[d.reason ?? ""] ?? "Skipped.";
  if (p.state === "failed") return d.error_type ? `Could not complete (${d.error_type}).` : "Could not complete.";
  const parts: string[] = [];
  if (id === "security") {
    parts.push(d.findings ? `${d.findings} security finding${d.findings === 1 ? "" : "s"}` : "No security findings");
  } else if (id === "extraction") {
    if (d.blocks != null) parts.push(`${d.blocks} block${d.blocks === 1 ? "" : "s"}`);
    if (d.pages != null) parts.push(`${d.pages} page${d.pages === 1 ? "" : "s"}`);
    if (d.errors) parts.push(`${d.errors} extraction warning${d.errors === 1 ? "" : "s"}`);
  } else if (id === "verdict") {
    if (d.status) parts.push(d.status.replace(/_/g, " "));
  } else if (d.issues != null) {
    parts.push(d.issues ? `${d.issues} issue${d.issues === 1 ? "" : "s"} found` : "No issues");
  }
  if (p.state === "warning" && d.reason) parts.push(SKIP_REASON[d.reason] ?? "");
  return parts.filter(Boolean).join(" · ") || (p.state === "warning" ? "Completed with warnings." : "Completed.");
}
