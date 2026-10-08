"use client";

import { useState } from "react";
import {
  currentStageId,
  describeOutcome,
  settledCount,
  type PipelineState,
  type StageProgress,
  type StageState,
} from "@/lib/pipeline";

interface ProcessingPipelineProps {
  fileName: string;
  pipeline: PipelineState;
  /** Shown once the result is on screen: a slim summary that can be expanded again. */
  compact?: boolean;
  onCancel?: () => void;
}

const CIRCLE: Record<StageState, string> = {
  pending: "border-gray-300 bg-white text-gray-400",
  running: "border-blue-600 bg-blue-600 text-white",
  completed: "border-green-600 bg-green-600 text-white",
  warning: "border-amber-500 bg-amber-500 text-white",
  failed: "border-red-600 bg-red-600 text-white",
  skipped: "border-gray-300 bg-gray-100 text-gray-500",
};

const LABEL: Record<StageState, string> = {
  pending: "text-gray-400",
  running: "text-blue-700 font-semibold",
  completed: "text-gray-900",
  warning: "text-amber-800",
  failed: "text-red-700",
  skipped: "text-gray-500",
};

const FILL: Record<StageState, string> = {
  pending: "bg-gray-300",
  running: "bg-blue-600",
  completed: "bg-green-500",
  warning: "bg-amber-400",
  failed: "bg-red-500",
  skipped: "bg-gray-300",
};

const STATE_WORD: Record<StageState, string> = {
  pending: "waiting",
  running: "in progress",
  completed: "completed",
  warning: "completed with warnings",
  failed: "failed",
  skipped: "skipped",
};

const SEVERITY_CLS: Record<string, string> = {
  critical: "bg-red-100 text-red-800",
  high: "bg-red-50 text-red-700",
  medium: "bg-amber-50 text-amber-700",
  low: "bg-gray-100 text-gray-600",
  info: "bg-gray-50 text-gray-500",
};

