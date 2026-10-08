"use client";

import { useEffect, useRef, useState } from "react";
import type { BBox } from "@/lib/types";
import { previewPageUrl } from "@/lib/api";
import { rag, type HeatBlock } from "@/lib/rag";
import SourceHighlight from "./SourceHighlight";

export interface Highlight {
  page: number;
  bbox: BBox | null;
  label?: string;
}

interface Props {
  docId: string;
  filename?: string;
  pageCount: number;
  highlights: Highlight[];
  /** Page to show first (defaults to the first highlight's page). */
  page?: number;
  /** When set, a confidence heatmap toggle is offered (needs the data room id). */
  workspaceId?: string;
  onClose?: () => void;
}

/** Original page image with any number of highlighted boxes (citations, receipt operands). */
export default function EvidenceViewer({ docId, filename, pageCount, highlights, page, workspaceId, onClose }: Props) {
  const [heat, setHeat] = useState(false);
  const [blocks, setBlocks] = useState<HeatBlock[]>([]);
  const firstPage = page ?? highlights[0]?.page ?? 1;
  const [current, setCurrent] = useState(Math.max(1, firstPage));
  const [trackedKey, setTrackedKey] = useState(`${docId}|${firstPage}|${highlights.length}`);
  const key = `${docId}|${firstPage}|${highlights.length}`;
  if (trackedKey !== key) {
    setTrackedKey(key);
    setCurrent(Math.max(1, firstPage));
  }
  const total = Math.max(1, pageCount);
  useEffect(() => {
    if (!heat || !workspaceId) return;
    let live = true;
    rag.get<HeatBlock[]>(`/workspaces/${workspaceId}/documents/${docId}/blocks?page=${current}`)
      .then((b) => { if (live) setBlocks(b); })
      .catch(() => { if (live) setBlocks([]); });
    return () => { live = false; };
  }, [heat, workspaceId, docId, current]);
  const here = highlights.filter((h) => h.page === current && h.bbox);
  const others = Array.from(new Set(highlights.filter((h) => h.page !== current && h.bbox).map((h) => h.page))).sort((a, b) => a - b);

  return (
    <div className="flex h-full flex-col rounded-lg border border-gray-200 bg-white">
      <div className="flex flex-shrink-0 items-center justify-between gap-2 border-b border-gray-200 p-2 text-xs">
        <div className="min-w-0">
          <div className="truncate font-medium text-gray-800">{filename ?? "Source document"}</div>
          <div className="flex items-center gap-1 text-gray-500">
            <button type="button" disabled={current <= 1} onClick={() => setCurrent((p) => p - 1)}
              className="rounded border border-gray-300 px-1.5 hover:bg-gray-50 disabled:opacity-30">&larr;</button>
            <span>Page {current} / {total}</span>
            <button type="button" disabled={current >= total} onClick={() => setCurrent((p) => p + 1)}
              className="rounded border border-gray-300 px-1.5 hover:bg-gray-50 disabled:opacity-30">&rarr;</button>
            {others.length > 0 && (
              <span className="ml-2">
                also on:{" "}
                {others.map((p) => (
                  <button key={p} type="button" onClick={() => setCurrent(p)} className="mr-1 text-blue-600 underline">p.{p}</button>
                ))}
              </span>
            )}
          </div>
        </div>
        {workspaceId && (
          <label className="flex flex-shrink-0 items-center gap-1 text-gray-600" title="Colour every extracted block by parser confidence">
            <input type="checkbox" checked={heat} onChange={(e) => setHeat(e.target.checked)} /> Confidence
          </label>
        )}
        {onClose && (
          <button type="button" onClick={onClose} aria-label="Close viewer" className="rounded p-1 text-gray-400 hover:bg-gray-100">✕</button>
        )}
      </div>
      <div className="flex-1 overflow-auto bg-gray-100 p-2">
        <Page key={`${docId}-${current}`} docId={docId} page={current} boxes={here} heat={heat ? blocks : []} />
      </div>
    </div>
  );
}

function heatColor(b: HeatBlock): string {
  if (b.requires_review) return "rgba(220,38,38,0.28)";
  if (b.confidence == null) return "rgba(13,148,136,0.16)"; // native digital text: no OCR, so no confidence score
  if (b.confidence >= 0.9) return "rgba(22,163,74,0.20)";
  if (b.confidence >= 0.75) return "rgba(234,179,8,0.28)";
  return "rgba(220,38,38,0.28)";
}

function Page({ docId, page, boxes, heat }: { docId: string; page: number; boxes: Highlight[]; heat: HeatBlock[] }) {
  const imgRef = useRef<HTMLImageElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    const img = imgRef.current;
    if (!img) return;
    const update = () => setSize({ width: img.clientWidth, height: img.clientHeight });
    const ro = new ResizeObserver(update);
    ro.observe(img);
    update();
    return () => ro.disconnect();
  }, [loaded]);

  // bring the first highlighted box into view once the page is shown
  useEffect(() => {
    if (!loaded || !boxes[0]?.bbox || !wrapRef.current || size.height === 0) return;
    const scroller = wrapRef.current.parentElement;
    if (!scroller) return;
    const top = boxes[0].bbox[1] * size.height;
    scroller.scrollTo({ top: Math.max(0, top - scroller.clientHeight / 3), behavior: "smooth" });
  }, [loaded, boxes, size.height]);

  if (error) {
    return <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-600">Could not load page {page}.</div>;
  }
  return (
    <div ref={wrapRef} className="relative inline-block max-w-full">
      {!loaded && <div className="py-10 text-center text-sm text-gray-500">Loading page {page}…</div>}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        ref={imgRef}
        src={previewPageUrl(docId, page)}
        alt={`Page ${page}`}
        className={`block max-w-full ${loaded ? "" : "invisible absolute"}`}
        onLoad={() => setLoaded(true)}
        onError={() => setError(true)}
      />
      {loaded &&
        heat.map((b) => (
          <div key={b.id} data-heat title={`${b.type} · ${b.confidence == null ? "native text (no OCR)" : Math.round(b.confidence * 100) + "%"}${b.extractor ? " · " + b.extractor : ""}${b.requires_review ? " · needs review" : ""}${b.flags.length ? " · " + b.flags.join(", ") : ""}\n${b.snippet}`}
            className="absolute rounded-sm border border-black/10"
            style={{ left: b.bbox[0] * size.width, top: b.bbox[1] * size.height, width: (b.bbox[2] - b.bbox[0]) * size.width, height: (b.bbox[3] - b.bbox[1]) * size.height, background: heatColor(b) }} />
        ))}
      {loaded &&
        boxes.map((b, i) => (
          <SourceHighlight key={i} bbox={b.bbox} containerWidth={size.width} containerHeight={size.height} label={b.label} />
        ))}
    </div>
  );
}
