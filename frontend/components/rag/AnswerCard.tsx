"use client";

import { useState } from "react";
import { API_BASE_URL } from "@/lib/api";
import type { Answer, Citation, Operand } from "@/lib/rag";
import CropPreview from "./CropPreview";
import GlassBox from "./GlassBox";
import ReceiptCard from "./ReceiptCard";

const LEVEL: Record<string, string> = {
  amber: "bg-amber-100 text-amber-800",
  red: "bg-red-100 text-red-800",
  green: "bg-green-100 text-green-800",
};

function CitationChip({ c, onSelect }: { c: Citation; onSelect: (c: Citation) => void }) {
  const [hover, setHover] = useState(false);
  const first = c.bboxes.find((b) => b.bbox);
  const flagged = c.badges.length > 0;
  return (
    <span className="relative inline-block" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      <button type="button" onClick={() => onSelect(c)}
        className={`mx-0.5 rounded px-1.5 py-0.5 align-baseline text-xs font-semibold ${
          flagged ? "bg-amber-100 text-amber-900 hover:bg-amber-200" : "bg-blue-100 text-blue-800 hover:bg-blue-200"}`}
        title={`${c.filename}, page ${c.pages.join(", ")}`}>
        {c.n}
      </button>
      {hover && first?.bbox && c.preview_pages > 0 && (
        <span className="absolute left-0 top-full z-30 mt-1 block">
          <CropPreview docId={c.doc_id} page={first.page} bbox={first.bbox} />
        </span>
      )}
    </span>
  );
}