function Mark({ state, n }: { state: StageState; n: number }) {
  if (state === "completed")
    return (
      <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2.6" aria-hidden>
        <path d="M4.5 10.5l3.5 3.5 7.5-8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  if (state === "failed")
    return (
      <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2.6" aria-hidden>
        <path d="M5.5 5.5l9 9M14.5 5.5l-9 9" strokeLinecap="round" />
      </svg>
    );
  if (state === "warning") return <span aria-hidden className="text-sm font-bold leading-none">!</span>;
  if (state === "skipped") return <span aria-hidden className="text-sm leading-none">–</span>;
  return <span aria-hidden className="text-xs font-semibold leading-none">{n}</span>;
}

function seconds(ms: number | null): string {
  return ms == null ? "" : `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`;
}

export default function ProcessingPipeline({ fileName, pipeline, compact = false, onCancel }: ProcessingPipelineProps) {
  const [selected, setSelected] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const streaming = pipeline.phase === "streaming";
  const current = currentStageId(pipeline);
  const settled = settledCount(pipeline);
  const total = pipeline.stages.length;
  const showFull = !compact || expanded;
  const detailed = !compact || expanded;
  const failed = pipeline.stages.some((s) => pipeline.progress[s.id]?.state === "failed");
  const warned = pipeline.stages.some((s) => pipeline.progress[s.id]?.state === "warning");

  const headline = streaming
    ? current
      ? `${pipeline.stages.find((s) => s.id === current)?.label} (${settled} of ${total} stages finished)`
      : `Starting… (${settled} of ${total} stages finished)`
    : pipeline.phase === "error"
      ? "Processing stopped before it finished"
      : failed
        ? "Processed — some stages could not complete"
        : warned
          ? "Processed — review the flagged stages"
          : "Processed";

  const selectedStage = pipeline.stages.find((s) => s.id === selected) ?? null;
  const selectedProgress: StageProgress | null = selectedStage ? pipeline.progress[selectedStage.id] : null;

  return (
    <section
      aria-label={`Processing ${fileName}`}
      className={`rounded-lg border bg-white ${compact ? "border-gray-200 p-3" : "border-gray-200 p-4 sm:p-5"}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-gray-900" title={fileName}>
            {fileName}
          </p>
          <p role="status" aria-live="polite" className={`text-xs ${pipeline.phase === "error" ? "text-red-600" : "text-gray-500"}`}>
            {headline}
          </p>
        </div>
        {streaming && onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-gray-300 px-3 py-1 text-xs text-gray-600 transition-colors hover:bg-gray-100"
          >
            Cancel
          </button>
        )}
        {compact && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            className="rounded-md border border-gray-300 px-3 py-1 text-xs text-gray-600 transition-colors hover:bg-gray-100"
          >
            {expanded ? "Hide pipeline" : "Show pipeline"}
          </button>
        )}
      </div>

      {showFull && (
        <ol className="mt-4 flex flex-col md:flex-row" aria-label="Processing stages">
          {pipeline.stages.map((stage, i) => {
            const p = pipeline.progress[stage.id] ?? { state: "pending" as StageState, detail: null, elapsedMs: null };
            const last = i === pipeline.stages.length - 1;
            const hasDetail = p.state !== "pending";
            const isSelected = selected === stage.id;
            return (
              <li
                key={stage.id}
                aria-current={p.state === "running" ? "step" : undefined}
                className="relative flex gap-3 pb-5 last:pb-0 md:flex-1 md:flex-col md:items-center md:gap-2 md:pb-0"
              >
                {!last && (
                  <span aria-hidden className="pipe-track">
                    <span className={`pipe-fill ${FILL[p.state]}`} data-on={String(p.state !== "pending" && p.state !== "running")} />
                  </span>
                )}
                <button
                  type="button"
                  disabled={!hasDetail}
                  onClick={() => setSelected(isSelected ? null : stage.id)}
                  aria-expanded={hasDetail ? isSelected : undefined}
                  aria-label={`${i + 1}. ${stage.label}: ${STATE_WORD[p.state]}`}
                  className="relative z-10 flex h-8 w-8 shrink-0 items-center justify-center focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2 rounded-full disabled:cursor-default"
                >
                  {p.state === "running" && (
                    <span aria-hidden className="absolute inset-0 animate-ping rounded-full bg-blue-400 opacity-60 motion-reduce:hidden" />
                  )}
                  <span
                    className={`relative flex h-8 w-8 items-center justify-center rounded-full border-2 transition-colors duration-500 ${CIRCLE[p.state]} ${
                      p.state === "completed" || p.state === "failed" || p.state === "warning" ? "pipe-pop" : ""
                    }`}
                  >
                    <Mark state={p.state} n={i + 1} />
                  </span>
                </button>
                {detailed && (
                  <div className="min-w-0 md:text-center">
                    <p className={`text-sm leading-tight transition-colors ${LABEL[p.state]}`}>{stage.label}</p>
                    <p className="mt-0.5 text-xs text-gray-500 md:px-1">
                      {p.state === "pending" || p.state === "running" ? stage.description : describeOutcome(stage.id, p)}
                    </p>
                    {hasDetail && p.state !== "running" && (
                      <button
                        type="button"
                        onClick={() => setSelected(isSelected ? null : stage.id)}
                        aria-expanded={isSelected}
                        className="mt-1 text-xs text-blue-600 hover:underline"
                      >
                        {isSelected ? "Hide details" : "Details"}
                      </button>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}

      {showFull && selectedStage && selectedProgress && (
        <div className="mt-4 rounded-md border border-gray-200 bg-gray-50 p-3 text-sm" data-testid="stage-details">
          <p className="font-medium text-gray-900">
            {selectedStage.label} <span className="font-normal text-gray-500">· {STATE_WORD[selectedProgress.state]}</span>
            {selectedProgress.elapsedMs != null && (
              <span className="font-normal text-gray-400"> · reported at {seconds(selectedProgress.elapsedMs)}</span>
            )}
          </p>
          <p className="mt-1 text-gray-600">{describeOutcome(selectedStage.id, selectedProgress)}</p>
          {selectedProgress.detail?.by_severity && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {Object.entries(selectedProgress.detail.by_severity).map(([sev, n]) => (
                <span key={sev} className={`rounded px-1.5 py-0.5 text-xs ${SEVERITY_CLS[sev] ?? "bg-gray-100 text-gray-600"}`}>
                  {n} {sev}
                </span>
              ))}
            </div>
          )}
          {selectedProgress.detail?.codes && (
            <ul className="mt-2 space-y-0.5 text-xs text-gray-600">
              {Object.entries(selectedProgress.detail.codes).map(([code, n]) => (
                <li key={code}>
                  <span className="font-mono">{code.replace(/_/g, " ")}</span> × {n}
                </li>
              ))}
            </ul>
          )}
          {(selectedStage.id === "security" || selectedStage.id === "verdict" || selectedProgress.detail?.issues) ? (
            <p className="mt-2 text-xs text-gray-400">Locations and evidence appear in the panels below once processing finishes.</p>
          ) : null}
        </div>
      )}

      {compact && !expanded && (
        <ol className="mt-3 flex items-center gap-1.5" aria-label="Processing stages">
          {pipeline.stages.map((stage, i) => {
            const state = pipeline.progress[stage.id]?.state ?? "pending";
            return (
              <li key={stage.id} title={`${stage.label}: ${STATE_WORD[state]}`} className="flex items-center gap-1.5">
                <span className={`h-2.5 w-2.5 rounded-full ${FILL[state]}`} aria-label={`${stage.label}: ${STATE_WORD[state]}`} />
                {i < pipeline.stages.length - 1 && <span aria-hidden className="h-px w-3 bg-gray-300" />}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
