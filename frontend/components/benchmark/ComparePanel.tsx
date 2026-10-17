"use client";

import { useState } from "react";
import { compareBenchRuns, type BenchComparison, type BenchHistoryItem } from "@/lib/benchmark";
import { fmtDelta, fmtDuration, fmtPct, shortId } from "@/lib/benchmark-utils";
import { btn, btnPrimary, card, Empty, ErrorBanner, errorText, input, ScoreBadge, th } from "./ui";

function Side({ s, label }: { s: BenchComparison["a"]; label: string }) {
  return (
    <div className="rounded-lg border border-gray-200 p-3 text-sm">
      <p className="text-xs font-medium uppercase tracking-wide text-gray-500">{label}</p>
      <p className="font-medium">{shortId(s.run_id)}</p>
      <p className="text-xs text-gray-600">commit {s.git.sha?.slice(0, 7) ?? "unknown"}{s.git.dirty ? " + uncommitted changes" : ""}</p>
      <p className="text-xs text-gray-600">{s.categories_tested.join(", ") || "no categories"}</p>
      <p className="text-xs text-gray-600">{s.documents.succeeded}/{s.documents.total} documents · {fmtDuration(s.elapsed_s)}</p>
      <div className="mt-1"><ScoreBadge kind={s.score_kind} /></div>
    </div>
  );
}

export default function ComparePanel({ history }: { history: BenchHistoryItem[] }) {
  const done = history.filter((h) => h.status === "completed" || h.status === "imported");
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [cmp, setCmp] = useState<BenchComparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(partial: boolean) {
    setBusy(true);
    try {
      setCmp(await compareBenchRuns(a, b, partial));
      setError(null);
    } catch (e) {
      setCmp(null);
      setError(errorText(e, "Could not compare these runs."));
    } finally {
      setBusy(false);
    }
  }

  if (done.length < 2) return <Empty>Finish two benchmark runs (or import one) to compare them.</Empty>;
  const option = (h: BenchHistoryItem) => (
    <option key={h.run_id} value={h.run_id}>{shortId(h.run_id)} · {h.git?.sha?.slice(0, 7) ?? "?"} · {fmtPct(h.overall_score_pct ?? h.diagnostic_score_pct)}</option>
  );

  return (
    <section className={`${card} flex flex-col gap-4`} aria-labelledby="bench-compare">
      <h3 id="bench-compare" className="font-medium text-gray-900">Before and after</h3>
      <div className="flex flex-wrap items-center gap-2">
        <select aria-label="Before (baseline run)" value={a} onChange={(e) => setA(e.target.value)} className={input}><option value="">Before</option>{done.map(option)}</select>
        <select aria-label="After (comparison run)" value={b} onChange={(e) => setB(e.target.value)} className={input}><option value="">After</option>{done.map(option)}</select>
        <button type="button" className={btnPrimary} disabled={busy || !a || !b || a === b} onClick={() => run(false)}>Compare</button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {cmp && (
        <>
          <div className="grid gap-3 sm:grid-cols-2"><Side s={cmp.a} label="Before" /><Side s={cmp.b} label="After" /></div>
          {cmp.blockers.length > 0 && (
            <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
              <p className="font-medium">{cmp.partial ? "Partial comparison: these runs are not directly comparable." : "These runs are not directly comparable."}</p>
              <ul className="list-disc pl-5">{cmp.blockers.map((x) => (<li key={x}>{x}</li>))}</ul>
              {!cmp.partial && <button type="button" className={`${btn} mt-2`} disabled={busy} onClick={() => run(true)}>Compare the cases they share anyway</button>}
            </div>
          )}
          {cmp.warnings.map((w) => (<p key={w} className="text-xs text-amber-800">{w}</p>))}
          {(cmp.comparable || cmp.partial) && (
            <>
              <p className="text-sm">
                Overall ({cmp.overall_basis}): {fmtPct(cmp.a.overall_score_pct ?? cmp.a.diagnostic_score_pct)} to {fmtPct(cmp.b.overall_score_pct ?? cmp.b.diagnostic_score_pct)}{" "}
                <strong className={cmp.overall_delta_pct !== null && cmp.overall_delta_pct < 0 ? "text-red-700" : "text-green-700"}>({fmtDelta(cmp.overall_delta_pct)} points)</strong>
                {cmp.latency?.delta_pct != null && <> · latency {fmtDelta(cmp.latency.delta_pct)}% per page</>}
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead><tr><th className={th}>Category</th><th className={th}>Before</th><th className={th}>After</th><th className={th}>Change</th><th className={th}>Cases (after)</th></tr></thead>
                  <tbody>
                    {cmp.categories.map((c) => (
                      <tr key={c.category} className="border-t border-gray-100">
                        <td className="py-2 pr-4 font-medium">{c.category}</td><td className="py-2 pr-4">{fmtPct(c.a_pct)}</td><td className="py-2 pr-4">{fmtPct(c.b_pct)}</td>
                        <td className={`py-2 pr-4 font-medium ${c.delta_pct != null && c.delta_pct < 0 ? "text-red-700" : c.delta_pct ? "text-green-700" : ""}`}>{fmtDelta(c.delta_pct)}</td>
                        <td className="py-2 text-xs text-gray-600">{c.b_passed ?? "?"}/{c.b_total ?? "?"} passed</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {cmp.cases && (
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <p className="text-sm font-medium text-green-700">{cmp.cases.newly_passing} newly passing</p>
                    <ul className="max-h-40 overflow-auto text-xs text-gray-700">{cmp.cases.newly_passing_list.map((c) => (<li key={c.id}>{c.pdf}</li>))}</ul>
                  </div>
                  <div>
                    <p className="text-sm font-medium text-red-700">{cmp.cases.newly_failing} newly failing</p>
                    <ul className="max-h-40 overflow-auto text-xs text-gray-700">{cmp.cases.newly_failing_list.map((c) => (<li key={c.id}>{c.pdf}</li>))}</ul>
                  </div>
                  <p className="text-xs text-gray-500 sm:col-span-2">{cmp.cases.compared} cases compared · {cmp.cases.still_failing} still failing · {cmp.cases.still_passing} still passing</p>
                </div>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
