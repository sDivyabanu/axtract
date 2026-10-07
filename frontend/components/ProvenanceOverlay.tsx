"use client";

import { useState, useRef, useEffect } from "react";
import type { DocumentBlock } from "@/lib/types";

interface ProvenanceOverlayProps {
  blocks: DocumentBlock[];
  pageImageUrl?: string; // URL to the rendered page image
  onPageChange?: (page: number) => void;
}

export default function ProvenanceOverlay({
  blocks,
  pageImageUrl,
  onPageChange,
}: ProvenanceOverlayProps) {
  const [selectedBlockId, setSelectedBlockId] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imageRef = useRef<HTMLImageElement>(null);

  // Listen for custom block selection events
  useEffect(() => {
    const handleBlockSelected = (e: Event) => {
      const customEvent = e as CustomEvent<string>;
      setSelectedBlockId(customEvent.detail);
    };

    window.addEventListener("blockSelected", handleBlockSelected);
    return () => window.removeEventListener("blockSelected", handleBlockSelected);
  }, []);

  // Get unique pages
  const pages = Array.from(new Set(blocks.map((b) => b.page))).sort((a, b) => a - b);
  const currentPageBlocks = blocks.filter((b) => b.page === currentPage);

  // Handle block selection
  const handleBlockClick = (blockId: string) => {
    setSelectedBlockId(blockId === selectedBlockId ? null : blockId);
  };

  // Handle page change
  const handlePageChange = (page: number) => {
    setCurrentPage(page);
    setSelectedBlockId(null);
    onPageChange?.(page);
  };

  // Get selected block
  const selectedBlock = currentPageBlocks.find((b) => b.id === selectedBlockId);

  // Draw overlays on canvas
  useEffect(() => {
    const canvas = canvasRef.current;
    const image = imageRef.current;
    if (!canvas || !image) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Clear canvas
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Draw image
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height);

    // Draw bounding boxes
    currentPageBlocks.forEach((block) => {
      if (!block.bbox) return;

      const [x1, y1, x2, y2] = block.bbox;
      const width = canvas.width;
      const height = canvas.height;

      // Convert normalized coordinates to pixels
      const px1 = x1 * width;
      const py1 = y1 * height;
      const px2 = x2 * width;
      const py2 = y2 * height;

      // Determine color based on block type and selection
      const isSelected = block.id === selectedBlockId;
      const color = getBlockColor(block.type, isSelected);

      // Draw rectangle
      ctx.strokeStyle = color;
      ctx.lineWidth = isSelected ? 3 : 2;
      ctx.strokeRect(px1, py1, px2 - px1, py2 - py1);

      // Draw semi-transparent fill for selected block
      if (isSelected) {
        ctx.fillStyle = color + "20"; // Add transparency
        ctx.fillRect(px1, py1, px2 - px1, py2 - py1);
      }

      // Draw block ID label
      ctx.fillStyle = color;
      ctx.font = isSelected ? "bold 14px sans-serif" : "12px sans-serif";
      ctx.fillText(block.type, px1, py1 - 5);
    });
  }, [currentPageBlocks, selectedBlockId, pageImageUrl]);

  // Handle image load
  const handleImageLoad = () => {
    const canvas = canvasRef.current;
    const image = imageRef.current;
    if (!canvas || !image) return;

    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Page selector */}
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium">Page:</span>
        <div className="flex gap-1">
          {pages.map((page) => (
            <button
              key={page}
              type="button"
              onClick={() => handlePageChange(page)}
              className={`rounded px-3 py-1 text-sm ${
                currentPage === page
                  ? "bg-blue-600 text-white"
                  : "bg-gray-200 text-gray-700 hover:bg-gray-300"
              }`}
            >
              {page}
            </button>
          ))}
        </div>
      </div>

      <div className="flex gap-4">
        {/* Canvas overlay */}
        <div className="relative flex-1">
          {pageImageUrl ? (
            <>
              <img
                ref={imageRef}
                src={pageImageUrl}
                alt={`Page ${currentPage}`}
                className="hidden"
                onLoad={handleImageLoad}
              />
              <canvas
                ref={canvasRef}
                className="w-full rounded-lg border border-gray-300 cursor-crosshair"
                onClick={(e) => {
                  const canvas = e.currentTarget;
                  const rect = canvas.getBoundingClientRect();
                  const x = (e.clientX - rect.left) / rect.width;
                  const y = (e.clientY - rect.top) / rect.height;

                  // Find block that contains the click
                  for (const block of currentPageBlocks) {
                    if (!block.bbox) continue;
                    const [x1, y1, x2, y2] = block.bbox;
                    if (x >= x1 && x <= x2 && y >= y1 && y <= y2) {
                      setSelectedBlockId(block.id);
                      break;
                    }
                  }
                }}
              />
            </>
          ) : (
            <div className="flex h-96 items-center justify-center rounded-lg border border-gray-300 bg-gray-50">
              <p className="text-sm text-gray-500">
                Page image not available (placeholder for PDF rendering)
              </p>
            </div>
          )}
        </div>

        {/* Block list */}
        <div className="w-80 overflow-auto rounded-lg border border-gray-200 p-4">
          <h3 className="mb-3 font-semibold">Blocks on Page {currentPage}</h3>
          <div className="flex flex-col gap-2">
            {currentPageBlocks.map((block) => (
              <button
                key={block.id}
                type="button"
                onClick={() => handleBlockClick(block.id)}
                className={`rounded border p-2 text-left text-sm transition-colors ${
                  selectedBlockId === block.id
                    ? "border-blue-500 bg-blue-50"
                    : "border-gray-200 hover:bg-gray-50"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium">{block.type}</span>
                  {block.bbox && (
                    <span className="text-xs text-gray-400">
                      [{block.bbox[0].toFixed(2)}, {block.bbox[1].toFixed(2)}]
                    </span>
                  )}
                </div>
                <div className="mt-1 truncate text-xs text-gray-600">
                  {block.content.slice(0, 50)}
                  {block.content.length > 50 ? "..." : ""}
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Selected block details */}
      {selectedBlock && (
        <div className="rounded-lg border border-blue-300 bg-blue-50 p-4">
          <h3 className="mb-2 font-semibold text-blue-900">Selected Block</h3>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <div>
              <span className="text-gray-600">ID:</span> {selectedBlock.id}
            </div>
            <div>
              <span className="text-gray-600">Type:</span> {selectedBlock.type}
            </div>
            <div>
              <span className="text-gray-600">Page:</span> {selectedBlock.page}
            </div>
            <div>
              <span className="text-gray-600">Extractor:</span>{" "}
              {selectedBlock.extractor}
            </div>
            {selectedBlock.bbox && (
              <>
                <div>
                  <span className="text-gray-600">BBox:</span>{" "}
                  {selectedBlock.bbox.map((v) => v.toFixed(3)).join(", ")}
                </div>
              </>
            )}
            {selectedBlock.confidence !== null && (
              <div>
                <span className="text-gray-600">Confidence:</span>{" "}
                {(selectedBlock.confidence * 100).toFixed(1)}%
              </div>
            )}
          </div>
          <div className="mt-2">
            <span className="text-gray-600">Content:</span>
            <p className="mt-1 rounded bg-white p-2 text-xs">
              {selectedBlock.content}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function getBlockColor(type: string, isSelected: boolean): string {
  const colors: Record<string, string> = {
    heading: "#3b82f6", // blue
    paragraph: "#6b7280", // gray
    list: "#22c55e", // green
    table: "#a855f7", // purple
    figure: "#f97316", // orange
    chart: "#ec4899", // pink
    equation: "#ef4444", // red
    header: "#eab308", // yellow
    footer: "#eab308", // yellow
    unknown: "#9ca3af", // gray
  };

  return colors[type] || "#9ca3af";
}
