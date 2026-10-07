"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth-context";
import {
  deleteDocument,
  downloadDocument,
  listDocuments,
  type DocumentListItem,
} from "@/lib/api-authenticated";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

export default function HistoryPage() {
  const { user, loading: authLoading } = useAuth();
  const [docs, setDocs] = useState<DocumentListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
    if (!confirm(`Delete "${doc.original_filename}"? This cannot be undone.`))
      return;
    try {
      await deleteDocument(doc.id);
      setDocs((prev) => prev.filter((d) => d.id !== doc.id));
    } catch {
      alert("Delete failed.");
    }
  }

  if (authLoading) {
    return (
      <main className="mx-auto max-w-4xl p-6">
        <p className="text-sm text-gray-500">Loading...</p>
      </main>
    );
  }

  if (!user) {
    return (
      <main className="mx-auto max-w-4xl p-6">
        <p className="text-sm text-gray-500">
          Please{" "}
          <Link href="/login" className="text-blue-600 underline">
            sign in
          </Link>{" "}
          to view your documents.
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-4xl p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Document History</h1>
          <p className="text-xs text-gray-500 mt-0.5">
            Previously processed documents
          </p>
        </div>
        <Link
          href="/"
          className="rounded bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-700"
        >
          New Document
        </Link>
      </div>

      {loading && <p className="text-sm text-gray-500">Loading documents...</p>}
      {error && (
        <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-600">
          {error}
        </div>
      )}

      {!loading && docs.length === 0 && (
        <p className="text-sm text-gray-500">
          No documents yet. Upload one from the{" "}
          <Link href="/" className="text-blue-600 underline">
            main page
          </Link>
          .
        </p>
      )}

      {docs.length > 0 && (
        <div className="border border-gray-200 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 text-left text-xs text-gray-500">
                <th className="px-4 py-2.5 font-medium">Document</th>
                <th className="px-4 py-2.5 font-medium">Size</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium">Blocks</th>
                <th className="px-4 py-2.5 font-medium">Date</th>
                <th className="px-4 py-2.5 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {docs.map((doc) => (
                <tr key={doc.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <Link
                      href={`/?doc=${doc.id}`}
                      className="text-blue-600 hover:underline font-medium"
                    >
                      {doc.original_filename}
                    </Link>
                    <div className="text-xs text-gray-400 mt-0.5">
                      {doc.mime_type}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-gray-600">
                    {formatBytes(doc.file_size_bytes)}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${
                        doc.status === "processed"
                          ? "bg-green-100 text-green-700"
                          : doc.status === "failed"
                            ? "bg-red-100 text-red-700"
                            : "bg-yellow-100 text-yellow-700"
                      }`}
                    >
                      {doc.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-600">
                    {doc.block_count ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs">
                    {formatDate(doc.created_at)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => handleDownload(doc)}
                        className="text-xs text-blue-600 hover:underline"
                      >
                        Download
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDelete(doc)}
                        className="text-xs text-red-600 hover:underline"
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
