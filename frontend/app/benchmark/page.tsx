"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth-context";
import {
  cancelBenchmark,
  getActiveBenchRun,
  getBenchCategories,
  getBenchEnvironment,
  getBenchResults,
  getBenchRun,
  listBenchRuns,
  resumeBenchmark,
  startBenchmark,
  type BenchCategoriesResponse,
  type BenchEnvironment,
  type BenchHistoryItem,
  type BenchResults,
  type BenchRunStatus,
  type StartOptions,
} from "@/lib/benchmark";
import { ALL_SELECTION, isActive, pickRunToRestore, type Selection } from "@/lib/benchmark-utils";
import ComparePanel from "@/components/benchmark/ComparePanel";
import FailureInspector from "@/components/benchmark/FailureInspector";
import HistoryTrends from "@/components/benchmark/HistoryTrends";
import ImportPanel from "@/components/benchmark/ImportPanel";
import ResultsPanel from "@/components/benchmark/ResultsPanel";
import RunLauncher from "@/components/benchmark/RunLauncher";
import RunPanel from "@/components/benchmark/RunPanel";
import { card, Empty, ErrorBanner, errorText } from "@/components/benchmark/ui";

type Tab = "run" | "history" | "compare" | "inspect" | "import";
const TABS: [Tab, string][] = [["run", "Run"], ["history", "History and trends"], ["compare", "Compare"], ["inspect", "Failures"], ["import", "Import"]];

export default function BenchmarkPage() {
  const { user, loading: authLoading } = useAuth();
  const [tab, setTab] = useState<Tab>("run");
  const [meta, setMeta] = useState<BenchCategoriesResponse | null>(null);
  const [env, setEnv] = useState<BenchEnvironment | null>(null);
  const [selection, setSelection] = useState<Selection>(ALL_SELECTION);
  const [history, setHistory] = useState<BenchHistoryItem[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [run, setRun] = useState<BenchRunStatus | null>(null);
  const [results, setResults] = useState<BenchResults | null>(null);
  const [inspectCategory, setInspectCategory] = useState<string | undefined>();
  const [error, setError] = useState<string | null>(null);
  const [denied, setDenied] = useState(false);
  const [busy, setBusy] = useState(false);

  const refreshHistory = useCallback(() => {
    listBenchRuns().then((r) => setHistory(r.runs)).catch(() => {});
  }, []);
  const refreshEnv = useCallback(() => {
    getBenchEnvironment().then(setEnv).catch(() => {});
  }, []);

  // First load, including refresh recovery: re-attach to the run that is executing (else the newest run).
  useEffect(() => {
    if (authLoading || !user) return;
    getBenchCategories().then(setMeta).catch((e) => {
      if (e?.code === "HTTP_403") setDenied(true);
      setError(errorText(e, "The benchmark is unavailable."));
    });
    getBenchEnvironment().then(setEnv).catch(() => {});
    Promise.all([listBenchRuns(), getActiveBenchRun()])
      .then(([h, a]) => {
        setHistory(h.runs);
        setActiveId(pickRunToRestore(a.run?.run_id ?? null, h.runs));
      })
      .catch(() => {});
  }, [authLoading, user]);

  useEffect(() => {
    if (!user) return;
    const t = setInterval(refreshEnv, 15000);
    return () => clearInterval(t);
  }, [user, refreshEnv]);

  // Follow the selected run: poll while it is active, load results once it has finished.
  const status = run?.status;
  useEffect(() => {
    if (!activeId) return;
    let stop = false;
    const tick = async () => {
      try {
        const r = await getBenchRun(activeId);
        if (stop) return;
        setRun(r);
        if (!isActive(r.status)) {
          refreshHistory();
          refreshEnv();
        }
        if (r.status === "completed" || r.status === "imported" || r.status === "failed" || r.status === "cancelled") {
          getBenchResults(activeId).then((res) => { if (!stop) setResults(res); }).catch(() => { if (!stop) setResults(null); });
        }
      } catch (e) {
        if (!stop) setError(errorText(e, "Lost contact with the benchmark."));
      }
    };
    tick();
    const timer = status && !isActive(status) ? null : setInterval(tick, 2000);
    return () => {
      stop = true;
      if (timer) clearInterval(timer);
    };
  }, [activeId, status, refreshHistory, refreshEnv]);

  function open(runId: string, nextTab: Tab = "run") {
    setActiveId(runId);
    setRun(null);
    setResults(null);
    setError(null);
    setTab(nextTab);
  }

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
      refreshEnv();
    }
  }

  const start = (o: StartOptions) => act(async () => { const { run_id } = await startBenchmark(o); refreshHistory(); open(run_id); });
  const resume = () => act(async () => { await resumeBenchmark(run!.run_id); setRun({ ...run!, status: "running", resumable: false }); });
  const cancel = () => act(async () => { await cancelBenchmark(run!.run_id); setRun({ ...run!, cancel_requested: true }); });

  const shell = "mx-auto w-full max-w-6xl px-4 py-6 sm:px-6";
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
  if (denied) {
    return (
      <main className={shell}>
        <div className={`${card} text-center text-sm text-gray-700`} role="alert">
          Your account is not allowed to use the benchmark runner. Ask an administrator to add you to the benchmark users list.
        </div>
      </main>
    );
  }

  const runActive = isActive(run?.status) || (env?.active_run_id != null);
  const categoryNames = meta?.categories.map((c) => c.name) ?? [];

  return (
    <main className={`${shell} flex flex-col gap-5`}>
      <div>
        <h2 className="font-display text-2xl font-semibold text-gray-900">Benchmark</h2>
        <p className="text-sm text-gray-500">Test AXTRACT against the official olmOCR benchmark without re-running everything after every change.</p>
      </div>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <nav aria-label="Benchmark sections" className="-mx-1 flex gap-1 overflow-x-auto px-1">
        {TABS.map(([id, label]) => (
          <button key={id} type="button" aria-current={tab === id ? "page" : undefined} onClick={() => setTab(id)}
            className={`shrink-0 rounded-full px-4 py-2 text-sm font-medium ${tab === id ? "bg-blue-50 text-blue-800" : "text-gray-700 hover:bg-gray-100"}`}>
            {label}{id === "run" && runActive ? " •" : ""}
          </button>
        ))}
      </nav>

      {tab === "run" && (
        <>
          <RunLauncher meta={meta} env={env} selection={selection} onSelection={setSelection} busy={busy} runActive={runActive} onStart={start} />
          {run && <RunPanel run={run} busy={busy} onCancel={cancel} onResume={resume} />}
          {results && (run?.status === "completed" || run?.status === "imported" || run?.status === "failed" || run?.status === "cancelled") && (
            <ResultsPanel results={results} onInspect={(c) => { setInspectCategory(c); setTab("inspect"); }} />
          )}
          {!run && <Empty>Start a quick benchmark above, or open a run from History.</Empty>}
        </>
      )}
      {tab === "history" && <HistoryTrends history={history} activeId={activeId} onOpen={(id) => open(id)} />}
      {tab === "compare" && <ComparePanel history={history} />}
      {tab === "inspect" && <FailureInspector runId={results ? activeId : null} categories={categoryNames} initialCategory={inspectCategory} />}
      {tab === "import" && <ImportPanel onImported={(id) => { refreshHistory(); open(id); }} />}
    </main>
  );
}
