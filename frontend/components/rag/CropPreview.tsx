"use client";

import { useState } from "react";
import { previewPageUrl } from "@/lib/api";
import type { BBox } from "@/lib/types";

/** The cited region cut out of the page image (shown on citation hover). */
export default function CropPreview({ docId, page, bbox, width = 300 }: { docId: string; page: number; bbox: BBox; width?: number }) {
  const [nat, setNat] = useState<{ w: number; h: number } | null>(null);
  const padX = 0.02, padY = 0.012;
  const x0 = Math.max(0, bbox[0] - padX), x1 = Math.min(1, bbox[2] + padX);
  const y0 = Math.max(0, bbox[1] - padY), y1 = Math.min(1, bbox[3] + padY);
  const scale = width / (x1 - x0);
  const imgW = scale;
  const imgH = nat ? (imgW * nat.h) / nat.w : 0;
  const height = Math.min(200, Math.max(36, (y1 - y0) * imgH));
  return (
    <div className="relative overflow-hidden rounded-xl border border-gray-200 bg-white shadow-lift" style={{ width, height: nat ? height : 60 }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={previewPageUrl(docId, page, 110)}
        alt="cited region"
        onLoad={(e) => setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
        style={{ position: "absolute", width: imgW, left: -x0 * imgW, top: -y0 * imgH, maxWidth: "none" }}
      />
      {nat && (
        <div className="absolute border-2 border-blue-500 bg-blue-500/10"
          style={{ left: (bbox[0] - x0) * imgW, top: (bbox[1] - y0) * imgH, width: (bbox[2] - bbox[0]) * imgW, height: (bbox[3] - bbox[1]) * imgH }} />
      )}
    </div>
  );
}
