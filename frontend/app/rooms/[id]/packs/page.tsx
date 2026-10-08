"use client";

import { useParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import EvidenceViewer, { type Highlight } from "@/components/EvidenceViewer";
import { API_BASE_URL } from "@/lib/api";
import { rag, type PackCell, type PackRun } from "@/lib/rag";

interface PackInfo { pack: string; title: string; doc_types: string[]; questions: string[]; latest: PackRun | null }
interface View { docId: string; filename: string; pageCount: number; highlights: Highlight[]; page?: number }

function Inner() {
  const { id } = useParams<{ id: string }>();
  const [packs, setPacks] = useState<PackInfo[]>([]);
  const [active, setActive] = useState<string>("financials");
  const [runs, setRuns] = useState<Record<string, PackRun>>({});
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    rag.get<PackInfo[]>(`/workspaces/${id}/packs`).then((p) => {
      setPacks(p);
      setRuns(Object.fromEntries(p.filter((x) => x.latest).map((x) => [x.pack, x.latest as PackRun])));
    }).catch((e) => setError(e.message));
  }, [id]);

  async function run() {
    setBusy(true); setError(null);
    try { const r = await rag.post<PackRun>(`/workspaces/${id}/packs/${active}`); setRuns((x) => ({ ...x, [active]: r })); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  function open(cell: PackCell, i = 0) {
    const c = cell.citations?.[i];
    if (!c) return;
    setView({ docId: c.doc_id, filename: c.filename, pageCount: c.preview_pages, page: c.page ?? 1,
      highlights: [{ page: c.page ?? 1, bbox: c.bbox, label: cell.text.slice(0, 40) }] });
  }

  const current = runs[active];
  return (
    <div className={`ax-rise ${view ? "grid grid-cols-1 gap-4 lg:grid-cols-2" : ""}`}>
      <div className="min-w-0">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {packs.map((p) => (
            <button key={p.pack} type="button" onClick={() => setActive(p.pack)}
              className={`rounded-full border px-3 py-1 text-sm font-medium transition ${active === p.pack ? "border-blue-600 bg-blue-600 text-white shadow-card" : "border-gray-200 bg-white hover:bg-gray-50 shadow-card"}`}>
              {p.title}
            </button>
          ))}
          <button type="button" onClick={run} disabled={busy}
            className="ml-2 rounded-full bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-card hover:bg-blue-700 disabled:opacity-50">
            {busy ? "Running…" : `Run ${packs.find((p) => p.pack === active)?.title ?? ""} pack`}
          </button>
          {current && (
            <span className="ml-auto flex gap-2 text-xs">
              <a className="rounded-full border border-gray-200 bg-white px-2.5 py-1 shadow-card hover:shadow-lift transition" href={`${API_BASE_URL}/api/workspaces/${id}/packs/${active}/export?format=xlsx`}>Export XLSX</a>
              <a className="rounded-full border border-gray-200 bg-white px-2.5 py-1 shadow-card hover:shadow-lift transition" href={`${API_BASE_URL}/api/workspaces/${id}/packs/${active}/export?format=csv`}>Export CSV</a>
            </span>
          )}
        </div>
        {error && <p role="alert" className="mb-3 rounded-xl border border-red-300 bg-red-50 p-3 text-sm text-red-700">{error}</p>}
        {!current ? (
          <p className="rounded-2xl border border-dashed border-gray-300 bg-white p-6 text-center text-sm text-gray-500 shadow-card">
            One click runs the standard questions against every relevant document. Every cell is a cited answer or “not found”.
          </p>
        ) : (
          <div className="overflow-x-auto rounded-2xl border border-gray-200 bg-white shadow-card" data-pack>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs text-gray-500">
                <tr><th className="px-3 py-2">Question</th>{current.columns.map((c) => <th key={c.doc_id} className="px-3 py-2">{c.filename}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {current.rows.map((r) => (
                  <tr key={r.label}>
                    <td className="px-3 py-2 font-medium">{r.label}</td>
                    {r.cells.map((cell) => (
                      <td key={cell.doc_id} className={`px-3 py-2 align-top ${cell.status === "not_found" ? "text-gray-400" : ""}`}>
                        {cell.status === "found" ? (
                          <button type="button" onClick={() => open(cell)} className="text-left hover:underline" title="Open the source">
                            {cell.text}
                            {cell.citations?.[0] && (
                              <span className="ml-1 text-xs font-medium text-blue-600">[p.{cell.citations[0].printed_page ?? cell.citations[0].page}]</span>
                            )}
                            {(cell.badges ?? []).map((b) => <span key={b.label} className="ml-1 rounded bg-amber-100 px-1 text-xs text-amber-800">{b.label}</span>)}
                          </button>
                        ) : "Not found"}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="border-t border-gray-100 px-3 py-1.5 text-xs text-gray-400">Computed in {current.seconds} s · no generated text (facts, tables and retrieved passages only)</p>
          </div>
        )}
      </div>
      {view && (
        <div className="sticky top-4 hidden h-[calc(100vh-2rem)] min-w-0 lg:block">
          <EvidenceViewer {...view} onClose={() => setView(null)} />
        </div>
      )}
    </div>
  );
}

export default function Page() {
  return <Suspense fallback={<p className="text-sm text-gray-500">Loading…</p>}><Inner /></Suspense>;
}
