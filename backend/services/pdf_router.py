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

logger = logging.getLogger(__name__)

# DPI for rendering scanned pages to images for OCR
_OCR_RENDER_DPI = 200


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
                            page, page_number
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
                                page, page_number
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
        self, page: pymupdf.Page, page_number: int
    ) -> tuple[list[DocumentBlock], list[DocumentError]]:
        """Render page to image and run OCR."""
        try:
            # Render page to pixmap
            mat = pymupdf.Matrix(_OCR_RENDER_DPI / 72, _OCR_RENDER_DPI / 72)
            pix = page.get_pixmap(matrix=mat, alpha=False)

            # Convert to PIL Image
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

            return self._ocr.ocr_pil_image(img, page_number)
        except Exception as exc:
            return [], [
                DocumentError(
                    code="OCR_RENDER_FAILED",
                    message=f"Could not render page for OCR: {exc}",
                    page=page_number,
                )
            ]
