"use client";

import { useParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { rag, type AuditEvent } from "@/lib/rag";

function AuditInner() {
  const { id } = useParams<{ id: string }>();
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");

  useEffect(() => {
    rag.get<AuditEvent[]>(`/workspaces/${id}/audit`).then(setEvents).catch((e: Error) => setError(e.message));
  }, [id]);

  if (error) return <p role="alert" className="text-sm text-red-700">{error}</p>;
  if (!events) return <p className="text-sm text-gray-500">Loading…</p>;
  const kinds = Array.from(new Set(events.map((e) => e.event))).sort();
  const shown = filter ? events.filter((e) => e.event === filter) : events;
  return (
    <div>
      <p className="mb-3 text-sm text-gray-600">
        Who did what, and when. Entries hold ids, hashes and timings only — never document text.
      </p>
      <div className="mb-3 flex flex-wrap gap-1.5 text-xs">
        <button type="button" onClick={() => setFilter("")} className={`rounded-full border px-2.5 py-1 ${filter === "" ? "bg-gray-900 text-white" : "bg-white"}`}>all ({events.length})</button>
        {kinds.map((k) => (
          <button key={k} type="button" onClick={() => setFilter(k)} className={`rounded-full border px-2.5 py-1 ${filter === k ? "bg-gray-900 text-white" : "bg-white"}`}>{k}</button>
        ))}
      </div>
      {shown.length === 0 ? (
        <p className="text-sm text-gray-500">No events yet.</p>
      ) : (
        <table className="w-full text-left text-xs">
          <thead className="border-b border-gray-200 text-gray-500">
            <tr><th className="py-1.5 pr-3">Time</th><th className="pr-3">Event</th><th className="pr-3">Reference</th><th>Detail</th></tr>
          </thead>
          <tbody>
            {shown.map((e) => (
              <tr key={e.id} data-audit-row className="border-b border-gray-100 align-top">
                <td className="whitespace-nowrap py-1.5 pr-3 text-gray-500">{new Date(e.ts * 1000).toLocaleString()}</td>
                <td className="pr-3 font-medium">{e.event}</td>
                <td className="pr-3 font-mono text-[11px] text-gray-600">{e.ref ? e.ref.slice(0, 12) : ""}</td>
                <td className="font-mono text-[11px] text-gray-600">{Object.entries(e.detail).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join("  ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function AuditPage() {
  return (
    <Suspense fallback={<p className="text-sm text-gray-500">Loading…</p>}>
      <AuditInner />
    </Suspense>
  );
}
