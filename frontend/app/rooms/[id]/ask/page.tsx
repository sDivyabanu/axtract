"use client";

import { useParams } from "next/navigation";
import { Suspense } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import AnswerCard from "@/components/rag/AnswerCard";
import EvidenceViewer, { type Highlight } from "@/components/EvidenceViewer";
import { askStream, rag, type Answer, type Citation, type DocSuggestions, type Operand, type RagDoc } from "@/lib/rag";

interface Turn {
  id: number;
  question: string;
  stages: { name: string; status: "start" | "done"; ms?: number }[];
  draft: string;
  answer?: Answer;
  error?: string;
}

interface ViewerState {
  docId: string;
  filename: string;
  pageCount: number;
  highlights: Highlight[];
  page?: number;
}

function AskPageInner() {
  const { id } = useParams<{ id: string }>();
  const [docs, setDocs] = useState<RagDoc[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [suggestions, setSuggestions] = useState<DocSuggestions[]>([]);
  const [viewer, setViewer] = useState<ViewerState | null>(null);
  const [scope, setScope] = useState<Set<string>>(new Set());
  const bottom = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);

  useEffect(() => {
    rag.getWorkspace(id).then((w) => setDocs(w.documents)).catch(() => setDocs([]));
  }, [id]);
  useEffect(() => {
    rag.get<DocSuggestions[]>(`/workspaces/${id}/suggestions`).then(setSuggestions).catch(() => setSuggestions([]));
  }, [id, docs.length]);
  const byId = useMemo(() => new Map(docs.map((d) => [d.doc_id, d])), [docs]);
  const readyDocs = docs.filter((d) => d.status === "ready");

  function openCitation(c: Citation) {
    const boxes = c.bboxes.filter((b) => b.bbox);
    setViewer({
      docId: c.doc_id,
      filename: c.filename,
      pageCount: byId.get(c.doc_id)?.preview_pages ?? c.preview_pages,
      highlights: boxes.map((b) => ({ page: b.page, bbox: b.bbox, label: `[${c.n}]` })),
      page: boxes[0]?.page ?? c.pages[0],
    });
  }

  function openOperand(o: Operand) {
    setViewer({
      docId: o.doc_id,
      filename: o.filename,
      pageCount: byId.get(o.doc_id)?.preview_pages ?? 1,
      highlights: [{ page: o.page, bbox: o.bbox, label: `${o.label} ${o.display}` }],
      page: o.page,
    });
  }

  async function ask(q: string) {
    if (!q.trim() || busy) return;
    const turnId = nextId.current++;
    setTurns((t) => [...t, { id: turnId, question: q, stages: [], draft: "" }]);
    setQuestion("");
    setBusy(true);
    const patch = (fn: (t: Turn) => Turn) => setTurns((all) => all.map((t) => (t.id === turnId ? fn(t) : t)));
    try {
      const final = await askStream(
        id,
        { question: q, doc_ids: scope.size ? Array.from(scope) : null, mode: "dealLens" },
        (e) => {
          if (e.event === "stage" && e.name) {
            patch((t) => ({
              ...t,
              stages: [...t.stages.filter((s) => s.name !== e.name), { name: e.name, status: e.status, ms: e.ms }],
            }));
          } else if (e.event === "token") {
            patch((t) => ({ ...t, draft: t.draft + e.text }));
          } else if (e.event === "error") {
            patch((t) => ({ ...t, error: e.message }));
          }
        },
      );
      if (final) patch((t) => ({ ...t, answer: final, draft: "" }));
    } catch (e) {
      patch((t) => ({ ...t, error: (e as Error).message }));
    } finally {
      setBusy(false);
      setTimeout(() => bottom.current?.scrollIntoView({ behavior: "smooth" }), 50);
    }
  }

  return (
    <div className={`ax-rise ${viewer ? "grid grid-cols-1 gap-4 lg:grid-cols-2" : "mx-auto max-w-3xl"}`}>
      <div className="min-w-0">
        {readyDocs.length === 0 && (
          <p className="mb-4 rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
            No documents are ready yet. Upload files in the Data Room tab and wait for them to finish indexing.
          </p>
        )}
        {turns.length === 0 && suggestions.length > 0 && (
          <div data-suggestions className="mb-4 space-y-3">
            <div className="text-xs font-semibold uppercase tracking-wider text-gray-500">Suggested questions</div>
            {suggestions.map((d) => (
              <div key={d.doc_id}>
                <div className="mb-1 text-xs text-gray-500">{d.filename}</div>
                <div className="flex flex-wrap gap-1.5">
                  {d.suggestions.map((q) => (
                    <button key={q.question} type="button" onClick={() => ask(q.question)} disabled={busy}
                      className="rounded-full border border-gray-200 bg-white px-3 py-1 text-left text-xs text-gray-700 shadow-card hover:shadow-lift hover:border-blue-300 transition disabled:opacity-50">
                      {q.question}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="space-y-5">
          {turns.map((t) => (
            <div key={t.id}>
              <div className="mb-2 flex justify-end">
                <div className="max-w-[85%] rounded-2xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white shadow-card">{t.question}</div>
              </div>
              {t.error && <p role="alert" className="rounded-xl border border-red-300 bg-red-50 p-3 text-sm text-red-700">{t.error}</p>}
              {!t.answer && !t.error && (
                <div className="rounded-2xl border border-gray-200 bg-white p-4 shadow-card">
                  <div className="mb-2 flex flex-wrap gap-1.5 text-xs">
                    {t.stages.map((s) => (
                      <span key={s.name} className={`rounded-full px-2.5 py-0.5 font-medium ${s.status === "done" ? "bg-green-100 text-green-800" : "animate-pulse bg-amber-100 text-amber-800"}`}>
                        {s.status === "done" ? "✓" : "…"} {s.name}{s.ms != null ? ` ${s.ms} ms` : ""}
                      </span>
                    ))}
                  </div>
                  {t.draft && <p className="whitespace-pre-wrap text-[15px] leading-7 text-gray-700">{t.draft}</p>}
                </div>
              )}
              {t.answer && <AnswerCard answer={t.answer} onSelectCitation={openCitation} onSelectOperand={openOperand} />}
            </div>
          ))}
        </div>

        <div ref={bottom} />
        <div className="sticky bottom-0 mt-5 rounded-2xl bg-white/90 pb-3 pt-3 backdrop-blur">
          {readyDocs.length > 1 && (
            <details className="mb-2 text-xs text-gray-600">
              <summary className="cursor-pointer">
                Search in: {scope.size === 0 ? "all documents" : `${scope.size} selected`}
              </summary>
              <div className="mt-1 flex flex-wrap gap-2">
                {readyDocs.map((d) => (
                  <label key={d.doc_id} className="flex items-center gap-1 rounded-xl border border-gray-200 bg-white px-2 py-1 shadow-card">
                    <input type="checkbox" checked={scope.has(d.doc_id)}
                      onChange={() => setScope((s) => { const n = new Set(s); if (n.has(d.doc_id)) n.delete(d.doc_id); else n.add(d.doc_id); return n; })} />
                    {d.filename}
                  </label>
                ))}
              </div>
            </details>
          )}
          <div className="flex gap-2">
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(question); } }}
              rows={2}
              placeholder="Ask about the data room…  e.g. What is total debt maturing in 2026?"
              className="flex-1 resize-none rounded-xl border border-gray-300 bg-white px-4 py-2.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
            />
            <button type="button" onClick={() => ask(question)} disabled={busy || !question.trim() || readyDocs.length === 0}
              className="self-end rounded-full bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-card hover:bg-blue-700 disabled:opacity-50">
              {busy ? "…" : "Ask"}
            </button>
          </div>
        </div>
      </div>

      {viewer && (
        <div className="sticky top-4 hidden h-[calc(100vh-2rem)] min-w-0 lg:block">
          <EvidenceViewer {...viewer} workspaceId={id} onClose={() => setViewer(null)} />
        </div>
      )}
    </div>
  );
}

export default function AskPage() {
  return (
    <Suspense fallback={<p className="text-sm text-gray-500">Loading…</p>}>
      <AskPageInner />
    </Suspense>
  );
}
