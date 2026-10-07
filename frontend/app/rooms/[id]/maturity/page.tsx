"use client";

import { useParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import EvidenceViewer, { type Highlight } from "@/components/EvidenceViewer";
import ReceiptCard from "@/components/rag/ReceiptCard";
import { rag, type Operand, type TimelineEvent, type Wall } from "@/lib/rag";

interface View { docId: string; filename: string; pageCount: number; highlights: Highlight[]; page?: number }
const KIND_COLOR: Record<string, string> = { maturity: "#dc2626", expiry: "#d97706", renewal: "#2563eb", notice: "#7c3aed", termination: "#be123c", other: "#6b7280" };

function Inner() {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<{ walls: Wall[]; events: TimelineEvent[] } | null>(null);
  const [year, setYear] = useState<number | null>(null);
  const [view, setView] = useState<View | null>(null);
  const [docs, setDocs] = useState<Record<string, number>>({});

  useEffect(() => {
    rag.get<{ walls: Wall[]; events: TimelineEvent[] }>(`/workspaces/${id}/maturity-wall`).then((d) => {
      setData(d);
      if (d.walls[0]?.bars[0]) setYear(d.walls[0].bars[0].year);
    }).catch(() => setData({ walls: [], events: [] }));
    rag.getWorkspace(id).then((w) => setDocs(Object.fromEntries(w.documents.map((x) => [x.doc_id, x.preview_pages])))).catch(() => {});
  }, [id]);

  const wall = data?.walls[0];
  const max = useMemo(() => Math.max(1, ...(wall?.bars.map((b) => b.value) ?? [1])), [wall]);
  const bar = wall?.bars.find((b) => b.year === year);

  function openOperand(o: Operand) {
    setView({ docId: o.doc_id, filename: o.filename, pageCount: docs[o.doc_id] ?? 1, page: o.page, highlights: [{ page: o.page, bbox: o.bbox, label: `${o.label} ${o.display}` }] });
  }
  function openEvent(e: TimelineEvent) {
    if (!e.page) return;
    setView({ docId: e.doc_id, filename: e.filename, pageCount: e.preview_pages, page: e.page, highlights: [{ page: e.page, bbox: e.bbox, label: e.label.slice(0, 40) }] });
  }

  if (!data) return <p className="text-sm text-gray-500">Building the maturity wall…</p>;
  const events = data.events;
  const t0 = events.length ? new Date(events[0].date).getTime() : 0;
  const t1 = events.length ? new Date(events[events.length - 1].date).getTime() : 1;

  return (
    <div className={view ? "grid grid-cols-1 gap-4 lg:grid-cols-2" : ""}>
      <div className="min-w-0 space-y-6">
        <section>
          <h2 className="mb-1 text-sm font-semibold text-gray-600">Debt maturity wall {wall ? `· ${wall.title} (${wall.unit})` : ""}</h2>
          {!wall ? (
            <p className="rounded border border-dashed border-gray-300 p-6 text-center text-sm text-gray-500">No debt table with maturity dates was found.</p>
          ) : (
            <>
              <div className="flex h-56 items-end gap-3 rounded-lg border border-gray-200 bg-white px-4 pb-2 pt-6" data-wall>
                {wall.bars.map((b) => (
                  <button key={b.year} type="button" onClick={() => setYear(b.year)} className="group flex h-full flex-1 flex-col justify-end" title={`${b.year}: ${b.display}`}>
                    <span className="mb-1 text-center text-xs font-semibold tabular-nums">{b.display}</span>
                    <span className={`w-full rounded-t ${b.year === year ? "bg-red-600" : "bg-blue-500 group-hover:bg-blue-600"}`}
                      style={{ height: `${Math.max(4, (b.value / max) * 100)}%` }} />
                    <span className="mt-1 text-center text-xs text-gray-600">{b.year}</span>
                  </button>
                ))}
              </div>
              {bar && <div className="mt-3"><ReceiptCard receipt={bar.receipt} onOperand={openOperand} /></div>}
            </>
          )}
        </section>

        <section>
          <h2 className="mb-1 text-sm font-semibold text-gray-600">Deal timeline · {events.length} dated events</h2>
          {events.length === 0 ? (
            <p className="text-sm text-gray-500">No dated events found.</p>
          ) : (
            <>
              <svg viewBox="0 0 800 70" className="mb-3 w-full" role="img" aria-label="Timeline of dated events">
                <line x1="20" y1="40" x2="780" y2="40" stroke="#9ca3af" strokeWidth="2" />
                {events.map((e, i) => {
                  const x = t1 === t0 ? 400 : 20 + ((new Date(e.date).getTime() - t0) / (t1 - t0)) * 760;
                  return (
                    <g key={i} onClick={() => openEvent(e)} className="cursor-pointer">
                      <circle cx={x} cy={40} r={(e.count ?? 1) > 1 ? 8 : 5} fill={KIND_COLOR[e.kind] ?? KIND_COLOR.other}><title>{e.date}: {e.label}</title></circle>
                    </g>
                  );
                })}
                <text x="20" y="62" fontSize="11" fill="#6b7280">{events[0].date}</text>
                <text x="780" y="62" fontSize="11" fill="#6b7280" textAnchor="end">{events[events.length - 1].date}</text>
              </svg>
              <ul className="divide-y divide-gray-100 rounded-lg border border-gray-200 text-sm" data-timeline>
                {events.map((e, i) => (
                  <li key={i}>
                    <button type="button" onClick={() => openEvent(e)} className="flex w-full items-start gap-3 px-3 py-2 text-left hover:bg-gray-50">
                      <span className="mt-1.5 h-2 w-2 flex-shrink-0 rounded-full" style={{ background: KIND_COLOR[e.kind] ?? KIND_COLOR.other }} />
                      <span className="w-24 flex-shrink-0 tabular-nums text-gray-500">{e.date}</span>
                      <span className="min-w-0 flex-1">{e.label}</span>
                      <span className="flex-shrink-0 text-xs text-gray-400">{e.filename} p.{e.page}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
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
