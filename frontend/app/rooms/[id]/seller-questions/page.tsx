"use client";

import { useParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import EvidenceViewer, { type Highlight } from "@/components/EvidenceViewer";
import { API_BASE_URL } from "@/lib/api";
import { rag, SEVERITY_STYLE, type Evidence, type SellerItem } from "@/lib/rag";

interface View { docId: string; filename: string; pageCount: number; highlights: Highlight[]; page?: number }

function Inner() {
  const { id } = useParams<{ id: string }>();
  const [items, setItems] = useState<SellerItem[] | null>(null);
  const [view, setView] = useState<View | null>(null);

  useEffect(() => { rag.get<SellerItem[]>(`/workspaces/${id}/seller-questions`).then(setItems).catch(() => setItems([])); }, [id]);

  function open(e: Evidence) {
    if (!e.page) return;
    setView({ docId: e.doc_id, filename: e.filename, pageCount: e.preview_pages, page: e.page, highlights: [{ page: e.page, bbox: e.bbox, label: e.label }] });
  }
  const edit = (i: number, text: string) => setItems((all) => all && all.map((x, k) => (k === i ? { ...x, question: text } : x)));
  const remove = (i: number) => setItems((all) => all && all.filter((_, k) => k !== i).map((x, k) => ({ ...x, n: k + 1 })));

  async function download(format: "docx" | "csv" | "md") {
    const res = await fetch(`${API_BASE_URL}/api/workspaces/${id}/seller-questions/export`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ format, items }),
    });
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = `seller-questions.${format}`; a.click();
    URL.revokeObjectURL(a.href);
  }

  if (items === null) return <p className="text-sm text-gray-500">Drafting questions from the findings…</p>;
  return (
    <div className={view ? "grid grid-cols-1 gap-4 lg:grid-cols-2" : ""}>
      <div>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <p className="text-sm text-gray-600">
            {items.length} questions drafted from contradictions, totals that do not add up, missing schedules, hardcoded values, hidden or injected content and
            unanswered pack topics. Edit freely, then export.
          </p>
          <span className="ml-auto flex gap-2 text-xs">
            {(["docx", "csv", "md"] as const).map((f) => (
              <button key={f} type="button" onClick={() => download(f)} className="rounded border border-gray-300 px-2 py-1 hover:bg-gray-50">Export {f.toUpperCase()}</button>
            ))}
          </span>
        </div>
        <ol className="space-y-3" data-seller>
          {items.map((it, i) => (
            <li key={it.id} className="rounded-lg border border-gray-200 bg-white p-3">
              <div className="mb-1 flex items-center gap-2 text-xs">
                <span className="font-bold text-gray-500">{it.n}.</span>
                <span className={`rounded px-2 py-0.5 font-semibold uppercase ${SEVERITY_STYLE[it.severity]}`}>{it.severity}</span>
                <span className="text-gray-400">{it.kind.replace("_", " ")}</span>
                <button type="button" onClick={() => remove(i)} className="ml-auto text-gray-400 hover:text-red-600" aria-label="Delete question">✕</button>
              </div>
              <textarea value={it.question} onChange={(e) => edit(i, e.target.value)} rows={3}
                className="w-full resize-y rounded border border-gray-200 p-2 text-sm focus:border-gray-500 focus:outline-none" />
              <p className="mt-1 text-xs text-gray-500">Why: {it.why}</p>
              {it.evidence.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {it.evidence.map((e, k) => (
                    <button key={k} type="button" onClick={() => open(e)} disabled={!e.page}
                      className="rounded bg-blue-50 px-2 py-0.5 text-xs text-blue-800 hover:bg-blue-100 disabled:opacity-50">
                      {e.filename}{e.page ? ` p.${e.page}` : ""}
                    </button>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ol>
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