export default function AnswerCard({
  answer, onSelectCitation, onSelectOperand,
}: {
  answer: Answer;
  onSelectCitation: (c: Citation) => void;
  onSelectOperand: (o: Operand) => void;
}) {
  const byN = new Map(answer.citations.map((c) => [c.n, c]));
  const g = answer.grounding;
  const allVerified = g != null && g.verified === g.total;
  const [packBusy, setPackBusy] = useState(false);
  const [packSha, setPackSha] = useState<string | null>(null);

  async function downloadPack() {
    setPackBusy(true);
    try {
      const res = await fetch(`${API_BASE_URL}/api/answers/${answer.answer_id}/evidence-pack`, { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setPackSha(res.headers.get("X-Evidence-Pack-SHA256"));
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `evidence-pack-${answer.answer_id.slice(0, 8)}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setPackSha("failed");
    } finally {
      setPackBusy(false);
    }
  }

  return (
    <div data-answer className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded bg-gray-100 px-2 py-0.5 text-gray-600">route: {answer.route}</span>
        <span className={`rounded px-2 py-0.5 ${answer.mode === "extractive" ? "bg-amber-100 text-amber-800" : "bg-green-100 text-green-800"}`}>
          {answer.mode === "llm" ? `LLM · ${answer.model}` : answer.mode === "computed" ? "Computed by table engine · no LLM arithmetic" : "LLM offline – extractive mode"}
        </span>
        {g && (
          <span className={`rounded px-2 py-0.5 font-medium ${allVerified ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>
            Grounding {g.verified}/{g.total} claims verified
          </span>
        )}
        {answer.total_ms != null && <span className="text-gray-400">{(answer.total_ms / 1000).toFixed(1)} s</span>}
      </div>

      {answer.refusal ? (
        <div className="rounded border border-amber-300 bg-amber-50 p-3">
          <div className="text-base font-semibold text-amber-900">DealLens refused to compute this</div>
          <p className="mt-1 text-sm text-amber-900">{answer.refusal}</p>
          <p className="mt-1 text-xs text-amber-800">Mixing incompatible units would produce a wrong number, so no number is shown.</p>
        </div>
      ) : answer.abstained ? (
        <div className="rounded border border-gray-200 bg-gray-50 p-3">
          <div className="text-base font-semibold text-gray-800">Not found in this data room</div>
          <p className="mt-1 text-sm text-gray-600">
            DealLens will not guess. {answer.abstain_reason === "no_documents" ? "No documents are indexed yet." : "The retrieved passages do not answer the question."}
          </p>
          {answer.searched?.note && <p className="mt-2 rounded bg-amber-50 p-2 text-sm text-amber-900">⚠ {answer.searched.note}</p>}
          {answer.searched && (
            <div className="mt-2 text-xs text-gray-600">
              <div><b>Searched:</b> {answer.searched.chunks_searched} passages in {answer.searched.documents.length} documents ({answer.searched.documents.join(", ")})</div>
              {answer.searched.closest_sections.length > 0 && (
                <div><b>Closest sections:</b> {answer.searched.closest_sections.map((s) => `${s.filename} p.${s.pages.join(",")}`).join(" · ")}</div>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="text-[15px] leading-7 text-gray-900">
          {answer.sentences.map((s, i) => (
            <span key={i}>
              <span className={s.verified === false ? "underline decoration-red-500 decoration-wavy underline-offset-4" : ""}
                title={s.verified === false ? `Unverified: ${s.reason ?? ""}` : undefined}>
                {s.text}
              </span>
              {s.citations.map((n) => byN.get(n)).filter((c): c is Citation => !!c).map((c) => (
                <CitationChip key={c.n} c={c} onSelect={onSelectCitation} />
              ))}{" "}
            </span>
          ))}
        </div>
      )}

      {answer.excluded_sources && answer.excluded_sources.length > 0 && (
        <div className="mt-2 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-800">
          ⚠ {answer.excluded_sources.length} source{answer.excluded_sources.length > 1 ? "s" : ""} excluded:{" "}
          {answer.excluded_sources.map((e) => `${e.reason}${e.page ? ` on p.${e.page}` : ""} (${e.filename})`).join("; ")}
        </div>
      )}

      {answer.receipts.length > 0 && (
        <div className="mt-3 space-y-2">
          {answer.receipts.map((r) => <ReceiptCard key={r.id} receipt={r} onOperand={onSelectOperand} />)}
        </div>
      )}

      {answer.badges.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {answer.badges.map((b) => (
            <span key={b.label} className={`rounded px-2 py-0.5 text-xs font-medium ${LEVEL[b.level]}`}>⚠ {b.label}</span>
          ))}
        </div>
      )}

      {answer.citations.length > 0 && (
        <div className="mt-3 border-t border-gray-100 pt-2">
          <div className="mb-1 text-xs font-semibold text-gray-500">Sources</div>
          <ul className="space-y-1 text-xs text-gray-700">
            {answer.citations.map((c) => (
              <li key={c.n} className="flex items-start gap-2">
                <button type="button" onClick={() => onSelectCitation(c)} className="rounded bg-blue-100 px-1.5 font-semibold text-blue-800 hover:bg-blue-200">{c.n}</button>
                <span className="min-w-0">
                  <b>{c.filename}</b> · p.{c.pages.join(", ")}
                  {c.printed_pages.length > 0 && <span className="text-gray-500"> (printed {c.printed_pages.join(", ")})</span>}
                  {c.heading_path.length > 0 && <span className="text-gray-500"> · {c.heading_path.join(" › ")}</span>}
                  {c.badges.map((b) => <span key={b.label} className={`ml-1 rounded px-1 ${LEVEL[b.level]}`}>{b.label}</span>)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {!answer.pipeline && (
        <div className="mt-3 flex items-center gap-2 text-xs">
          <button type="button" onClick={downloadPack} disabled={packBusy}
            className="rounded border border-gray-300 px-2 py-1 font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50">
            {packBusy ? "Building…" : "Export Evidence Pack (PDF)"}
          </button>
          {packSha && <span className="text-gray-500">{packSha === "failed" ? "Export failed" : `SHA-256 ${packSha.slice(0, 16)}…`}</span>}
        </div>
      )}
      <GlassBox answer={answer} />
    </div>
  );
}
