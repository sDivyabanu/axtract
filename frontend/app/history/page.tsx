"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Download, FolderOpen, Search, Trash2 } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import {
  deleteDocument,
  downloadDocument,
  listDocuments,
  type DocumentListItem,
} from "@/lib/api-authenticated";
import { fileKind, formatSize } from "@/components/FileDropzone";
import { statusChip } from "@/components/RecentDocuments";

type SortKey = "newest" | "oldest" | "name" | "size";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export default function HistoryPage() {
  const { user, loading: authLoading } = useAuth();
  const [docs, setDocs] = useState<DocumentListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("newest");
  const [status, setStatus] = useState<string>("all");

  useEffect(() => {
    if (authLoading || !user) return;
    loadDocuments();
  }, [authLoading, user]);

  async function loadDocuments() {
    setLoading(true);
    setError(null);
    try {
      const { documents } = await listDocuments();
      setDocs(documents);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load documents.");
    } finally {
      setLoading(false);
    }
  }

  async function handleDownload(doc: DocumentListItem) {
    try {
      const blob = await downloadDocument(doc.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = doc.original_filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      alert("Download failed.");
    }
  }

  async function handleDelete(doc: DocumentListItem) {
    if (!confirm(`Delete "${doc.original_filename}"? This cannot be undone.`)) return;
    try {
      await deleteDocument(doc.id);
      setDocs((prev) => prev.filter((d) => d.id !== doc.id));
    } catch {
      alert("Delete failed.");
    }
  }

  const statuses = useMemo(() => Array.from(new Set(docs.map((d) => d.status))), [docs]);
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = docs.filter(
      (d) => (status === "all" || d.status === status) && (!q || d.original_filename.toLowerCase().includes(q)),
    );
    const by: Record<SortKey, (a: DocumentListItem, b: DocumentListItem) => number> = {
      newest: (a, b) => b.created_at.localeCompare(a.created_at),
      oldest: (a, b) => a.created_at.localeCompare(b.created_at),
      name: (a, b) => a.original_filename.localeCompare(b.original_filename),
      size: (a, b) => b.file_size_bytes - a.file_size_bytes,
    };
    return [...list].sort(by[sort]);
  }, [docs, query, sort, status]);

  const shell = "mx-auto w-full max-w-6xl px-4 py-6 sm:px-6";

  if (authLoading) {
    return (
      <main className={shell} aria-busy="true">
        <div className="ax-skeleton h-10 w-64" />
      </main>
    );
  }

  if (!user) {
    return (
      <main className={shell}>
        <div className="rounded-xl border border-gray-200 bg-white p-8 text-center shadow-card">
          <FolderOpen size={32} className="mx-auto text-gray-400" aria-hidden />
          <p className="mt-3 text-sm text-gray-600">
            Please{" "}
            <Link href="/login" className="font-medium text-blue-700 underline">
              sign in
            </Link>{" "}
            to view your documents.
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className={shell}>
      <div className="mb-5">
        <h2 className="font-display text-2xl font-semibold text-gray-900">Your documents</h2>
        <p className="text-sm text-gray-500">
          {loading
            ? "Loading…"
            : `${docs.length} saved ${docs.length === 1 ? "document" : "documents"}. Open one to see its saved result without processing it again.`}
        </p>
      </div>

      {docs.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <label className="relative min-w-[200px] flex-1">
            <span className="sr-only">Search by file name</span>
            <Search size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" aria-hidden />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by file name"
              className="w-full rounded-full border border-gray-300 bg-white py-2 pl-10 pr-4 text-sm focus:border-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-100"
            />
          </label>
          {statuses.length > 1 && (
            <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status" className="rounded-full border border-gray-300 bg-white px-4 py-2 text-sm">
              <option value="all">All statuses</option>
              {statuses.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          )}
          <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} aria-label="Sort documents" className="rounded-full border border-gray-300 bg-white px-4 py-2 text-sm">
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
            <option value="name">Name</option>
            <option value="size">Largest</option>
          </select>
        </div>
      )}

      {loading && (
        <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading documents">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="ax-skeleton h-16 w-full" />
          ))}
        </div>
      )}
      {error && (
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}{" "}
          <button type="button" onClick={loadDocuments} className="font-medium underline">
            Retry
          </button>
        </div>
      )}

      {!loading && !error && docs.length === 0 && (
        <div className="rounded-xl border border-dashed border-gray-300 bg-white p-10 text-center">
          <FolderOpen size={32} className="mx-auto text-gray-400" aria-hidden />
          <p className="mt-3 font-medium text-gray-900">No documents yet</p>
          <p className="text-sm text-gray-500">Documents you process while signed in are saved here.</p>
          <Link href="/" className="mt-4 inline-block rounded-full bg-blue-600 px-5 py-2 text-sm font-medium text-white hover:bg-blue-700">
            Start an extraction
          </Link>
        </div>
      )}

      {!loading && docs.length > 0 && visible.length === 0 && (
        <p className="rounded-lg border border-gray-200 bg-white p-6 text-center text-sm text-gray-500">No documents match your search.</p>
      )}

      {visible.length > 0 && (
        <ul className="flex flex-col gap-2">
          {visible.map((doc) => {
            const k = fileKind(doc.original_filename);
            return (
              <li key={doc.id} className="ax-rise flex flex-wrap items-center gap-3 rounded-xl border border-gray-200 bg-white p-3 shadow-card transition-shadow hover:shadow-lift sm:p-4">
                <span className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-lg ${k.cls}`} aria-hidden>
                  <k.Icon size={22} />
                </span>
                <Link href={`/?doc=${doc.id}`} className="min-w-0 flex-1 basis-48">
                  <span className="block truncate font-medium text-gray-900 hover:text-blue-700">{doc.original_filename}</span>
                  <span className="block text-xs text-gray-500">
                    {k.label} · {formatSize(doc.file_size_bytes)} · {doc.block_count ?? "—"} blocks · {formatDate(doc.created_at)}
                  </span>
                </Link>
                <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${statusChip(doc.status)}`}>{doc.status}</span>
                <div className="flex items-center gap-1">
                  <Link href={`/?doc=${doc.id}`} className="rounded-full px-3 py-1.5 text-sm font-medium text-blue-700 hover:bg-blue-50">
                    Open
                  </Link>
                  <button type="button" onClick={() => handleDownload(doc)} aria-label={`Download ${doc.original_filename}`} title="Download original" className="rounded-full p-2 text-gray-600 hover:bg-gray-100">
                    <Download size={17} />
                  </button>
                  <button type="button" onClick={() => handleDelete(doc)} aria-label={`Delete ${doc.original_filename}`} title="Delete" className="rounded-full p-2 text-red-600 hover:bg-red-50">
                    <Trash2 size={17} />
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </main>
  );
}
