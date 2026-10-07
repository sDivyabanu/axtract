// DealLens API client + types. Mirrors backend/rag (additive; does not touch the parser types).
import { API_BASE_URL } from "./api";
import type { BBox } from "./types";

export interface Workspace {
  workspace_id: string;
  name: string;
  created_at: number;
  documents?: RagDoc[] | number;
}

export interface RagDoc {
  doc_id: string;
  workspace_id: string;
  filename: string;
  doc_type: string;
  file_type: string;
  page_count: number;
  status: "queued" | "parsing" | "indexing" | "ready" | "failed";
  stage: string;
  progress: number;
  error: string | null;
  parse_status: string | null;
  flags: string[];
  flag_count: number;
  block_count: number;
  chunk_count: number;
  quarantined_count: number;
  preview_available: boolean;
  preview_pages: number;
  parse_ms: number;
}

export interface Loc {
  page: number;
  bbox: BBox | null;
  block_id?: string;
}

export interface Badge {
  label: string;
  level: "amber" | "red" | "green";
}

export interface Citation {
  n: number;
  chunk_id: string;
  doc_id: string;
  filename: string;
  preview_pages: number;
  kind: string;
  pages: number[];
  printed_pages: string[];
  heading_path: string[];
  bboxes: Loc[];
  snippet: string;
  min_confidence: number | null;
  flags: string[];
  badges: Badge[];
}

export interface Claim {
  kind: string;
  text: string;
  supported: boolean;
}

export interface Sentence {
  text: string;
  citations: number[];
  verified: boolean | null;
  reason?: string;
  claims?: Claim[];
}

export interface Operand {
  label: string;
  value: number | null;
  display: string;
  doc_id: string;
  filename: string;
  page: number;
  printed_page?: string | null;
  bbox: BBox | null;
  confidence: number | null;
  exact_cell: boolean;
  period?: string | null;
}

export interface Receipt {
  id: string;
  op: string;
  title: string;
  result_display: string;
  result: number | null;
  unit: string;
  period?: string | null;
  formula: string;
  operands: Operand[];
  min_confidence: number | null;
  warnings: string[];
}

export interface Stage {
  name: string;
  ms: number;
  [k: string]: unknown;
}

export interface GlassHit {
  chunk_id: string;
  filename: string;
  kind: string;
  pages: number[];
  bm25: number;
  bm25_rank: number | null;
  dense: number;
  dense_rank: number | null;
  rrf: number;
  rerank: number | null;
  used: boolean;
}

export interface Answer {
  answer_id: string;
  question: string;
  text: string;
  sentences: Sentence[];
  citations: Citation[];
  receipts: Receipt[];
  grounding: { verified: number; total: number } | null;
  badges: Badge[];
  abstained: boolean;
  abstain_reason: string | null;
  searched: { documents: string[]; chunks_searched: number; closest_sections: { filename: string; pages: number[] }[]; note?: string } | null;
  route: string;
  mode: "llm" | "extractive" | "computed";
  refusal?: string;
  model: string | null;
  stages: Stage[];
  total_ms?: number;
  pipeline?: string;
  excluded_sources?: { reason: string; page: number | null; filename: string }[];
  glass_box: {
    route: string;
    llm: { available: boolean; reason: string; model: string };
    workspace_chunks: number;
    retrieved: GlassHit[];
    thresholds: Record<string, number | null>;
    plan?: unknown;
  };
}

async function j<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const msg = body?.error?.message ?? body?.detail ?? `HTTP ${res.status}`;
    throw new Error(String(msg));
  }
  return body as T;
}

