"use client";

import { useParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import EvidenceViewer, { type Highlight } from "@/components/EvidenceViewer";
import { rag, SEVERITY_STYLE, type Contradiction, type Side } from "@/lib/rag";

interface View { docId: string; filename: string; pageCount: number; highlights: Highlight[]; page?: number }

function Inner() {
  const { id } = useParams<{ id: string }>();
  const [items, setItems] = useState<Contradiction[] | null>(null);
  const [view, setView] = useState<View | null>(null);

  useEffect(() => { rag.get<Contradiction[]>(`/workspaces/${id}/contradictions`).then(setItems).catch(() => setItems([])); }, [id]);

  function open(s: Side) {
    setView({ docId: s.doc_id, filename: s.filename, pageCount: s.preview_pages, page: s.page,
      highlights: [{ page: s.page, bbox: s.bbox, label: `${s.label} ${s.display}` }] });
  }

  if (items === null) return <p className="text-sm text-gray-500">Checking documents against each other…</p>;
  return (
    <div className={`ax-rise ${view ? "grid grid-cols-1 gap-4 lg:grid-cols-2" : ""}`}>
      <div>
        <p className="mb-3 text-sm text-gray-600">
          The same figure (concept, period, unit) read from different documents. Differences larger than 0.5 % — or than rounding — are listed
          with both sides, each linked to the exact cell.
        </p>
        {items.length === 0 ? (
          <p className="rounded-2xl border border-dashed border-gray-300 bg-white p-6 text-center text-sm text-gray-500 shadow-card">No contradictions found across the documents.</p>
        ) : (
          <ul className="space-y-3" data-contradictions>
            {items.map((c) => (
              <li key={c.id} className="rounded-2xl border border-red-200 bg-red-50/30 p-5 shadow-card">
                <div className="mb-1 flex items-center gap-2">
                  <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold uppercase ${SEVERITY_STYLE[c.severity]}`}>{c.severity}</span>
                  <h3 className="font-display font-semibold">{c.concept_label} · {c.period}</h3>
                  <span className="ml-auto rounded-full bg-red-100 px-2.5 py-0.5 text-sm font-bold text-red-800">{c.gap_pct}% gap</span>
                </div>
                <p className="text-sm text-gray-800">{c.summary}</p>
                {c.note && <p className="mt-1 text-xs text-amber-800">{c.note}</p>}
                <div className="mt-2 grid gap-2 sm:grid-cols-2">
                  {[c.primary.high, c.primary.low].map((s, i) => (
                    <button key={i} type="button" onClick={() => open(s)}
                      className={`rounded-xl border p-3 text-left shadow-card transition hover:shadow-lift ${i === 0 ? "border-red-300" : "border-green-300"}`}>
                      <div className="text-xs text-gray-500">{s.filename} · p.{s.printed_page ?? s.page}</div>
                      <div className="text-lg font-bold tabular-nums">{s.display}</div>
                      <div className="text-xs text-gray-600">{s.label}</div>
                    </button>
                  ))}
                </div>
                {c.sides.length > 2 && (
                  <p className="mt-2 text-xs text-gray-500">
                    Also: {c.sides.filter((s) => s !== c.primary.high && s !== c.primary.low).map((s) => `${s.filename} ${s.display}`).join(" · ")}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
      {view && (
        <div className="sticky top-4 hidden h-[calc(100vh-2rem)] lg:block">
          <EvidenceViewer {...view} onClose={() => setView(null)} />
        </div>
      )}
    </div>
  );
}

export default function Page() {
  return <Suspense fallback={<p className="text-sm text-gray-500">Loading…</p>}><Inner /></Suspense>;
}
