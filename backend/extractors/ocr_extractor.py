"""OCR extractor using RapidOCR (PaddleOCR models via ONNX Runtime).

Handles scanned PDFs, image-only PDF pages, and standalone images (JPG/PNG).
Self-hosted — no external API calls.
"""

from __future__ import annotations

import io
import logging
import threading
from pathlib import Path
from typing import ClassVar

from PIL import Image, ImageOps

from extractors.base import BaseExtractor, ExtractionResult
from models.document import BBox, BlockType, DocumentBlock
from models.errors import DocumentError

logger = logging.getLogger(__name__)

# Lazy-initialized singleton
_ocr_engine = None
_ocr_init_lock = threading.Lock()


def _get_ocr():
    """Lazy-load RapidOCR (thread-safe: requests run in a thread pool)."""
    global _ocr_engine
    if _ocr_engine is None:
        with _ocr_init_lock:
            if _ocr_engine is None:
                from rapidocr_onnxruntime import RapidOCR
                _ocr_engine = RapidOCR()
    return _ocr_engine


def _normalize_bbox_from_points(
    points: list[list[float]], img_w: int, img_h: int
) -> BBox:
    """Convert RapidOCR quad points [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] to normalized bbox."""
    if not points or img_w <= 0 or img_h <= 0:
        return (0.0, 0.0, 1.0, 1.0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (
        round(min(xs) / img_w, 6),
        round(min(ys) / img_h, 6),
        round(max(xs) / img_w, 6),
        round(max(ys) / img_h, 6),
    )


# Confidence threshold below which blocks are flagged for review
_LOW_CONFIDENCE_THRESHOLD = 0.6


class OCRExtractor(BaseExtractor):
    """Extracts text from images and scanned PDFs using RapidOCR.

    Uses PaddleOCR's detection + recognition models via ONNX Runtime.
    Returns real confidence scores from the OCR engine.
    """

    name: ClassVar[str] = "rapidocr"
    supported_extensions: ClassVar[frozenset[str]] = frozenset({"jpg", "jpeg", "png"})

    def extract(self, file_path: Path) -> ExtractionResult:
        try:
            img = Image.open(file_path)
            img.load()
            img = ImageOps.exif_transpose(img)  # phone photos carry rotation in EXIF
        except Exception as exc:
            from models.errors import AppError
            raise AppError(
                "INVALID_FILE", f"Could not open image: {exc}", status_code=422
            ) from exc

        blocks, errors = self._ocr_image(img, page_number=1)
        return ExtractionResult(page_count=1, blocks=blocks, errors=errors)

    def ocr_pil_image(
        self, img: Image.Image, page_number: int
    ) -> tuple[list[DocumentBlock], list[DocumentError]]:
        """Public interface for the routing layer to OCR a single page image."""
        return self._ocr_image(img, page_number)

    def _ocr_image(
        self, img: Image.Image, page_number: int
    ) -> tuple[list[DocumentBlock], list[DocumentError]]:
        """Run OCR on a PIL Image. Returns (blocks, errors)."""
        errors: list[DocumentError] = []
        blocks: list[DocumentBlock] = []

        # Convert to RGB; flatten transparency onto white (a black fill hides dark text)
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            flat = Image.new("RGB", rgba.size, "white")
            flat.paste(rgba, mask=rgba.split()[-1])
            img = flat
        elif img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        img_w, img_h = img.size
        if img_w < 10 or img_h < 10:
            return blocks, errors

        try:
            ocr = _get_ocr()
            # RapidOCR accepts PIL Image, numpy array, or path
            import numpy as np
            img_array = np.array(img)
            result, _ = ocr(img_array)
        except Exception as exc:
            errors.append(
                DocumentError(
                    code="OCR_FAILED",
                    message=f"OCR engine error: {exc}",
                    page=page_number,
                )
            )
            return blocks, errors

        if result is None:
            return blocks, errors

        for idx, (points, text, conf) in enumerate(result):
            text = text.strip()
            if not text:
                continue

            confidence = round(float(conf), 4) if conf is not None else None
            requires_review = (
                confidence is not None and confidence < _LOW_CONFIDENCE_THRESHOLD
            )

            bbox = _normalize_bbox_from_points(points, img_w, img_h)

            blocks.append(
                DocumentBlock(
                    id=f"p{page_number}-ocr{idx}",
                    type=BlockType.PARAGRAPH,
                    content=text,
                    page=page_number,
                    bbox=bbox,
                    confidence=confidence,
                    extractor=self.name,
                    requires_review=requires_review,
                    metadata={
                        "ocr_engine": "rapidocr",
                        "confidence_source": "rapidocr_recognition",
                        "image_width": img_w,
                        "image_height": img_h,
                    },
                )
            )

        return blocks, errors
