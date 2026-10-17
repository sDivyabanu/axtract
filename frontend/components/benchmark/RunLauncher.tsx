"use client";

import { useState } from "react";
import { Play, Sparkles } from "lucide-react";
import {
  getChangeRecommendation,
  type BenchCategoriesResponse,
  type BenchEnvironment,
  type ChangeRecommendation,
  type StartOptions,
} from "@/lib/benchmark";
import { estimatePdfs, selectOnly, selectedNames, toggleCategory, type Selection } from "@/lib/benchmark-utils";
import { btn, btnPrimary, card, ErrorBanner, errorText, input } from "./ui";

interface Props {
  meta: BenchCategoriesResponse | null;
  env: BenchEnvironment | null;
  selection: Selection;
  onSelection: (s: Selection) => void;
  busy: boolean;
  runActive: boolean;
  onStart: (o: StartOptions) => void;
}

export default function RunLauncher({ meta, env, selection, onSelection, busy, runActive, onStart }: Props) {
  const [mode, setMode] = useState<"quick" | "full">("quick");
  const [perCategory, setPerCategory] = useState(5);
  const [workers, setWorkers] = useState(2);
  const [timeoutS, setTimeoutS] = useState(300);
  const [confirmFull, setConfirmFull] = useState(false);
  const [rec, setRec] = useState<ChangeRecommendation | null>(null);
  const [recBusy, setRecBusy] = useState(false);
  const [recError, setRecError] = useState<string | null>(null);

  const names = meta?.categories.map((c) => c.name) ?? [];
  const maxPer = meta?.defaults.max_per_category ?? 50;
  const maxWorkers = env?.limits.max_workers ?? meta?.defaults.max_workers ?? 4;
  const totalPdfs = meta?.categories.reduce((n, c) => n + c.pdfs, 0) ?? 0;
  const estimate = mode === "full" ? totalPdfs : estimatePdfs(selection, meta?.categories ?? [], perCategory);
  const blockedReason = !env
    ? "Checking the benchmark environment…"
    : !env.dataset_available
      ? "The benchmark dataset is not available on this server."
      : env.official_benchmark_active
        ? "The official benchmark is running. Start a benchmark after it finishes."
        : runActive
          ? "A benchmark is already running."
          : null;
  const disabled = busy || !meta || blockedReason !== null || (mode === "full" && !confirmFull);

  async function recommend() {
    setRecBusy(true);
    setRecError(null);
    try {
      setRec(await getChangeRecommendation());
    } catch (e) {
      setRec(null);
      setRecError(errorText(e, "Could not read the repository changes."));
    } finally {
      setRecBusy(false);
    }
  }

  const chip = (on: boolean) =>
    `rounded-full border px-3.5 py-1.5 text-sm ${on ? "border-blue-600 bg-blue-50 text-blue-800" : "border-gray-300 text-gray-700 hover:bg-gray-50"}`;

  return (
    <section className={card} aria-labelledby="bench-new">
      <h3 id="bench-new" className="mb-3 font-medium text-gray-900">New run</h3>

      <div role="tablist" aria-label="Benchmark mode" className="mb-4 flex gap-2">
        <button type="button" role="tab" aria-selected={mode === "quick"} onClick={() => setMode("quick")} className={chip(mode === "quick")}>Quick (sample)</button>
        <button type="button" role="tab" aria-selected={mode === "full"} onClick={() => setMode("full")} className={chip(mode === "full")}>Full official</button>
      </div>

      {mode === "quick" ? (
        <>
          <fieldset className="mb-4">
            <legend className="mb-1.5 text-xs font-medium uppercase tracking-wide text-gray-500">Categories</legend>
            <div className="flex flex-wrap gap-2">
              <button type="button" aria-pressed={selection.all} onClick={() => onSelection({ all: true, chosen: [] })} className={chip(selection.all)}>
                All categories
              </button>
              {meta?.categories.map((c) => (
                <button key={c.name} type="button" aria-pressed={!selection.all && selection.chosen.includes(c.name)}
                  title={`${c.pdfs} PDFs, ${c.tests} test cases`} onClick={() => onSelection(toggleCategory(selection, c.name, names))}
                  className={chip(!selection.all && selection.chosen.includes(c.name))}>
                  {c.name}
                </button>
              ))}
            </div>
          </fieldset>

          <div className="mb-4 rounded-lg bg-gray-50 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" onClick={recommend} disabled={recBusy || !meta} className={btn}>
                <Sparkles size={14} /> {recBusy ? "Checking changes…" : "Recommend from my code changes"}
              </button>
              {rec && <span className="text-sm text-gray-700">{rec.summary}</span>}
            </div>
            <ErrorBanner error={recError} onDismiss={() => setRecError(null)} />
            {rec && (
              <div className="mt-2 text-sm">
                <p className="text-xs text-gray-500">
                  Changes since {rec.base.ref.length > 12 ? rec.base.ref.slice(0, 7) : rec.base.ref} ({rec.base.source}); {rec.changed_files} file(s) changed.
                  You can still pick categories yourself.
                </p>
                {rec.recommended_categories.length > 0 && (
                  <button type="button" onClick={() => onSelection(selectOnly(rec.recommended_categories, names))} className={`${btn} mt-2`}>
                    Use recommended: {rec.recommended_categories.join(", ")}
                  </button>
                )}
                {rec.low_confidence.length > 0 && (
                  <p className="mt-1 text-xs text-amber-800">Low-confidence mapping for: {rec.low_confidence.join(", ")}</p>
                )}
                <details className="mt-2 text-xs text-gray-600">
                  <summary className="cursor-pointer">Why these categories</summary>
                  <ul className="mt-1 flex flex-col gap-0.5">
                    {rec.files.filter((f) => f.categories.length > 0).map((f) => (
                      <li key={f.path}><span className="font-medium">{f.path}</span>: {f.reason}</li>
                    ))}
                  </ul>
                </details>
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-end gap-4">
            <label className="text-sm text-gray-700">
              <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-gray-500">PDFs per category</span>
              <input type="number" min={1} max={maxPer} value={perCategory} className={`${input} w-28`}
                onChange={(e) => setPerCategory(Math.max(1, Math.min(maxPer, Number(e.target.value) || 1)))} />
            </label>
            <details className="text-sm text-gray-700">
              <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-gray-500">Advanced</summary>
              <div className="mt-2 flex flex-wrap gap-3">
                <label>Workers <input type="number" min={1} max={maxWorkers} value={workers} className={`${input} w-20`}
                  onChange={(e) => setWorkers(Math.max(1, Math.min(maxWorkers, Number(e.target.value) || 1)))} /></label>
                <label>Timeout per PDF (s) <input type="number" min={env?.limits.min_timeout_s ?? 10} max={env?.limits.max_timeout_s ?? 3600} value={timeoutS} className={`${input} w-24`}
                  onChange={(e) => setTimeoutS(Number(e.target.value) || 300)} /></label>
              </div>
            </details>
            <p className="pb-2 text-sm text-gray-500">About {estimate} PDFs</p>
            <button type="button" disabled={disabled} className={`${btnPrimary} ml-auto`}
              onClick={() => onStart({ mode: "quick", categories: selectedNames(selection, names).length === names.length ? ["all"] : selectedNames(selection, names), per_category: perCategory, workers, timeout_s: timeoutS })}>
              <Play size={16} /> Start quick benchmark
            </button>
          </div>
        </>
      ) : (
        <div className="flex flex-col gap-3 text-sm text-gray-700">
          <p>
            Runs <strong>every</strong> benchmark PDF ({totalPdfs.toLocaleString()}) through the official scorer using the same flags as the official run.
            It can take many hours, runs in the background, saves a checkpoint after every document, and can be cancelled and resumed.
            It only counts as an official score if every document produced output.
          </p>
          <div className="flex flex-wrap items-end gap-4">
            <label>Workers <input type="number" min={1} max={maxWorkers} value={workers} className={`${input} w-20`}
              onChange={(e) => setWorkers(Math.max(1, Math.min(maxWorkers, Number(e.target.value) || 1)))} /></label>
            <label>Timeout per PDF (s) <input type="number" min={env?.limits.min_timeout_s ?? 10} max={env?.limits.max_timeout_s ?? 3600} value={timeoutS} className={`${input} w-24`}
              onChange={(e) => setTimeoutS(Number(e.target.value) || 300)} /></label>
          </div>
          <label className="flex items-start gap-2">
            <input type="checkbox" checked={confirmFull} onChange={(e) => setConfirmFull(e.target.checked)} className="mt-1" />
            <span>I understand this runs the complete benchmark ({totalPdfs.toLocaleString()} PDFs) and uses significant CPU and memory.</span>
          </label>
          <button type="button" disabled={disabled} className={`${btnPrimary} self-start`}
            onClick={() => onStart({ mode: "full", categories: ["all"], per_category: null, workers, timeout_s: timeoutS, confirm_full: true })}>
            <Play size={16} /> Start full benchmark
          </button>
        </div>
      )}
      {blockedReason && <p className="mt-3 text-sm text-amber-800" role="status">{blockedReason}</p>}
    </section>
  );
}
