"use client";

import { useEffect, useMemo, useState } from "react";
import { getBenchTrends, type BenchHistoryItem, type TrendResponse } from "@/lib/benchmark";
import { buildSeries, fmtDuration, fmtPct, headline, shortId } from "@/lib/benchmark-utils";
import { card, Empty, ErrorBanner, errorText, input, ScoreBadge } from "./ui";

const W = 640;
const H = 160;
const COLORS = ["#1a73e8", "#188038", "#e37400", "#a142f4", "#d93025", "#12b5cb", "#80868b"];

interface Props {
  history: BenchHistoryItem[];
  activeId: string | null;
  onOpen: (runId: string) => void;
}

function TrendChart({ trend, metric }: { trend: TrendResponse; metric: string }) {
  const pts = trend.points;
  const ids = pts.map((p) => p.run_id);
  const lines = useMemo(() => {
    const names = metric === "overall" ? ["overall"] : [metric];
    return names.map((name, i) => ({
      name,
      color: COLORS[i % COLORS.length],
      segs: buildSeries(pts.map((p) => (name === "overall" ? p.overall_pct ?? p.diagnostic_pct : p.categories[name] ?? null)), ids, W, H),
    }));
  }, [pts, ids, metric]);
  if (pts.length === 0) return <Empty>No finished runs for this sample yet.</Empty>;
  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-40 w-full" role="img" aria-label={`Trend of ${metric} pass rate across ${pts.length} runs`}>
        {[0, 50, 100].map((g) => (
          <g key={g}>
            <line x1={0} x2={W} y1={6 + (H - 12) * (1 - g / 100)} y2={6 + (H - 12) * (1 - g / 100)} stroke="#dadce0" strokeDasharray="3 3" />
            <text x={2} y={4 + (H - 12) * (1 - g / 100)} fontSize="9" fill="#5f6368">{g}%</text>
          </g>
        ))}
        {lines.map((l) => l.segs.map((seg, i) => (
          <g key={`${l.name}-${i}`}>
            <polyline fill="none" stroke={l.color} strokeWidth={2} points={seg.map((p) => `${p.x},${p.y}`).join(" ")} />
            {seg.map((p) => (<circle key={p.runId} cx={p.x} cy={p.y} r={3.5} fill={l.color}><title>{shortId(p.runId)}</title></circle>))}
          </g>
        )))}
      </svg>
      <figcaption className="text-xs text-gray-500">{pts.length} run(s), oldest to newest. Points from runs without a valid official score are diagnostic.</figcaption>
    </figure>
  );
}

export default function HistoryTrends({ history, activeId, onOpen }: Props) {
  const [sample, setSample] = useState<string>("");
  const [metric, setMetric] = useState("overall");
  const [trend, setTrend] = useState<TrendResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fingerprint = history.map((h) => `${h.run_id}:${h.status}`).join(",");

  useEffect(() => {
    let stop = false;
    getBenchTrends(sample || undefined)
      .then((t) => { if (!stop) { setTrend(t); setError(null); } })
      .catch((e) => { if (!stop) setError(errorText(e, "Could not load trends.")); });
    return () => { stop = true; };
  }, [sample, fingerprint]);

  const cats = useMemo(() => Array.from(new Set(history.flatMap((h) => Object.keys(h.category_scores ?? {})))).sort(), [history]);

  return (
    <div className="flex flex-col gap-5">
      <section className={`${card} flex flex-col gap-3`} aria-labelledby="bench-trend">
        <div className="flex flex-wrap items-center gap-2">
          <h3 id="bench-trend" className="mr-auto font-medium text-gray-900">Trend</h3>
          <select aria-label="Sample" value={sample} onChange={(e) => setSample(e.target.value)} className={input}>
            <option value="">All samples</option>
            {trend?.samples.map((s) => (<option key={s.sample_id} value={s.sample_id}>Sample {s.sample_id.slice(0, 8)} ({s.runs} runs)</option>))}
          </select>
          <select aria-label="Metric" value={metric} onChange={(e) => setMetric(e.target.value)} className={input}>
            <option value="overall">Overall</option>
            {cats.map((c) => (<option key={c} value={c}>{c}</option>))}
          </select>
        </div>
        <ErrorBanner error={error} />
        {trend?.note && <p className="text-xs text-amber-800">{trend.note}</p>}
        {trend && <TrendChart trend={trend} metric={metric} />}
      </section>

      <section className={card} aria-labelledby="bench-history">
        <h3 id="bench-history" className="mb-3 font-medium text-gray-900">History</h3>
        {history.length === 0 ? <Empty>No benchmark runs yet.</Empty> : (
          <ul className="flex flex-col gap-2">
            {history.map((h) => {
              const hl = headline(h.overall_score_pct, h.diagnostic_score_pct);
              return (
                <li key={h.run_id}>
                  <button type="button" onClick={() => onOpen(h.run_id)}
                    className={`grid w-full gap-x-4 gap-y-1 rounded-lg border px-3 py-2 text-left text-sm hover:bg-gray-50 sm:grid-cols-[1.4fr_1fr_1fr_auto] ${h.run_id === activeId ? "border-blue-600" : "border-gray-200"}`}>
                    <span className="min-w-0">
                      <span className="block font-medium">{shortId(h.run_id)}</span>
                      <span className="block text-xs text-gray-500">{new Date(h.created_at).toLocaleString()}</span>
                    </span>
                    <span className="text-xs text-gray-600">
                      <span className="block">{h.git?.sha ? h.git.sha.slice(0, 7) : "no revision"}{h.git?.dirty ? " +local changes" : ""}</span>
                      <span className="block">{h.mode} · {Object.keys(h.category_scores ?? {}).length || h.config.categories.length} categories · sample {h.sample_id?.slice(0, 6) ?? "?"}</span>
                    </span>
                    <span className="text-xs text-gray-600">
                      <span className="block capitalize">{h.status}{h.completeness === "incomplete" ? ", incomplete" : ""}</span>
                      <span className="block">{fmtDuration(h.elapsed_s)}{h.median_ms_per_page ? ` · ${h.median_ms_per_page} ms/page` : ""}</span>
                    </span>
                    <span className="flex flex-col items-start gap-1 sm:items-end">
                      <span className={h.overall_score_pct === null ? "text-gray-500" : "font-medium"}>{hl.value}</span>
                      <ScoreBadge kind={h.score_kind} />
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        <p className="mt-3 text-xs text-gray-500">{history.length > 0 && `Showing ${history.length} run(s). Pass rates: ${fmtPct(history[0].diagnostic_score_pct ?? history[0].overall_score_pct)} for the newest.`}</p>
      </section>
    </div>
  );
}
