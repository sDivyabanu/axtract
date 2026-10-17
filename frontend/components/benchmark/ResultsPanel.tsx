"use client";

import { useEffect, useState } from "react";
import { fetchBenchReportUrl, type BenchResults } from "@/lib/benchmark";
import { fmtPct, headline } from "@/lib/benchmark-utils";
import { btn, card, errorText, ScoreBadge, ScoreNotice, th } from "./ui";

export default function ResultsPanel({ results, onInspect }: { results: BenchResults; onInspect: (category?: string) => void }) {
  const [reportUrl, setReportUrl] = useState<string | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const h = headline(results.overall_score_pct, results.diagnostic_score_pct);
  const valid = results.validity === "valid";

  useEffect(() => () => { if (reportUrl) URL.revokeObjectURL(reportUrl); }, [reportUrl]);

  async function showReport() {
    try {
      setReportUrl(await fetchBenchReportUrl(results.run_id));
      setReportError(null);
    } catch (e) {
      setReportError(errorText(e, "Could not load the report."));
    }
  }

  return (
    <section className={`${card} flex flex-col gap-4`} aria-labelledby="bench-results">
      <ScoreNotice kind={results.score_kind} />
      <div className="flex flex-wrap items-baseline gap-3">
        <h3 id="bench-results" className="font-medium text-gray-900">{valid ? "Score" : "Diagnostic result"}</h3>
        <span className={`font-display text-3xl font-semibold ${valid ? "text-gray-900" : "text-gray-500"}`}>{h.value}</span>
        <span className="text-sm text-gray-500">{h.caption}</span>
        {results.confidence_interval && <span className="text-sm text-gray-500">{results.confidence_interval}</span>}
        <span className="ml-auto"><ScoreBadge kind={results.score_kind} /></span>
      </div>
      {results.official_status && <p className="text-sm text-gray-700">Official status: <strong>{results.official_status}</strong></p>}
      {results.scoring_error && <p className="text-sm text-red-700">{results.scoring_error}</p>}
      {results.notes.map((n) => (<p key={n} className="text-sm text-amber-900">{n}</p>))}

      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead><tr><th className={th}>Category</th><th className={th}>{valid ? "Pass rate" : "Diagnostic pass rate"}</th><th className={th}>Cases passed</th><th /></tr></thead>
          <tbody>
            {Object.entries(results.categories).map(([name, c]) => (
              <tr key={name} className="border-t border-gray-100">
                <td className="py-2 pr-4 font-medium">{name}</td>
                <td className="py-2 pr-4">{fmtPct(c.pass_rate_pct)}{c.basis === "diagnostic" && <span className="ml-1 text-xs text-gray-500">(diagnostic)</span>}</td>
                <td className="py-2 pr-4">{c.passed}/{c.total}</td>
                <td className="py-2 text-right"><button type="button" className="text-xs text-blue-700 underline" onClick={() => onInspect(name)}>Inspect failures</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-gray-500">
        {results.documents.succeeded}/{results.documents.total} documents extracted
        {results.documents.timed_out ? ` · ${results.documents.timed_out} timed out` : ""}
        {results.documents.empty ? ` · ${results.documents.empty} empty` : ""}
        {` · median ${results.timing.per_page_ms.median ?? "n/a"} ms per page`}
        {results.cases.failed !== null ? ` · ${results.cases.failed} failed cases` : ""}
      </p>
      {results.scorer_errors.length > 0 && (
        <details className="text-xs text-gray-600">
          <summary className="cursor-pointer">Scorer reported {results.scorer_error_count} problem(s)</summary>
          <ul className="mt-1 flex flex-col gap-1">{results.scorer_errors.map((e) => (<li key={e}>{e}</li>))}</ul>
        </details>
      )}
      <div className="flex flex-wrap gap-2">
        <button type="button" className={btn} onClick={() => onInspect()}>Inspect failed cases</button>
        {results.has_report ? (
          <button type="button" className={btn} onClick={showReport}>View official report</button>
        ) : (
          <span className="self-center text-xs text-gray-500">No HTML report for this run (large reports are not generated or loaded here).</span>
        )}
      </div>
      {reportError && <p className="text-sm text-red-700">{reportError}</p>}
      {reportUrl && <iframe title="Benchmark report" src={reportUrl} sandbox="allow-scripts" className="h-[70vh] w-full rounded-lg border border-gray-200" />}
    </section>
  );
}
