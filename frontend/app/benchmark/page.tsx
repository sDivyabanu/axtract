"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { FlaskConical, Play, RotateCcw, Square } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import {
  cancelBenchmark,
  compareBenchRuns,
  fetchBenchReportUrl,
  getBenchCategories,
  getBenchLog,
  getBenchResults,
  getBenchRun,
  listBenchRuns,
  resumeBenchmark,
  startBenchmark,
  type BenchCategoriesResponse,
  type BenchComparison,
  type BenchHistoryItem,
  type BenchResults,
  type BenchRunStatus,
} from "@/lib/benchmark";

const ACTIVE = new Set(["created", "running"]);

function fmtTime(s: number): string {
  const m = Math.floor(s / 60);
  return m ? `${m}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`;
}
const pct = (v: number | null | undefined) => (v == null ? "n/a" : `${v.toFixed(1)}%`);
const shortId = (id: string) => id.replace(/^qk-/, "").slice(0, 24);

/** Quick/sample scores must never read like the full official score. */
function SampleBanner({ official }: { official: boolean }) {
  if (official) return null;
  return (
    <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900" role="note">
      <strong>Quick sample score, not a full official score.</strong> It covers a small sample of documents, so it is
      noisy and not comparable to the full olmOCR-bench result.
    </p>
  );
}

