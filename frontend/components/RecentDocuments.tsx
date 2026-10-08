"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, History } from "lucide-react";
import { listDocuments, type DocumentListItem } from "@/lib/api-authenticated";
import { fileKind } from "@/components/FileDropzone";

export function statusChip(status: string): string {
  return status === "processed"
    ? "bg-green-50 text-green-700"
    : status === "failed"
      ? "bg-red-50 text-red-700"
      : "bg-amber-50 text-amber-700";
}

/** Real data only: the signed-in user's most recent saved documents. Renders nothing if the list is unavailable. */
export default function RecentDocuments() {
  const [docs, setDocs] = useState<DocumentListItem[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    listDocuments(5)
      .then((r) => !cancelled && setDocs(r.documents))
      .catch(() => !cancelled && setDocs([]));
    return () => {
      cancelled = true;
    };
  }, []);

  if (docs === null) {
    return (
      <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading recent documents">
        <div className="ax-skeleton h-5 w-40" />
        <div className="ax-skeleton h-14 w-full" />
        <div className="ax-skeleton h-14 w-full" />
      </div>
    );
  }
  if (docs.length === 0) return null;

  return (
    <section aria-label="Recent documents" className="ax-rise">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="flex items-center gap-2 font-display text-base font-semibold text-gray-900">
          <History size={18} className="text-gray-500" aria-hidden /> Recent documents
        </h2>
        <Link href="/history" className="inline-flex items-center gap-1 text-sm font-medium text-blue-700 hover:underline">
          View library <ArrowRight size={14} />
        </Link>
      </div>
      <ul className="grid gap-2 md:grid-cols-2">
        {docs.map((d) => {
          const k = fileKind(d.original_filename);
          return (
            <li key={d.id}>
              <Link
                href={`/workspace?doc=${d.id}`}
                className="flex items-center gap-3 rounded-lg border border-gray-200 bg-white p-3 shadow-card transition-all hover:border-blue-300 hover:shadow-lift"
              >
                <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${k.cls}`} aria-hidden>
                  <k.Icon size={20} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-gray-900">{d.original_filename}</span>
                  <span className="block text-xs text-gray-500">
                    {new Date(d.created_at).toLocaleDateString()} · {d.block_count ?? "—"} blocks
                  </span>
                </span>
                <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${statusChip(d.status)}`}>{d.status}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
