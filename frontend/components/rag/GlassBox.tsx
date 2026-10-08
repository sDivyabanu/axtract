"use client";

import { useState } from "react";
import type { Answer } from "@/lib/rag";

/** Inside view of one answer: route, retrieval scores, stage timings, model and mode. */
export default function GlassBox({ answer }: { answer: Answer }) {
  const [open, setOpen] = useState(false);
  const g = answer.glass_box;
  return (
    <div className="mt-3 rounded-xl border border-gray-200 text-xs shadow-card">
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center justify-between rounded-xl px-4 py-2 text-gray-600 hover:bg-gray-50 transition">
        <span className="font-semibold">Glass box — how this answer was produced</span>
        <span>{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="space-y-3 border-t border-gray-200 p-4">
          <div className="flex flex-wrap gap-x-6 gap-y-1">
            <span><b>Route:</b> {g.route}</span>
            <span><b>Mode:</b> {answer.mode === "llm" ? `LLM (${answer.model})` : "extractive (no generation)"}</span>
            <span><b>Pipeline:</b> {answer.pipeline ?? "dealLens"}</span>
            <span><b>Chunks searched:</b> {g.workspace_chunks}</span>
            {answer.total_ms != null && <span><b>Total:</b> {(answer.total_ms / 1000).toFixed(1)} s</span>}
          </div>
          {!g.llm.available && <p className="rounded bg-amber-50 p-2 text-amber-800">LLM offline: {g.llm.reason}. Answers are extractive.</p>}

          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-gray-500">Stage timings</div>
            <div className="flex flex-wrap gap-2">
              {answer.stages.map((s) => (
                <span key={s.name} className="rounded-full bg-gray-100 px-2.5 py-0.5 font-medium">{s.name} <b>{s.ms} ms</b></span>
              ))}
            </div>
          </div>

          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-gray-500">Retrieved evidence (BM25 + dense → RRF → cross-encoder rerank)</div>
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead className="text-gray-500">
                  <tr><th className="pr-2">used</th><th className="pr-2">document</th><th className="pr-2">kind</th><th className="pr-2">pages</th>
                    <th className="pr-2 text-right">BM25</th><th className="pr-2 text-right">dense</th><th className="pr-2 text-right">RRF</th><th className="text-right">rerank</th></tr>
                </thead>
                <tbody>
                  {g.retrieved.map((h) => (
                    <tr key={h.chunk_id} className={h.used ? "bg-green-50" : ""}>
                      <td className="pr-2">{h.used ? "✓" : ""}</td>
                      <td className="max-w-[160px] truncate pr-2">{h.filename}</td>
                      <td className="pr-2">{h.kind}</td>
                      <td className="pr-2">{h.pages.join(",")}</td>
                      <td className="pr-2 text-right tabular-nums">{h.bm25.toFixed(2)}{h.bm25_rank ? ` (#${h.bm25_rank})` : ""}</td>
                      <td className="pr-2 text-right tabular-nums">{h.dense.toFixed(3)}{h.dense_rank ? ` (#${h.dense_rank})` : ""}</td>
                      <td className="pr-2 text-right tabular-nums">{h.rrf.toFixed(4)}</td>
                      <td className="text-right tabular-nums">{h.rerank == null ? "—" : h.rerank.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-1 text-gray-500">
              Abstain if best rerank &lt; {String(g.thresholds.abstain_below)} (best here: {g.thresholds.best == null ? "—" : Number(g.thresholds.best).toFixed(2)}).
            </p>
          </div>
          {g.plan != null && (
            <div>
              <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-gray-500">Table plan (executed by our code, not by the LLM)</div>
              <pre className="overflow-x-auto rounded-xl bg-gray-50 p-3">{JSON.stringify(g.plan, null, 2)}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
