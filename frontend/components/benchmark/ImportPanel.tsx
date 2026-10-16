"use client";

import { useEffect, useState } from "react";
import { importBenchmark, listImportable, type ImportSource } from "@/lib/benchmark";
import { btnPrimary, card, Empty, ErrorBanner, errorText } from "./ui";

export default function ImportPanel({ onImported }: { onImported: (runId: string) => void }) {
  const [sources, setSources] = useState<ImportSource[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    let stop = false;
    listImportable().then((r) => { if (!stop) setSources(r.sources); }).catch((e) => { if (!stop) setError(errorText(e, "Could not list importable runs.")); });
    return () => { stop = true; };
  }, []);

  async function run(source: string) {
    setBusy(source);
    try {
      const r = await importBenchmark(source);
      setNote(r.already_imported ? "That run was already imported." : "Imported. Nothing was re-run.");
      setError(null);
      onImported(r.run_id);
    } catch (e) {
      setError(errorText(e, "Import failed."));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className={`${card} flex flex-col gap-3`} aria-labelledby="bench-import">
      <h3 id="bench-import" className="font-medium text-gray-900">Import a finished official run</h3>
      <p className="text-sm text-gray-600">
        Reads the saved results of an earlier official benchmark without re-running anything. The original verdict is kept: a run the official
        scorer rejected stays invalid, and its percentages are shown as diagnostic only.
      </p>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {note && <p className="text-sm text-green-700" role="status">{note}</p>}
      {sources === null ? <div className="ax-skeleton h-12 w-full" /> : sources.length === 0 ? <Empty>No official run artifacts were found on this server.</Empty> : (
        <ul className="flex flex-col gap-2">
          {sources.map((s) => (
            <li key={s.source} className="flex flex-wrap items-center gap-3 rounded-lg border border-gray-200 p-3 text-sm">
              <span className="min-w-0 flex-1">
                <span className="block break-all font-medium">{s.source}</span>
                <span className="block text-xs text-gray-600">
                  Official status: <strong className={s.official_status?.startsWith("FAILED") ? "text-red-700" : ""}>{s.official_status ?? "unknown"}</strong>
                  {s.source_commit ? ` · commit ${s.source_commit.slice(0, 7)}` : ""}{s.cases ? ` · ${s.cases.toLocaleString()} cases` : ""}
                </span>
              </span>
              <button type="button" className={btnPrimary} disabled={busy !== null} onClick={() => run(s.source)}>{busy === s.source ? "Importing…" : "Import"}</button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
