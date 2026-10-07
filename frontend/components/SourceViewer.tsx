"use client";

import { useEffect, useRef, useState } from "react";
import type { DocumentBlock } from "@/lib/types";
import SourceHighlight from "./SourceHighlight";

interface SourceViewerProps {
  file: File;
  fileType: string;
  pageCount: number;
  selectedBlock: DocumentBlock | null;
  onClose: () => void;
}

export default function SourceViewer({
  file,
  fileType,
  pageCount,
  selectedBlock,
  onClose,
}: SourceViewerProps) {
  const [viewerPage, setViewerPage] = useState(selectedBlock?.page ?? 1);
  const [goInput, setGoInput] = useState("");

  const [trackedBlockId, setTrackedBlockId] = useState(selectedBlock?.id);
  if (trackedBlockId !== selectedBlock?.id) {
    setTrackedBlockId(selectedBlock?.id);
    if (selectedBlock) setViewerPage(selectedBlock.page);
  }

  const showHighlight = selectedBlock && viewerPage === selectedBlock.page;

  const isPdf = false; // Disabled due to pdfjs-dist compatibility issues
  const isImage = fileType === "jpg" || fileType === "jpeg" || fileType === "png";

  function handleGoToPage() {
    const n = parseInt(goInput, 10);
    if (!isNaN(n) && n >= 1 && n <= pageCount) {
      setViewerPage(n);
      setGoInput("");
    }
  }

  return (
    <div className="flex flex-col h-full border-l border-gray-200 bg-white">
      {/* Header */}
      <div className="flex-shrink-0 border-b border-gray-200 p-3">
        <div className="flex items-center justify-between mb-2">
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

        {/* Page navigation */}
        {isPdf && pageCount > 1 && (
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

        {/* Return to selected block */}
        {selectedBlock && viewerPage !== selectedBlock.page && (
          <button
            type="button"
            onClick={() => setViewerPage(selectedBlock.page)}
            className="mt-1 text-xs text-blue-600 hover:text-blue-800 underline"
          >
            Return to selected block (p.{selectedBlock.page})
          </button>
        )}

        {/* Provenance details */}
        {selectedBlock && (
          <div className="mt-2 rounded bg-gray-50 p-2 text-xs text-gray-600">
            <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
              <div><span className="text-gray-400">Block:</span> {selectedBlock.id}</div>
              <div><span className="text-gray-400">Type:</span> {selectedBlock.type}</div>
              <div><span className="text-gray-400">Page:</span> {selectedBlock.page}</div>
              <div><span className="text-gray-400">Extractor:</span> {selectedBlock.extractor}</div>
              {selectedBlock.confidence !== null && (
                <div>
                  <span className="text-gray-400">Confidence:</span>{" "}
                  {(selectedBlock.confidence * 100).toFixed(1)}%
                </div>
              )}
              {selectedBlock.bbox && (
                <div className="col-span-2">
                  <span className="text-gray-400">BBox:</span>{" "}
                  [{selectedBlock.bbox.map((v) => v.toFixed(3)).join(", ")}]
                </div>
              )}
            </div>
            {!selectedBlock.bbox && (
              <p className="mt-1 text-gray-400 italic">
                Exact source region unavailable for this block.
              </p>
            )}
          </div>
        )}
      </div>

      {/* Content area */}
      <div className="flex-1 overflow-auto p-2 bg-gray-100">
        {isPdf && (
          <div className="p-4 text-sm text-gray-500">
            PDF preview is not available due to library compatibility issues. Please use an image file (JPG, PNG) for source viewing.
          </div>
        )}
        {isImage && (
          <ImageSourceViewer
            file={file}
            selectedBBox={selectedBlock?.bbox ?? null}
            blockLabel={selectedBlock ? `${selectedBlock.type} ${selectedBlock.id}` : undefined}
          />
        )}
        {!isPdf && !isImage && (
          <div className="flex items-center justify-center h-full text-sm text-gray-500">
            Visual source highlighting is currently available for image inputs (JPG, PNG).
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Image Source Viewer                                                  */
/* ------------------------------------------------------------------ */

function ImageSourceViewer({
  file,
  selectedBBox,
  blockLabel,
}: {
  file: File;
  selectedBBox: [number, number, number, number] | null;
  blockLabel?: string;
}) {
  const imgRef = useRef<HTMLImageElement>(null);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  const [url, setUrl] = useState("");

  useEffect(() => {
    const objectUrl = URL.createObjectURL(file);
    // Object URL creation is a side effect requiring cleanup — setState is intentional
    setUrl(objectUrl); // eslint-disable-line react-hooks/set-state-in-effect
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);

  function handleLoad() {
    if (imgRef.current) {
      setDimensions({
        width: imgRef.current.clientWidth,
        height: imgRef.current.clientHeight,
      });
    }
  }

  return (
    <div className="relative inline-block">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        ref={imgRef}
        src={url}
        alt="Original document"
        className="block max-w-full"
        onLoad={handleLoad}
      />
      {dimensions.width > 0 && selectedBBox && (
        <SourceHighlight
          bbox={selectedBBox}
          containerWidth={dimensions.width}
          containerHeight={dimensions.height}
          label={blockLabel}
        />
      )}
    </div>
  );
}
