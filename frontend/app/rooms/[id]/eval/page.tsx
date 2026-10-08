"use client";

import { Suspense, useEffect, useState } from "react";
import { rag, type EvalResult, type PipelineSummary } from "@/lib/rag";

const METRICS: { key: keyof PipelineSummary; label: string; note?: string }[] = [
  { key: "accuracy", label: "Answer accuracy" },
  { key: "numeric_exact_match", label: "Numeric exact-match" },
  { key: "citation_accuracy", label: "Citation accuracy", note: "right document + page; the baseline produces no citations" },
  { key: "abstention_correctness", label: "Abstention correctness", note: "declines unanswerable questions" },
  { key: "injection_resistance", label: "Injection resistance", note: "does not obey the hidden instruction" },
  { key: "injection_accuracy", label: "Injection traps answered correctly", note: "resisted and stated the real fact" },
];

const pct = (v: number | null | undefined) => (v == null ? "n/a" : `${Math.round(v * 100)}%`);

function Bar({ value, color }: { value: number | null; color: string }) {
  return (
    <div className="h-4 w-full rounded bg-gray-100">
      <div className={`h-4 rounded ${color}`} style={{ width: `${Math.round((value ?? 0) * 100)}%` }} />
    </div>
  );
}

function Inner() {
  const [r, setR] = useState<EvalResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { rag.get<EvalResult>("/eval/latest").then(setR).catch((e) => setError(e.message)); }, []);

  if (error) {
    return (
      <div className="rounded border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
        {error}
        <pre className="mt-2 rounded bg-white p-2 text-xs">backend/.venv/bin/python scripts/run_rag_eval.py</pre>
      </div>
    );
  }
  if (!r) return <p className="text-sm text-gray-500">Loading…</p>;
  const b = r.summary.baseline, d = r.summary.dealLens;
  return (
    <div className="space-y-6">
      <p className="text-sm text-gray-600">
        Golden questions on the Project Falcon data room, run live through both pipelines with the same model (<b>{r.llm}</b>).
        {" "}{r.n_questions} questions · run {r.ts_iso}. Judging is mechanical and identical for both; nothing is edited by hand.
      </p>

      <section className="rounded-lg border border-gray-200 p-4" data-eval>
        <div className="mb-3 flex items-center gap-4 text-xs text-gray-600">
          <span className="flex items-center gap-1"><span className="inline-block h-3 w-3 rounded bg-gray-400" />Baseline</span>
          <span className="flex items-center gap-1"><span className="inline-block h-3 w-3 rounded bg-blue-600" />DealLens</span>
        </div>
        <div className="space-y-4">
          {METRICS.map((m) => (
            <div key={m.key}>
              <div className="mb-1 flex items-baseline justify-between text-sm">
                <span className="font-medium">{m.label}</span>
                {m.note && <span className="text-xs text-gray-400">{m.note}</span>}
              </div>
              <div className="grid grid-cols-[1fr_3rem] items-center gap-2"><Bar value={b[m.key] as number | null} color="bg-gray-400" /><span className="text-sm tabular-nums">{pct(b[m.key] as number | null)}</span></div>
              <div className="mt-1 grid grid-cols-[1fr_3rem] items-center gap-2"><Bar value={d[m.key] as number | null} color="bg-blue-600" /><span className="text-sm font-semibold tabular-nums">{pct(d[m.key] as number | null)}</span></div>
            </div>
          ))}
          <div className="grid grid-cols-3 gap-3 border-t border-gray-100 pt-3 text-sm">
            <div><div className="text-xs text-gray-500">Average latency</div>Baseline <b>{b.avg_latency_s} s</b> · DealLens <b>{d.avg_latency_s} s</b></div>
            <div><div className="text-xs text-gray-500">Retrieval hit rate</div>Baseline <b>{pct(b.retrieval_hit_rate)}</b> · DealLens <b>{pct(d.retrieval_hit_rate)}</b></div>
          </div>
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">Accuracy by question type</h2>
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs text-gray-500"><tr><th className="px-3 py-2">Type</th><th className="px-3 py-2">Baseline</th><th className="px-3 py-2">DealLens</th></tr></thead>
            <tbody className="divide-y divide-gray-100">
              {Object.keys(d.by_type).map((t) => (
                <tr key={t}><td className="px-3 py-2">{t}</td><td className="px-3 py-2 tabular-nums">{pct(b.by_type[t])}</td><td className="px-3 py-2 font-semibold tabular-nums">{pct(d.by_type[t])}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">Every question</h2>
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs text-gray-500"><tr><th className="px-3 py-2">#</th><th className="px-3 py-2">Question</th><th className="px-3 py-2">Baseline</th><th className="px-3 py-2">DealLens</th></tr></thead>
            <tbody className="divide-y divide-gray-100">
              {r.questions.map((q) => (
                <tr key={q.id}>
                  <td className="px-3 py-2 text-gray-400">{q.id}</td>
                  <td className="px-3 py-2"><div>{q.question}</div><div className="text-xs text-gray-400">{q.type}</div></td>
                  {[q.baseline, q.dealLens].map((x, i) => (
                    <td key={i} className="px-3 py-2 align-top">
                      <span className={x.judge.pass ? "text-green-700" : "text-red-700"}>{x.judge.pass ? "✓" : "✗"} {x.judge.detail}</span>
                      <div className="line-clamp-2 text-xs text-gray-400">{x.text}</div>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        <h2 className="mb-1 font-semibold">Honest limitations</h2>
        <ul className="list-disc space-y-1 pl-5">{r.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
      </section>
    </div>
  );
}

export default function Page() {
  return <Suspense fallback={<p className="text-sm text-gray-500">Loading…</p>}><Inner /></Suspense>;
}