export const rag = {
  status: () => fetch(`${API_BASE_URL}/api/rag/status`).then(j<{ mode: string; llm: { available: boolean; model: string; reason: string } }>),
  listWorkspaces: () => fetch(`${API_BASE_URL}/api/workspaces`).then(j<Workspace[]>),
  createWorkspace: (name: string) =>
    fetch(`${API_BASE_URL}/api/workspaces`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }).then(j<Workspace>),
  getWorkspace: (id: string) => fetch(`${API_BASE_URL}/api/workspaces/${id}`).then(j<Workspace & { documents: RagDoc[] }>),
  upload: (id: string, files: File[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    return fetch(`${API_BASE_URL}/api/workspaces/${id}/documents`, { method: "POST", body: fd }).then(
      j<{ documents: RagDoc[]; rejected: { filename: string; code: string; message: string }[] }>,
    );
  },
  get: <T,>(path: string) => fetch(`${API_BASE_URL}/api${path}`).then(j<T>),
  post: <T,>(path: string, body?: unknown) =>
    fetch(`${API_BASE_URL}/api${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(j<T>),
};

export interface AskBody {
  question: string;
  doc_ids?: string[] | null;
  doc_types?: string[] | null;
  periods?: string[] | null;
  mode?: "dealLens" | "baseline";
}

export type AskEvent =
  | { event: "stage"; name: string; status: "start" | "done"; ms?: number; [k: string]: unknown }
  | { event: "token"; text: string }
  | { event: "answer"; answer: Answer }
  | { event: "error"; message: string; code?: string };

/** POST /ask and read the server-sent events. Resolves with the final answer. */
export async function askStream(
  workspaceId: string,
  body: AskBody,
  onEvent: (e: AskEvent) => void,
  signal?: AbortSignal,
): Promise<Answer | null> {
  const res = await fetch(`${API_BASE_URL}/api/workspaces/${workspaceId}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`Request failed (HTTP ${res.status})`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let final: Answer | null = null;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const raw = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const dataLine = raw.split("\n").find((l) => l.startsWith("data:"));
      if (!dataLine) continue;
      try {
        const ev = JSON.parse(dataLine.slice(5).trim()) as AskEvent;
        if (ev.event === "answer") final = ev.answer;
        onEvent(ev);
      } catch {
        /* ignore malformed event */
      }
    }
  }
  return final;
}

export interface BaselineAnswer {
  text: string;
  mode: string;
  model: string | null;
  total_ms: number;
  stages: Stage[];
  sources: { filename: string; page: number; text: string; dense: number }[];
  checks: {
    grounding: { verified: number; total: number };
    unsupported: string[];
    retrieved_instructions: { filename: string; page: number; reason: string; snippet: string }[];
  } | null;
}

export interface QuarantineItem {
  id: string;
  doc_id: string;
  filename: string;
  block_id: string | null;
  reason: string;
  reason_label: string;
  kind: string | null;
  page: number | null;
  bbox: BBox | null;
  snippet: string;
  preview_pages: number;
}

export interface Side {
  doc_id: string;
  filename: string;
  label: string;
  page: number;
  printed_page: string | null;
  bbox: BBox | null;
  preview_pages: number;
  display: string;
  value: number;
  agrees_with_lowest: boolean;
}

export interface Contradiction {
  id: string;
  concept_label: string;
  period: string;
  sides: Side[];
  primary: { high: Side; low: Side };
  gap_pct: number;
  severity: "high" | "medium" | "low";
  note: string;
  summary: string;
}

export interface Evidence {
  doc_id: string;
  filename: string;
  page: number | null;
  bbox: BBox | null;
  label: string;
  preview_pages: number;
}

export interface SellerItem {
  id: string;
  n: number;
  kind: string;
  severity: "high" | "medium" | "low";
  question: string;
  why: string;
  evidence: Evidence[];
}

export interface PackCell {
  doc_id: string;
  status: "found" | "not_found";
  text: string;
  citations?: { doc_id: string; filename: string; page: number | null; printed_page: string | null; bbox: BBox | null; preview_pages: number }[];
  badges?: Badge[];
}

export interface PackRun {
  pack: string;
  title: string;
  columns: { doc_id: string; filename: string; doc_type: string; preview_pages: number }[];
  rows: { label: string; question: string | null; cells: PackCell[] }[];
  seconds: number;
}

export interface MaturityBar {
  year: number;
  value: number;
  display: string;
  receipt: Receipt;
}

export interface Wall {
  title: string;
  filename: string;
  doc_id: string;
  unit: string;
  bars: MaturityBar[];
}

export interface TimelineEvent {
  date: string;
  label: string;
  kind: string;
  doc_id: string;
  filename: string;
  preview_pages: number;
  page: number | null;
  bbox: BBox | null;
  count?: number;
}

export const SEVERITY_STYLE: Record<string, string> = {
  high: "bg-red-100 text-red-800",
  medium: "bg-amber-100 text-amber-800",
  low: "bg-gray-100 text-gray-700",
};
