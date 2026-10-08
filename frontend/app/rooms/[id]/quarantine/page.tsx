"use client";

import { useParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import EvidenceViewer from "@/components/EvidenceViewer";
import { rag, type QuarantineItem } from "@/lib/rag";

function kindOf(reason: string): { label: string; cls: string } {
  if (reason.startsWith("hidden_")) return { label: "hidden content", cls: "bg-red-100 text-red-800" };
  if (reason.startsWith("pdf_") || reason.startsWith("office_")) return { label: "active content · not executed", cls: "bg-amber-100 text-amber-800" };
  if (reason === "homoglyph_suspected" || reason === "zero_width_characters" || reason === "bidi_override")
    return { label: "unicode trick", cls: "bg-amber-100 text-amber-800" };
  return { label: "instruction aimed at an AI", cls: "bg-red-100 text-red-800" };
}

function QuarantineInner() {
  const { id } = useParams<{ id: string }>();
  const [items, setItems] = useState<QuarantineItem[] | null>(null);
  const [sel, setSel] = useState<QuarantineItem | null>(null);

  useEffect(() => { rag.get<QuarantineItem[]>(`/workspaces/${id}/quarantine`).then(setItems).catch(() => setItems([])); }, [id]);

  if (items === null) return <p className="text-sm text-gray-500">Loading…</p>;
  return (
    <div className={`ax-rise ${sel ? "grid grid-cols-1 gap-4 lg:grid-cols-2" : ""}`}>
      <div>
        <p className="mb-3 text-sm text-gray-600">
          Content that could manipulate an AI model, or that a human reader cannot see. It is <b>excluded from retrieval</b> and
          never reaches a prompt. Nothing is deleted and nothing is executed.
        </p>
        {items.length === 0 ? (
          <p className="rounded-2xl border border-dashed border-gray-300 bg-white p-6 text-center text-sm text-gray-500 shadow-card">Nothing quarantined in this data room.</p>
        ) : (
          <ul className="space-y-2" data-quarantine>
            {items.map((it) => {
              const k = kindOf(it.reason);
              return (
                <li key={it.id} className="rounded-2xl border border-red-200 bg-red-50/40 p-4 text-sm shadow-card">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${k.cls}`}>{k.label}</span>
                    <b>{it.reason_label}</b>
                    <span className="text-xs text-gray-500">{it.filename}{it.page ? ` · p.${it.page}` : ""}</span>
                    {it.page && it.preview_pages > 0 && it.reason !== "hidden_sheet" && (
                      <button type="button" onClick={() => setSel(it)} className="ml-auto rounded-full border border-gray-200 bg-white px-2.5 py-0.5 text-xs shadow-card hover:shadow-lift transition">
                        Show in document
                      </button>
                    )}
                  </div>
                  <pre className="mt-2 whitespace-pre-wrap rounded-xl bg-white p-3 font-mono text-xs text-red-800">{it.snippet}</pre>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      {sel && sel.page && (
        <div className="sticky top-4 hidden h-[calc(100vh-2rem)] lg:block">
          <EvidenceViewer docId={sel.doc_id} filename={sel.filename} pageCount={sel.preview_pages} page={sel.page}
            highlights={[{ page: sel.page, bbox: sel.bbox, label: sel.reason_label }]} onClose={() => setSel(null)} />
        </div>
      )}
    </div>
  );
}

export default function QuarantinePage() {
  return (
    <Suspense fallback={<p className="text-sm text-gray-500">Loading…</p>}>
      <QuarantineInner />
    </Suspense>
  );
}
