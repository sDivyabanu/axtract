"use client";

import { useEffect, useRef, useState } from "react";
import type { BBox, BlockPreview, DocumentBlock } from "@/lib/types";
import { previewPageUrl } from "@/lib/api";
import SourceHighlight from "./SourceHighlight";

interface SourceViewerProps {
  documentId: string;
  pageCount: number;
  selectedBlock: DocumentBlock | null;
  onClose: () => void;
}

/** Page + box to draw for a block: Office formats carry metadata.preview, others use their own. */
export function previewTarget(block: DocumentBlock | null): BlockPreview | null {
  if (!block) return null;
  const p = block.metadata?.preview as BlockPreview | undefined;
  if (p && typeof p.page === "number") return { page: p.page, bbox: p.bbox ?? null };
  return { page: block.page, bbox: block.bbox };
}

export default function SourceViewer({
  documentId,
  pageCount,
  selectedBlock,
  onClose,
}: SourceViewerProps) {
  const target = previewTarget(selectedBlock);
  const [viewerPage, setViewerPage] = useState(target?.page ?? 1);
  const [goInput, setGoInput] = useState("");

  const [trackedBlockId, setTrackedBlockId] = useState(selectedBlock?.id);
  if (trackedBlockId !== selectedBlock?.id) {
    setTrackedBlockId(selectedBlock?.id);
    if (target) setViewerPage(Math.min(Math.max(1, target.page), pageCount));
  }

  const showHighlight = target !== null && viewerPage === target.page;

  function handleGoToPage() {
    const n = parseInt(goInput, 10);
    if (!isNaN(n) && n >= 1 && n <= pageCount) {
      setViewerPage(n);
      setGoInput("");
    }
  }

  return (
    <div className="flex h-full flex-col border-l border-gray-200 bg-white">
      <div className="flex-shrink-0 border-b border-gray-200 p-3">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold">Original Document</h3>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
            aria-label="Close source viewer"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {pageCount > 1 && (
          <div className="flex items-center gap-1 text-xs">
            <button
              type="button"
              onClick={() => setViewerPage((p) => Math.max(1, p - 1))}
              disabled={viewerPage <= 1}
              className="rounded border border-gray-300 px-2 py-1 hover:bg-gray-50 disabled:opacity-30"
            >
              &larr;
            </button>
            <span className="min-w-[80px] text-center text-gray-600">
              Page {viewerPage} / {pageCount}
            </span>
            <button
              type="button"
              onClick={() => setViewerPage((p) => Math.min(pageCount, p + 1))}
              disabled={viewerPage >= pageCount}
              className="rounded border border-gray-300 px-2 py-1 hover:bg-gray-50 disabled:opacity-30"
            >
              &rarr;
            </button>
            <input
              type="number"
              min={1}
              max={pageCount}
              value={goInput}
              onChange={(e) => setGoInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleGoToPage()}
              placeholder="#"
              className="ml-1 w-12 rounded border border-gray-300 px-1.5 py-1 text-xs focus:border-gray-500 focus:outline-none"
            />
            <button
              type="button"
              onClick={handleGoToPage}
              className="rounded border border-gray-300 px-2 py-1 hover:bg-gray-50"
            >
              Go
            </button>
          </div>
        )}

        {target && viewerPage !== target.page && (
          <button
            type="button"
            onClick={() => setViewerPage(target.page)}
            className="mt-1 text-xs text-blue-600 underline hover:text-blue-800"
          >
            Return to selected block (p.{target.page})
          </button>
        )}

        {selectedBlock && (
          <div className="mt-2 rounded bg-gray-50 p-2 text-xs text-gray-600">
            <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
              <div><span className="text-gray-400">Block:</span> {selectedBlock.id}</div>
              <div><span className="text-gray-400">Type:</span> {selectedBlock.type}</div>
              <div><span className="text-gray-400">Page:</span> {selectedBlock.page}</div>
              <div><span className="text-gray-400">Extractor:</span> {selectedBlock.extractor}</div>
              {selectedBlock.confidence !== null && selectedBlock.confidence !== undefined && (
                <div>
                  <span className="text-gray-400">Confidence:</span>{" "}
                  {(selectedBlock.confidence * 100).toFixed(1)}%
                </div>
              )}
              {target?.bbox && (
                <div className="col-span-2">
                  <span className="text-gray-400">BBox:</span>{" "}
                  [{target.bbox.map((v) => v.toFixed(3)).join(", ")}]
                </div>
              )}
            </div>
            {!target?.bbox && (
              <p className="mt-1 italic text-gray-400">
                Exact source region unavailable for this block.
              </p>
            )}
          </div>
        )}
      </div>

      <div className="flex-1 overflow-auto bg-gray-100 p-2">
        <PageImage
          key={`${documentId}-${viewerPage}`}
          documentId={documentId}
          page={viewerPage}
          bbox={showHighlight ? target?.bbox ?? null : null}
          label={showHighlight && selectedBlock ? `${selectedBlock.type} ${selectedBlock.id}` : undefined}
        />
      </div>
    </div>
  );
}

function PageImage({
  documentId,
  page,
  bbox,
  label,
}: {
  documentId: string;
  page: number;
  bbox: BBox | null;
  label?: string;
}) {
  const imgRef = useRef<HTMLImageElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [error, setError] = useState(false);
  const [loaded, setLoaded] = useState(false);

  // Track the rendered size so the overlay stays aligned when the pane is resized.
  useEffect(() => {
    const img = imgRef.current;
    if (!img) return;
    const update = () => setSize({ width: img.clientWidth, height: img.clientHeight });
    const observer = new ResizeObserver(update);
    observer.observe(img);
    update();
    return () => observer.disconnect();
  }, [loaded]);

  if (error) {
    return (
      <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-600">
        Could not load page {page}. The preview may have expired; parse the file again.
      </div>
    );
  }

  return (
    <div className="relative inline-block max-w-full">
      {!loaded && (
        <div className="py-12 text-center text-sm text-gray-500">Loading page {page}…</div>
      )}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        ref={imgRef}
        src={previewPageUrl(documentId, page)}
        alt={`Page ${page} of the original document`}
        className={`block max-w-full ${loaded ? "" : "invisible absolute"}`}
        onLoad={() => setLoaded(true)}
        onError={() => setError(true)}
      />
      {loaded && bbox && (
        <SourceHighlight
          bbox={bbox}
          containerWidth={size.width}
          containerHeight={size.height}
          label={label}
        />
      )}
    </div>
  );
}
