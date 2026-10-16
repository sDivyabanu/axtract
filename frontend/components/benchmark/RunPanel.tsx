"use client";

import { useState } from "react";
import { RotateCcw, Square } from "lucide-react";
import { getBenchLog, type BenchRunStatus } from "@/lib/benchmark";
import { fmtDuration, isActive, processed, progressPercent, shortId } from "@/lib/benchmark-utils";
import { btn, btnPrimary, card, errorText, ScoreBadge } from "./ui";

interface Props {
  run: BenchRunStatus;
  busy: boolean;
  onCancel: () => void;
  onResume: () => void;
}

const STATUS_TEXT: Record<string, string> = {
  created: "Starting",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  interrupted: "Interrupted",
  imported: "Imported",
};

export default function RunPanel({ run, busy, onCancel, onResume }: Props) {
  const [log, setLog] = useState<string | null>(null);
  const [logError, setLogError] = useState<string | null>(null);
  const c = run.counts;
  const active = isActive(run.status);
  const cancelling = active && run.cancel_requested;
  const phase = run.phase && active ? `, ${run.phase}` : "";

  async function showLog() {
    try {
      const { log: text } = await getBenchLog(run.run_id);
      setLog(text || "(the log is empty)");
      setLogError(null);
    } catch (e) {
      setLogError(errorText(e, "Could not load the log."));
    }
  }

  return (
    <section className={`${card} flex flex-col gap-3`} aria-labelledby="bench-run" aria-live="polite">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id="bench-run" className="font-medium text-gray-900">Run {shortId(run.run_id)}</h3>
        <ScoreBadge kind={run.score_kind} />
        <span className="rounded-full bg-gray-100 px-2.5 py-0.5 text-xs font-medium text-gray-700">
          {cancelling ? "Cancelling…" : `${STATUS_TEXT[run.status] ?? run.status}${phase}`}
        </span>
        <span className="ml-auto text-sm text-gray-500">Elapsed {fmtDuration(run.elapsed_s)}</span>
      </div>

      {c && (
        <>
          <div className="h-2.5 overflow-hidden rounded-full bg-gray-100" role="progressbar" aria-label="Documents processed"
            aria-valuemin={0} aria-valuemax={c.total} aria-valuenow={processed(c)}>
            <div className="h-full bg-blue-600 transition-[width]" style={{ width: `${progressPercent(c)}%` }} />
          </div>
          <p className="text-sm text-gray-700">
            {processed(c)} of {c.total} documents processed ·{" "}
            <span className="text-green-700">{c.done} succeeded</span> ·{" "}
            <span className={c.failed ? "text-red-700" : ""}>{c.failed} failed{c.timed_out ? ` (${c.timed_out} timed out)` : ""}</span>
            {c.empty ? <> · <span className="text-amber-800">{c.empty} empty</span></> : null} · {c.pending} remaining
          </p>
        </>
      )}
      {run.error && <p className="text-sm text-red-700">{run.error}</p>}
      <p className="text-xs text-gray-500">
        {run.config.categories.length} categories
        {run.config.per_category ? ` · ${run.config.per_category} per category` : " · every PDF"}
        {run.config.workers ? ` · ${run.config.workers} worker(s)` : ""} · revision {run.git.sha?.slice(0, 7) ?? "unknown"}
        {run.git.dirty ? " (uncommitted changes)" : ""}
      </p>
      {run.status === "interrupted" && (
        <p className="text-sm text-amber-800">This run stopped before it finished (the server may have restarted). Finished documents are saved; resume to continue.</p>
      )}
      <div className="flex flex-wrap gap-2">
        {active && (
          <button type="button" disabled={busy || cancelling} className={btn}
            onClick={() => { if (window.confirm("Cancel this run? Finished documents are kept and you can resume it later.")) onCancel(); }}>
            <Square size={14} /> {cancelling ? "Cancelling…" : "Cancel"}
          </button>
        )}
        {run.resumable && run.status !== "created" && (
          <button type="button" disabled={busy} className={btnPrimary} onClick={onResume}>
            <RotateCcw size={14} /> Resume{c ? ` (${c.pending + c.failed} left)` : ""}
          </button>
        )}
        <button type="button" onClick={showLog} className={btn}>View log</button>
      </div>
      {logError && <p className="text-sm text-red-700">{logError}</p>}
      {run.failure_count > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-red-700">{run.failure_count} failed document{run.failure_count === 1 ? "" : "s"}</summary>
          <ul className="mt-2 flex max-h-56 flex-col gap-1 overflow-auto text-xs text-gray-700">
            {run.failures.map((f) => (<li key={f.pdf}><span className="font-medium">{f.pdf}</span>: {f.timed_out ? "timed out" : f.error ?? "unknown error"}</li>))}
            {run.failure_count > run.failures.length && <li>…and {run.failure_count - run.failures.length} more</li>}
          </ul>
        </details>
      )}
      {log !== null && <pre className="max-h-64 overflow-auto rounded-lg bg-gray-900 p-3 text-xs text-gray-100">{log}</pre>}
    </section>
  );
}
