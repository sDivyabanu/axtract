"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Suspense } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { rag, type RagDoc } from "@/lib/rag";

const TYPE_LABEL: Record<string, string> = {
  financial_statement: "Financial statement",
  cim: "CIM",
  contract: "Contract",
  debt_schedule: "Debt schedule",
  bank_statement: "Bank statement",
  presentation: "Presentation",
  spreadsheet: "Spreadsheet",
  other: "Document",
};

const STATUS_STYLE: Record<string, string> = {
  ready: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
  queued: "bg-gray-100 text-gray-700",
  parsing: "bg-amber-100 text-amber-800",
  indexing: "bg-amber-100 text-amber-800",
};

function DataRoomPageInner() {
  const { id } = useParams<{ id: string }>();
  const [docs, setDocs] = useState<RagDoc[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [rejected, setRejected] = useState<{ filename: string; message: string }[]>([]);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    rag.getWorkspace(id).then((w) => setDocs(w.documents)).catch((e) => setError(e.message));
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const working = docs.some((d) => ["queued", "parsing", "indexing"].includes(d.status));
  useEffect(() => {
    if (!working) return;
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, [working, load]);

  async function upload(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;
    setError(null);
    try {
      const r = await rag.upload(id, list);
      setRejected(r.rejected);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const readyCount = docs.filter((d) => d.status === "ready").length;

  return (
    <div>
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); upload(e.dataTransfer.files); }}
        className={`mb-5 flex flex-col items-center gap-2 rounded-lg border-2 border-dashed p-8 text-center ${
          dragging ? "border-blue-500 bg-blue-50" : "border-gray-300"
        }`}
      >
        <p className="font-medium">Drop the data room here</p>
        <p className="text-xs text-gray-500">PDF · DOCX · PPTX · XLSX · JPG · PNG — several files at once</p>
        <button type="button" onClick={() => input.current?.click()}
          className="mt-1 rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-700">
          Choose files
        </button>
        <input ref={input} type="file" multiple className="hidden"
          accept=".pdf,.docx,.pptx,.xlsx,.jpg,.jpeg,.png"
          onChange={(e) => { if (e.target.files) upload(e.target.files); e.target.value = ""; }} />
      </div>

      {error && <p role="alert" className="mb-3 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-700">{error}</p>}
      {rejected.length > 0 && (
        <ul className="mb-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
          {rejected.map((r) => <li key={r.filename}><b>{r.filename}</b>: {r.message}</li>)}
        </ul>
      )}

      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-500">{docs.length} documents · {readyCount} ready</h2>
        {readyCount > 0 && (
          <Link href={`/rooms/${id}/ask`} className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700">
            Ask a question →
          </Link>
        )}
      </div>

      {docs.length === 0 ? (
        <p className="rounded border border-dashed border-gray-300 p-6 text-center text-sm text-gray-500">No documents yet.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs text-gray-500">
              <tr>
                <th className="px-3 py-2">File</th><th className="px-3 py-2">Type</th><th className="px-3 py-2">Status</th>
                <th className="px-3 py-2 text-right">Pages</th><th className="px-3 py-2 text-right">Chunks</th>
                <th className="px-3 py-2 text-right">Flags</th><th className="px-3 py-2 text-right">Quarantined</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {docs.map((d) => (
                <tr key={d.doc_id}>
                  <td className="px-3 py-2 font-medium">{d.filename}</td>
                  <td className="px-3 py-2"><span className="rounded bg-gray-100 px-2 py-0.5 text-xs">{TYPE_LABEL[d.doc_type] ?? d.doc_type}</span></td>
                  <td className="px-3 py-2">
                    <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[d.status]}`}>
                      {d.status === "ready" || d.status === "failed" ? d.status : d.stage || d.status}
                    </span>
                    {["parsing", "indexing", "queued"].includes(d.status) && (
                      <div className="mt-1 h-1 w-28 overflow-hidden rounded bg-gray-100">
                        <div className="h-full bg-amber-400 transition-all" style={{ width: `${Math.round(d.progress * 100)}%` }} />
                      </div>
                    )}
                    {d.error && <div className="mt-1 text-xs text-red-600">{d.error}</div>}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{d.page_count || "—"}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{d.chunk_count || "—"}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {d.flag_count > 0 ? <span className="rounded bg-amber-100 px-1.5 text-amber-800">{d.flag_count}</span> : "—"}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {d.quarantined_count > 0 ? <span className="rounded bg-red-100 px-1.5 text-red-800">{d.quarantined_count}</span> : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function DataRoomPage() {
  return (
    <Suspense fallback={<p className="text-sm text-gray-500">Loading…</p>}>
      <DataRoomPageInner />
    </Suspense>
  );
}
