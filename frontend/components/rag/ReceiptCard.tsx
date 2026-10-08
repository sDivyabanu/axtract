"use client";

import { useState } from "react";
import type { Operand, Receipt } from "@/lib/rag";

/** Number Receipt: the result, the operation and every operand with its source. */
export default function ReceiptCard({ receipt, onOperand }: { receipt: Receipt; onOperand: (o: Operand) => void }) {
  const [showAll, setShowAll] = useState(false);
  const lowConf = receipt.min_confidence != null && receipt.min_confidence < 0.85;
  return (
    <div className="rounded-lg border border-emerald-200 bg-emerald-50/50 p-3 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <span className="text-xs font-semibold uppercase tracking-wide text-emerald-700">Number receipt · {receipt.op.replace("_", " ")}</span>
          <div className="text-base font-semibold text-gray-900">{receipt.title}</div>
        </div>
        <div className="text-lg font-bold tabular-nums text-emerald-800">{receipt.result_display}</div>
      </div>
      <div className="mt-1 font-mono text-xs text-gray-700">{receipt.formula}</div>
      <ul className="mt-2 divide-y divide-emerald-100 rounded border border-emerald-100 bg-white">
        {(showAll ? receipt.operands : receipt.operands.slice(0, 6)).map((o, i) => (
          <li key={i}>
            <button type="button" onClick={() => onOperand(o)}
              className="flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left hover:bg-emerald-50">
              <span className="min-w-0">
                <span className="font-medium">{o.label}</span>
                <span className="ml-2 text-xs text-gray-500">{o.filename} · p.{o.printed_page ?? o.page}{o.printed_page ? ` (pdf p.${o.page})` : ""}{o.exact_cell ? " · exact cell" : ""}</span>
              </span>
              <span className="flex items-center gap-2">
                {o.confidence != null && o.confidence < 0.85 && (
                  <span className="rounded bg-amber-100 px-1.5 text-xs text-amber-800">{Math.round(o.confidence * 100)}%</span>
                )}
                <span className="tabular-nums">{o.display}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
      {receipt.operands.length > 6 && (
        <button type="button" onClick={() => setShowAll(!showAll)} className="mt-1 text-xs text-emerald-800 underline">
          {showAll ? "Show fewer operands" : `Show all ${receipt.operands.length} operands`}
        </button>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-gray-600">
        {receipt.period && <span className="rounded bg-white px-1.5 py-0.5 ring-1 ring-gray-200">period {receipt.period}</span>}
        {receipt.unit && <span className="rounded bg-white px-1.5 py-0.5 ring-1 ring-gray-200">unit {receipt.unit}</span>}
        <span className={`rounded px-1.5 py-0.5 ${lowConf ? "bg-amber-100 text-amber-800" : "bg-green-100 text-green-800"}`}>
          lowest source confidence {receipt.min_confidence == null ? "n/a (digital text)" : `${Math.round(receipt.min_confidence * 100)}%`}
        </span>
        {receipt.warnings.map((w) => <span key={w} className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-800">⚠ {w}</span>)}
      </div>
    </div>
  );
}
