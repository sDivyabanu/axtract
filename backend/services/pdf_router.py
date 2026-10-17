"""Adaptive PDF routing: analyzes each page and routes to the best extractor.

Pipeline:
  1. Open PDF, analyze each page (digital text? scanned? mixed?)
  2. Digital pages → PyMuPDF text/table/figure extraction
  3. Scanned pages → render to image → OCR
  4. Mixed pages → PyMuPDF for text, OCR for image-only regions
"""

from __future__ import annotations

import logging
from pathlib import Path

import pymupdf
from PIL import Image

from extractors.base import ExtractionResult
from extractors.ocr_extractor import OCRExtractor
from extractors.pymupdf_extractor import PyMuPDFExtractor
from models.document import BlockType, DocumentBlock
from models.errors import AppError, DocumentError
from utils import deadline
from utils.request_cache import set_cached_image

logger = logging.getLogger(__name__)

# Pre-render pixel budget and adaptive DPI constants
_BASE_OCR_DPI = 200
_MAX_OCR_PIXELS = 4_500_000  # Strictly enforced 4.5 Megapixel memory cap
_MAX_OCR_SIDE = 2500
_MIN_OCR_DPI_SINGLE = 72


class PDFRouter:
    """Analyzes PDF pages and routes to appropriate extractors."""

    def __init__(self):
        self._pymupdf = PyMuPDFExtractor()
        self._ocr = OCRExtractor()

    def extract(self, file_path: Path) -> ExtractionResult:
        try:
            doc = pymupdf.open(str(file_path), filetype="pdf")
        except Exception as exc:
            raise AppError(
                "INVALID_FILE", "The PDF could not be opened.", status_code=422
            ) from exc

        with doc:
            if doc.needs_pass:
                raise AppError(
                    "ENCRYPTED_FILE",
                    "Password-protected PDFs are not supported yet.",
                    status_code=422,
                )

            if doc.page_count == 0:
                raise AppError(
                    "INVALID_FILE",
                    "The PDF has no readable pages (it may be corrupt or truncated).",
                    status_code=422,
                )

            result = ExtractionResult(page_count=doc.page_count)

            for page_number, page in enumerate(doc, start=1):
                deadline.check()
                try:
                    analysis = PyMuPDFExtractor.analyze_page(page)
                    route = self._decide_route(analysis)

                    if route == "digital":
                        page_blocks = self._pymupdf._extract_page(page, page_number)
                        for b in page_blocks:
                            b.metadata["route"] = "digital"
                        result.blocks.extend(page_blocks)

                    elif route == "scanned":
                        ocr_blocks, ocr_errors = self._ocr_page(
                            page, page_number, file_path=file_path, analysis=analysis
                        )
                        for b in ocr_blocks:
                            b.metadata["route"] = "scanned"
                        result.blocks.extend(ocr_blocks)
                        result.errors.extend(ocr_errors)

                    elif route == "mixed":
                        # Extract digital text first
                        digital_blocks = self._pymupdf._extract_page(page, page_number)
                        for b in digital_blocks:
                            b.metadata["route"] = "mixed-digital"
                        result.blocks.extend(digital_blocks)

                        # If very little text extracted, also OCR
                        total_text = sum(len(b.content) for b in digital_blocks)
                        if total_text < 50:
                            ocr_blocks, ocr_errors = self._ocr_page(
                                page, page_number, file_path=file_path, analysis=analysis
                            )
                            for b in ocr_blocks:
                                b.metadata["route"] = "mixed-ocr"
                            result.blocks.extend(ocr_blocks)
                            result.errors.extend(ocr_errors)

                    result.blocks.append(
                        DocumentBlock(
                            id=f"p{page_number}-meta",
                            type=BlockType.UNKNOWN,
                            content="",
                            page=page_number,
                            bbox=None,
                            confidence=None,
                            extractor="router",
                            metadata={
                                "page_analysis": analysis,
                                "route": route,
                                "_internal_meta": True,
                            },
                        )
                    )

                except Exception as exc:
                    result.errors.append(
                        DocumentError(
                            code="PAGE_ROUTING_FAILED",
                            message=f"Page {page_number}: {exc}",
                            page=page_number,
                        )
                    )

            # Remove internal meta blocks from output
            result.blocks = [
                b for b in result.blocks
                if not b.metadata.get("_internal_meta")
            ]

            self._assign_heading_levels(result.blocks)
            return result

    @staticmethod
    def _assign_heading_levels(blocks: list[DocumentBlock]) -> None:
        """Document-level heading levels: larger font -> shallower level (max 4)."""
        headings = [b for b in blocks if b.type == BlockType.HEADING and b.metadata.get("font_size")]
        sizes = sorted({round(b.metadata["font_size"]) for b in headings}, reverse=True)
        for b in headings:
            level = sizes.index(round(b.metadata["font_size"])) + 1
            b.metadata["heading_level"] = min(level, 4)

    def _decide_route(self, analysis: dict) -> str:
        """Decide extraction route based on page analysis."""
        if analysis["is_likely_scanned"]:
            return "scanned"
        if analysis["has_text"]:
            if analysis["image_coverage"] > 0.3 and analysis["text_coverage"] < 0.1:
                return "mixed"
            return "digital"
        if analysis["has_images"]:
            # A picture with only a short caption is an ordinary digital page (the picture
            # becomes a figure/chart block); only an image-dominated page with ~no text is a scan.
            if analysis["text_char_count"] >= 5 and analysis["image_coverage"] < 0.5:
                return "digital"
            return "scanned"
        return "digital"  # Empty page, let PyMuPDF handle it

    def _ocr_page(
        self,
        page: pymupdf.Page,
        page_number: int,
        file_path: Path | None = None,
        analysis: dict | None = None,
    ) -> tuple[list[DocumentBlock], list[DocumentError]]:
        """Render page to image within strict pixel budgets and run OCR."""
        errors: list[DocumentError] = []
        try:
            w_pt, h_pt = page.rect.width, page.rect.height
            raw_w = w_pt * (_BASE_OCR_DPI / 72.0)
            raw_h = h_pt * (_BASE_OCR_DPI / 72.0)
            tot_pixels = raw_w * raw_h

            scale = 1.0
            if tot_pixels > _MAX_OCR_PIXELS:
                scale = min(scale, (_MAX_OCR_PIXELS / tot_pixels) ** 0.5)
            if max(raw_w, raw_h) * scale > _MAX_OCR_SIDE:
                scale = min(scale, _MAX_OCR_SIDE / max(raw_w, raw_h))

            effective_dpi = _BASE_OCR_DPI * scale
            target_dpi = max(1, int(round(effective_dpi)))
            while target_dpi > 1 and (w_pt * target_dpi / 72.0) * (h_pt * target_dpi / 72.0) > _MAX_OCR_PIXELS:
                target_dpi -= 1

            # Bounded tiling fallback for enormous pages where single-pass DPI drops below 72
            if target_dpi < _MIN_OCR_DPI_SINGLE and (w_pt > 2000 or h_pt > 2000):
                cols = 2 if w_pt > 2500 else 1
                rows = 2 if h_pt > 2500 else 1
                tile_blocks: list[DocumentBlock] = []
                tile_w_pt = w_pt / cols
                tile_h_pt = h_pt / rows
                pad_x = tile_w_pt * 0.05
                pad_y = tile_h_pt * 0.05
                max_tile_pixels = 3_500_000

                for r in range(rows):
                    for c in range(cols):
                        deadline.check()
                        x0 = max(0.0, c * tile_w_pt - pad_x)
                        y0 = max(0.0, r * tile_h_pt - pad_y)
                        x1 = min(w_pt, (c + 1) * tile_w_pt + pad_x)
                        y1 = min(h_pt, (r + 1) * tile_h_pt + pad_y)
                        clip = pymupdf.Rect(x0, y0, x1, y1)

                        tw_pt = clip.width
                        th_pt = clip.height
                        raw_tw = tw_pt * (_BASE_OCR_DPI / 72.0)
                        raw_th = th_pt * (_BASE_OCR_DPI / 72.0)
                        tile_scale = min(1.0, (max_tile_pixels / max(1.0, raw_tw * raw_th)) ** 0.5)
                        tile_dpi = max(1, int(round(_BASE_OCR_DPI * tile_scale)))
                        while tile_dpi > 1 and (tw_pt * tile_dpi / 72.0) * (th_pt * tile_dpi / 72.0) > max_tile_pixels:
                            tile_dpi -= 1

                        pix = page.get_pixmap(clip=clip, dpi=tile_dpi, alpha=False)
                        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                        sub_blocks, sub_errors = self._ocr.ocr_pil_image(img, page_number)
                        errors.extend(sub_errors)

                        # Remap coordinates from tile relative to whole page relative
                        clip_rel_x0 = x0 / w_pt
                        clip_rel_y0 = y0 / h_pt
                        clip_rel_w = tw_pt / w_pt
                        clip_rel_h = th_pt / h_pt

                        for b in sub_blocks:
                            if b.bbox:
                                bx0, by0, bx1, by1 = b.bbox
                                remapped_bbox = (
                                    round(clip_rel_x0 + bx0 * clip_rel_w, 6),
                                    round(clip_rel_y0 + by0 * clip_rel_h, 6),
                                    round(clip_rel_x0 + bx1 * clip_rel_w, 6),
                                    round(clip_rel_y0 + by1 * clip_rel_h, 6),
                                )
                                # Overlap deduplication
                                is_dup = any(
                                    existing.bbox and abs(existing.bbox[0] - remapped_bbox[0]) < 0.02
                                    and abs(existing.bbox[1] - remapped_bbox[1]) < 0.02
                                    and existing.content.strip() == b.content.strip()
                                    for existing in tile_blocks
                                )
                                if not is_dup:
                                    b.bbox = remapped_bbox
                                    tile_blocks.append(b)
                            else:
                                tile_blocks.append(b)

                # Cache a preview-sized overview for downstream region routing
                overview_dpi = max(1, target_dpi)
                overview_pix = page.get_pixmap(dpi=overview_dpi, alpha=False)
                overview_img = Image.frombytes("RGB", (overview_pix.width, overview_pix.height), overview_pix.samples)
                if file_path:
                    set_cached_image(file_path, page_number, overview_img)

                if not tile_blocks and analysis and (analysis.get("has_images") or analysis.get("is_likely_scanned")):
                    errors.append(
                        DocumentError(
                            code="OCR_NO_TEXT_FOUND",
                            message=f"Scanned page {page_number} yielded no detectable text.",
                            page=page_number,
                        )
                    )
                return tile_blocks, errors

            # Standard single-pass render (guaranteed <= _MAX_OCR_PIXELS)
            pix = page.get_pixmap(dpi=target_dpi, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

            # Store in request cache so region_router can reuse it directly without re-rendering
            if file_path:
                set_cached_image(file_path, page_number, img)

            blocks, ocr_errors = self._ocr.ocr_pil_image(img, page_number)
            errors.extend(ocr_errors)

            if not blocks and analysis and (analysis.get("has_images") or analysis.get("is_likely_scanned")):
                errors.append(
                    DocumentError(
                        code="OCR_NO_TEXT_FOUND",
                        message=f"Scanned page {page_number} yielded no detectable text.",
                        page=page_number,
                    )
                )

            return blocks, errors
        except Exception as exc:
            return [], [
                DocumentError(
                    code="OCR_RENDER_FAILED",
                    message=f"Could not render page for OCR: {exc}",
                    page=page_number,
                )
            ]