export default function BenchmarkPage() {
  const { user, loading: authLoading } = useAuth();
  const [meta, setMeta] = useState<BenchCategoriesResponse | null>(null);
  const [selected, setSelected] = useState<string[]>([]); // empty = all categories
  const [perCategory, setPerCategory] = useState(5);
  const [history, setHistory] = useState<BenchHistoryItem[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [run, setRun] = useState<BenchRunStatus | null>(null);
  const [results, setResults] = useState<BenchResults | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string | null>(null);
  const [reportUrl, setReportUrl] = useState<string | null>(null);
  const [cmpA, setCmpA] = useState("");
  const [cmpB, setCmpB] = useState("");
  const [cmp, setCmp] = useState<BenchComparison | null>(null);

  const refreshHistory = useCallback(async () => {
    try {
      setHistory((await listBenchRuns()).runs);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load benchmark history.");
    }
  }, []);

  useEffect(() => {
    if (authLoading || !user) return;
    getBenchCategories().then(setMeta).catch((e) => setError(e instanceof Error ? e.message : "Benchmark is unavailable."));
    listBenchRuns().then((r) => setHistory(r.runs)).catch(() => {});
  }, [authLoading, user]);

  // Poll the selected run while it is active; load results once it settles.
  const status = run?.status;
  useEffect(() => {
    if (!activeId) return;
    let stop = false;
    const tick = async () => {
      try {
        const r = await getBenchRun(activeId);
        if (stop) return;
        setRun(r);
        if (r.status === "completed") {
          setResults(await getBenchResults(activeId));
        }
        if (!ACTIVE.has(r.status)) refreshHistory();
      } catch (e) {
        if (!stop) setError(e instanceof Error ? e.message : "Lost contact with the benchmark.");
      }
    };
    tick();
    const timer = status && !ACTIVE.has(status) ? null : setInterval(tick, 2000);
    return () => {
      stop = true;
      if (timer) clearInterval(timer);
    };
  }, [activeId, status, refreshHistory]);

  useEffect(() => () => { if (reportUrl) URL.revokeObjectURL(reportUrl); }, [reportUrl]);

  function open(id: string) {
    setActiveId(id);
    setRun(null);
    setResults(null);
    setLog(null);
    setReportUrl(null);
    setError(null);
  }

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed.");
    } finally {
      setBusy(false);
    }
  }

  const start = () =>
    act(async () => {
      const { run_id } = await startBenchmark({ categories: selected.length ? selected : ["all"], per_category: perCategory });
      open(run_id);
    });
  const resume = () => act(async () => { await resumeBenchmark(run!.run_id); setRun({ ...run!, status: "running" }); });
  const cancel = () => act(() => cancelBenchmark(run!.run_id));
  const showReport = () => act(async () => setReportUrl(await fetchBenchReportUrl(run!.run_id)));
  const showLog = () => act(async () => setLog((await getBenchLog(run!.run_id)).log || "(empty)"));
  const compare = () => act(async () => setCmp(await compareBenchRuns(cmpA, cmpB)));

  const completed = useMemo(() => history.filter((h) => h.status === "completed"), [history]);
  const shell = "mx-auto w-full max-w-6xl px-4 py-6 sm:px-6";
  const card = "rounded-xl border border-gray-200 bg-white p-4 shadow-card sm:p-5";
  const total = meta?.categories.length ?? 0;
  const isAll = selected.length === 0 || selected.length === total;
  const estimate = isAll ? total * perCategory : selected.length * perCategory;

  if (authLoading) return <main className={shell} aria-busy="true"><div className="ax-skeleton h-10 w-64" /></main>;
  if (!user) {
    return (
      <main className={shell}>
        <div className={`${card} text-center text-sm text-gray-600`}>
          Please <Link href="/login" className="font-medium text-blue-700 underline">sign in</Link> to use the benchmark runner.
        </div>
      </main>
    );
  }

  return (
    <main className={`${shell} flex flex-col gap-5`}>
      <div>
        <h2 className="font-display text-2xl font-semibold text-gray-900">Benchmark</h2>
        <p className="text-sm text-gray-500">Run a small, repeatable sample of the official olmOCR benchmark.</p>
      </div>

      {error && (
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      <section className={card} aria-labelledby="bench-new">
        <h3 id="bench-new" className="mb-3 flex items-center gap-2 font-medium text-gray-900"><FlaskConical size={18} /> New run</h3>
        <fieldset className="mb-4">
          <legend className="mb-1.5 text-xs font-medium uppercase tracking-wide text-gray-500">Mode</legend>
          <label className="inline-flex items-center gap-2 rounded-full bg-blue-50 px-4 py-2 text-sm font-medium text-blue-800">
            <input type="radio" checked readOnly name="mode" /> Quick (sample)
          </label>
          <p className="mt-1 text-xs text-gray-500">Full official scores come from the complete official benchmark, not from this page.</p>
        </fieldset>

        <fieldset className="mb-4">
          <legend className="mb-1.5 text-xs font-medium uppercase tracking-wide text-gray-500">Categories</legend>
          <div className="flex flex-wrap gap-2">
            <button type="button" aria-pressed={isAll} onClick={() => setSelected([])}
              className={`rounded-full border px-3.5 py-1.5 text-sm ${isAll ? "border-blue-600 bg-blue-50 text-blue-800" : "border-gray-300 text-gray-700 hover:bg-gray-50"}`}>
              All categories
            </button>
            {meta?.categories.map((c) => {
              const on = !isAll && selected.includes(c.name);
              return (
                <button key={c.name} type="button" aria-pressed={on} title={`${c.pdfs} PDFs, ${c.tests} test cases`}
                  onClick={() => setSelected((s) => (s.includes(c.name) ? s.filter((x) => x !== c.name) : [...(isAll ? [] : s), c.name]))}
                  className={`rounded-full border px-3.5 py-1.5 text-sm ${on ? "border-blue-600 bg-blue-50 text-blue-800" : "border-gray-300 text-gray-700 hover:bg-gray-50"}`}>
                  {c.name}
                </button>
              );
            })}
          </div>
        </fieldset>

        <div className="flex flex-wrap items-end gap-4">
          <label className="text-sm text-gray-700">
            <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-gray-500">PDFs per category</span>
            <input type="number" min={1} max={meta?.defaults.max_per_category ?? 50} value={perCategory}
              onChange={(e) => setPerCategory(Math.max(1, Math.min(meta?.defaults.max_per_category ?? 50, Number(e.target.value) || 1)))}
              className="w-28 rounded-full border border-gray-300 px-4 py-2 text-sm" />
          </label>
          <p className="pb-2 text-sm text-gray-500">About {estimate} PDFs</p>
          <button type="button" onClick={start} disabled={busy || !meta || (run != null && ACTIVE.has(run.status))}
            className="ml-auto inline-flex items-center gap-2 rounded-full bg-blue-600 px-5 py-2.5 text-sm font-medium text-white shadow-card hover:bg-blue-700 disabled:opacity-50">
            <Play size={16} /> Start benchmark
          </button>
        </div>
      </section>

      {run && (
        <section className={`${card} flex flex-col gap-3`} aria-labelledby="bench-run" aria-live="polite">
          <div className="flex flex-wrap items-center gap-2">
            <h3 id="bench-run" className="font-medium text-gray-900">Run {shortId(run.run_id)}</h3>
            <span className="rounded-full bg-gray-100 px-2.5 py-0.5 text-xs font-medium capitalize text-gray-700">{run.status}{run.phase && ACTIVE.has(run.status) ? `: ${run.phase}` : ""}</span>
            <span className="ml-auto text-sm text-gray-500">Elapsed {fmtTime(run.elapsed_s)}</span>
          </div>
          {run.counts && (
            <>
              <div className="h-2.5 overflow-hidden rounded-full bg-gray-100" role="progressbar" aria-valuemin={0} aria-valuemax={run.counts.total} aria-valuenow={run.counts.done + run.counts.failed}>
                <div className="h-full bg-blue-600 transition-[width]" style={{ width: `${((run.counts.done + run.counts.failed) / Math.max(1, run.counts.total)) * 100}%` }} />
              </div>
              <p className="text-sm text-gray-700">
                {run.counts.done + run.counts.failed} of {run.counts.total} documents processed · <span className="text-green-700">{run.counts.done} completed</span> · <span className={run.counts.failed ? "text-red-700" : ""}>{run.counts.failed} failed</span>
              </p>
            </>
          )}
          {run.error && <p className="text-sm text-red-700">{run.error}</p>}
          <p className="text-xs text-gray-500">
            {run.config.categories.length} categories · {run.config.per_category} per category · seed {run.config.seed} · revision {run.git.sha?.slice(0, 7) ?? "unknown"}{run.git.dirty ? " (uncommitted changes)" : ""}
          </p>
          <div className="flex flex-wrap gap-2">
            {ACTIVE.has(run.status) && (
              <button type="button" onClick={cancel} disabled={busy} className="inline-flex items-center gap-1.5 rounded-full border border-gray-300 px-4 py-2 text-sm hover:bg-gray-50">
                <Square size={14} /> Cancel
              </button>
            )}
            {run.resumable && run.status !== "created" && (
              <button type="button" onClick={resume} disabled={busy} className="inline-flex items-center gap-1.5 rounded-full bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700">
                <RotateCcw size={14} /> Resume
              </button>
            )}
            <button type="button" onClick={showLog} disabled={busy} className="rounded-full border border-gray-300 px-4 py-2 text-sm hover:bg-gray-50">View log</button>
            {results?.has_report && (
              <button type="button" onClick={showReport} disabled={busy} className="rounded-full border border-gray-300 px-4 py-2 text-sm hover:bg-gray-50">View report</button>
            )}
          </div>
          {run.failures.length > 0 && (
            <details className="text-sm">
              <summary className="cursor-pointer text-red-700">{run.failures.length} failed documents</summary>
              <ul className="mt-2 flex flex-col gap-1 text-xs text-gray-700">
                {run.failures.map((f) => (<li key={f.pdf}><span className="font-medium">{f.pdf}</span>: {f.error ?? "unknown error"}</li>))}
              </ul>
            </details>
          )}
          {log !== null && <pre className="max-h-64 overflow-auto rounded-lg bg-gray-900 p-3 text-xs text-gray-100">{log}</pre>}
        </section>
      )}

      {results && run?.status === "completed" && (
        <section className={`${card} flex flex-col gap-4`} aria-labelledby="bench-results">
          <SampleBanner official={results.is_official} />
          <div className="flex flex-wrap items-baseline gap-3">
            <h3 id="bench-results" className="font-medium text-gray-900">Sample score</h3>
            <span className="font-display text-3xl font-semibold text-gray-900">{pct(results.overall_score_pct)}</span>
            {results.confidence_interval && <span className="text-sm text-gray-500">{results.confidence_interval}</span>}
          </div>
          {results.scoring_error && <p className="text-sm text-red-700">{results.scoring_error}</p>}
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-gray-500"><tr><th className="py-2 pr-4">Category</th><th className="py-2 pr-4">Pass rate</th><th className="py-2">Tests passed</th></tr></thead>
              <tbody>
                {Object.entries(results.categories).map(([name, c]) => (
                  <tr key={name} className="border-t border-gray-100"><td className="py-2 pr-4 font-medium">{name}</td><td className="py-2 pr-4">{pct(c.pass_rate_pct)}</td><td className="py-2">{c.passed}/{c.total}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-gray-500">
            Median {results.timing.per_page_ms.median ?? "n/a"} ms per page · {results.documents.succeeded}/{results.documents.total} documents extracted · {results.failed_test_count} failed test cases
          </p>
        </section>
      )}

      {reportUrl && (
        <section className={card} aria-label="Official scorer report">
          <iframe title="Benchmark report" src={reportUrl} sandbox="allow-scripts" className="h-[70vh] w-full rounded-lg border border-gray-200" />
        </section>
      )}

      <section className={card} aria-labelledby="bench-history">
        <h3 id="bench-history" className="mb-3 font-medium text-gray-900">History</h3>
        {history.length === 0 ? (
          <p className="text-sm text-gray-500">No benchmark runs yet.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {history.map((h) => (
              <li key={h.run_id}>
                <button type="button" onClick={() => open(h.run_id)}
                  className={`flex w-full flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border px-3 py-2 text-left text-sm hover:bg-gray-50 ${h.run_id === activeId ? "border-blue-600" : "border-gray-200"}`}>
                  <span className="font-medium">{shortId(h.run_id)}</span>
                  <span className="capitalize text-gray-600">{h.status}</span>
                  <span className="text-gray-500">{h.counts ? `${h.counts.done}/${h.counts.total} docs` : ""}</span>
                  <span className="ml-auto">{h.overall_score_pct != null ? <>{pct(h.overall_score_pct)} <span className="text-xs text-amber-800">sample</span></> : ""}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {completed.length >= 2 && (
        <section className={`${card} flex flex-col gap-3`} aria-labelledby="bench-compare">
          <h3 id="bench-compare" className="font-medium text-gray-900">Compare runs</h3>
          <div className="flex flex-wrap items-center gap-2">
            {[[cmpA, setCmpA, "Baseline run"], [cmpB, setCmpB, "Comparison run"]].map(([val, set, label]) => (
              <select key={label as string} value={val as string} aria-label={label as string} onChange={(e) => (set as (v: string) => void)(e.target.value)}
                className="rounded-full border border-gray-300 bg-white px-4 py-2 text-sm">
                <option value="">{label as string}</option>
                {completed.map((h) => (<option key={h.run_id} value={h.run_id}>{shortId(h.run_id)} ({pct(h.overall_score_pct)})</option>))}
              </select>
            ))}
            <button type="button" onClick={compare} disabled={busy || !cmpA || !cmpB || cmpA === cmpB} className="rounded-full bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50">Compare</button>
          </div>
          {cmp && (
            <div className="flex flex-col gap-2">
              <SampleBanner official={false} />
              {cmp.note && <p className="text-xs text-amber-800">{cmp.note}</p>}
              <p className="text-sm">Overall: {pct(cmp.overall_a_pct)} to {pct(cmp.overall_b_pct)} ({cmp.overall_delta_pct != null ? `${cmp.overall_delta_pct > 0 ? "+" : ""}${cmp.overall_delta_pct}` : "n/a"} points)</p>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="text-xs uppercase tracking-wide text-gray-500"><tr><th className="py-2 pr-4">Category</th><th className="py-2 pr-4">Baseline</th><th className="py-2 pr-4">Comparison</th><th className="py-2">Change</th></tr></thead>
                  <tbody>
                    {cmp.categories.map((c) => (
                      <tr key={c.category} className="border-t border-gray-100">
                        <td className="py-2 pr-4 font-medium">{c.category}</td><td className="py-2 pr-4">{pct(c.a_pct)}</td><td className="py-2 pr-4">{pct(c.b_pct)}</td>
                        <td className={`py-2 ${c.delta_pct != null && c.delta_pct < 0 ? "text-red-700" : c.delta_pct ? "text-green-700" : ""}`}>{c.delta_pct == null ? "n/a" : `${c.delta_pct > 0 ? "+" : ""}${c.delta_pct}`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </section>
      )}
    </main>
  );
}
