"use client";

import { useCallback, useEffect, useState } from "react";
import { getBenchCase, listBenchCases, type CaseDetail, type CasePage } from "@/lib/benchmark";
import { failureReasonLabel } from "@/lib/benchmark-utils";
import { btn, card, Empty, ErrorBanner, errorText, input } from "./ui";

const PAGE = 25;

interface Props {
  runId: string | null;
  categories: string[];
  initialCategory?: string;
}

export default function FailureInspector({ runId, categories, initialCategory }: Props) {
  const [outcome, setOutcome] = useState<"failed" | "passed" | "all">("failed");
  const [category, setCategory] = useState(initialCategory ?? "");
  const [ctype, setCtype] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<CasePage | null>(null);
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [seen, setSeen] = useState(initialCategory);

  // Follow the category passed in from the results table, without an effect-driven state cascade.
  if (initialCategory !== seen) {
    setSeen(initialCategory);
    setCategory(initialCategory ?? "");
    setOffset(0);
  }

  const load = useCallback(async () => {
    if (!runId) return;
    setLoading(true);
    try {
      setPage(await listBenchCases(runId, { outcome, category: category || undefined, type: ctype || undefined, q: q || undefined, offset, limit: PAGE }));
      setError(null);
    } catch (e) {
      setError(errorText(e, "Could not load the cases."));
    } finally {
      setLoading(false);
    }
  }, [runId, outcome, category, ctype, q, offset]);

  useEffect(() => {
    const t = setTimeout(load, q ? 300 : 0); // debounce typing in the search box
    return () => clearTimeout(t);
  }, [load, q]);

  async function open(caseId: string) {
    if (!runId) return;
    try {
      setDetail(await getBenchCase(runId, caseId));
      setError(null);
    } catch (e) {
      setError(errorText(e, "Could not load that case."));
    }
  }

  if (!runId) return <Empty>Pick a run first, then inspect its failed cases here.</Empty>;
  const pages = page ? Math.max(1, Math.ceil(page.total / PAGE)) : 1;

  return (
    <section className={`${card} flex flex-col gap-3`} aria-labelledby="bench-cases">
      <h3 id="bench-cases" className="font-medium text-gray-900">Failure inspector</h3>
      <div className="flex flex-wrap gap-2">
        <select aria-label="Outcome" value={outcome} onChange={(e) => { setOutcome(e.target.value as typeof outcome); setOffset(0); }} className={input}>
          <option value="failed">Failed cases</option><option value="passed">Passed cases</option><option value="all">All cases</option>
        </select>
        <select aria-label="Category" value={category} onChange={(e) => { setCategory(e.target.value); setOffset(0); }} className={input}>
          <option value="">All categories</option>{categories.map((c) => (<option key={c} value={c}>{c}</option>))}
        </select>
        <select aria-label="Test type" value={ctype} onChange={(e) => { setCtype(e.target.value); setOffset(0); }} className={input}>
          <option value="">All test types</option>{["present", "absent", "order", "table", "math"].map((t) => (<option key={t} value={t}>{t}</option>))}
        </select>
        <input type="search" aria-label="Search PDF or case id" placeholder="Search PDF or case id" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} className={`${input} min-w-[12rem] flex-1`} />
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {page && (
        <p className="text-xs text-gray-500">
          {page.total.toLocaleString()} matching case(s) · {page.run_totals.failed.toLocaleString()} failed and {page.run_totals.passed.toLocaleString()} passed in this run
        </p>
      )}
      {page && page.items.length === 0 && !loading && <Empty>No cases match these filters.</Empty>}
      <ul className="flex flex-col gap-1.5" aria-busy={loading}>
        {page?.items.map((it) => (
          <li key={it.id}>
            <button type="button" onClick={() => open(it.id)} aria-pressed={detail?.id === it.id}
              className={`w-full rounded-lg border px-3 py-2 text-left text-sm hover:bg-gray-50 ${detail?.id === it.id ? "border-blue-600" : "border-gray-200"}`}>
              <span className="flex flex-wrap items-center gap-2">
                <span className={`h-2 w-2 shrink-0 rounded-full ${it.passed ? "bg-green-600" : "bg-red-600"}`} aria-label={it.passed ? "passed" : "failed"} />
                <span className="min-w-0 break-all font-medium">{it.pdf}</span>
                <span className="text-xs text-gray-500">p.{it.page} · {it.type}{it.category ? ` · ${it.category}` : ""}</span>
              </span>
              <span className="mt-0.5 block text-xs text-gray-600">{it.expected}</span>
            </button>
          </li>
        ))}
      </ul>
      {page && page.total > PAGE && (
        <div className="flex items-center justify-between text-sm">
          <button type="button" className={btn} disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
          <span className="text-gray-500">Page {Math.floor(offset / PAGE) + 1} of {pages}</span>
          <button type="button" className={btn} disabled={offset + PAGE >= page.total || loading} onClick={() => setOffset(offset + PAGE)}>Next</button>
        </div>
      )}

      {detail && (
        <div className="flex flex-col gap-2 rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm" role="region" aria-label="Case detail">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="break-all">{detail.pdf}</strong>
            <span className="text-xs text-gray-500">page {detail.page} · {detail.type}{detail.category ? ` · ${detail.category}` : ""}</span>
            <button type="button" className="ml-auto text-xs underline" onClick={() => setDetail(null)}>Close</button>
          </div>
          {!detail.passed && detail.failure_reason && <p className="font-medium text-red-700">{failureReasonLabel[detail.failure_reason] ?? detail.failure_reason}</p>}
          <p><span className="text-xs uppercase text-gray-500">Expected</span><br />{detail.expected}</p>
          {detail.scorer_feedback && <p><span className="text-xs uppercase text-gray-500">Scorer feedback</span><br />{detail.scorer_feedback}</p>}
          {detail.document && (
            <p className="text-xs text-gray-600">
              Document: {detail.document.status}{detail.document.timed_out ? " (timed out)" : ""}{detail.document.empty ? " (empty output)" : ""}
              {detail.document.per_page_ms ? ` · ${detail.document.per_page_ms} ms per page` : ""}{detail.document.error ? ` · ${detail.document.error}` : ""}
            </p>
          )}
          <div>
            <span className="text-xs uppercase text-gray-500">Actual extraction</span>
            {detail.actual_output.available ? (
              <>
                <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded bg-white p-2 text-xs">{detail.actual_output.text || "(empty)"}</pre>
                {detail.actual_output.truncated && <p className="text-xs text-gray-500">Showing the first part of a {Math.round((detail.actual_output.size_bytes ?? 0) / 1024)} KB output.</p>}
              </>
            ) : (
              <p className="text-xs text-gray-600">{detail.actual_output.reason ?? "Not available."}</p>
            )}
          </div>
          {detail.logs.length > 0 && (
            <details><summary className="cursor-pointer text-xs">Extraction log ({detail.logs.length})</summary>
              <pre className="mt-1 max-h-40 overflow-auto rounded bg-gray-900 p-2 text-xs text-gray-100">{detail.logs.join("\n")}</pre></details>
          )}
        </div>
      )}
    </section>
  );
}
