"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { rag, type Workspace } from "@/lib/rag";

export default function RoomsPage() {
  const [rooms, setRooms] = useState<Workspace[] | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    rag.listWorkspaces().then(setRooms).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function create() {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await rag.createWorkspace(name.trim());
      setName("");
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-3xl p-6">
      <h1 className="text-2xl font-bold tracking-tight">DealLens</h1>
      <p className="mb-6 mt-1 text-sm text-gray-600">
        Create a data room, drop in the documents, and ask questions. Every answer links back to the exact place in the
        source and shows the arithmetic behind each number.
      </p>

      <div className="mb-6 flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && create()}
          placeholder="New data room name, e.g. Project Falcon"
          className="flex-1 rounded border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
        />
        <button type="button" onClick={create} disabled={busy || !name.trim()}
          className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-700 disabled:opacity-50">
          Create
        </button>
      </div>
      {error && <p role="alert" className="mb-4 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-700">{error}</p>}

      <h2 className="mb-2 text-sm font-semibold text-gray-500">Data rooms</h2>
      {rooms === null ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : rooms.length === 0 ? (
        <p className="rounded border border-dashed border-gray-300 p-6 text-center text-sm text-gray-500">No data rooms yet.</p>
      ) : (
        <ul className="divide-y divide-gray-200 rounded-lg border border-gray-200">
          {rooms.map((r) => (
            <li key={r.workspace_id}>
              <Link href={`/rooms/${r.workspace_id}`} className="flex items-center justify-between p-4 hover:bg-gray-50">
                <span className="font-medium">{r.name}</span>
                <span className="text-xs text-gray-500">{typeof r.documents === "number" ? r.documents : 0} documents</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
