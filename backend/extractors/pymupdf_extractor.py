"""Enhanced PyMuPDF extractor: digital text, headings, tables, and figures from PDFs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import ClassVar

import pymupdf

from extractors.base import BaseExtractor, ExtractionResult
from models.document import BBox, BlockType, DocumentBlock
from models.errors import AppError, DocumentError

# PyMuPDF block type codes from page.get_text("blocks").
_TEXT_BLOCK = 0
_IMAGE_BLOCK = 1


def _normalize_bbox(
    x0: float, y0: float, x1: float, y1: float, page_w: float, page_h: float
) -> BBox:
    """Normalize bbox to 0.0–1.0 relative to page dimensions."""
    if page_w <= 0 or page_h <= 0:
        return (0.0, 0.0, 1.0, 1.0)
    return (
        round(x0 / page_w, 6),
        round(y0 / page_h, 6),
        round(x1 / page_w, 6),
        round(y1 / page_h, 6),
    )


class PyMuPDFExtractor(BaseExtractor):
    """Extracts digital text, tables, and figures from PDFs using PyMuPDF.

    Performs font-based heading detection and uses PyMuPDF's built-in table finder.
    Does NOT perform OCR — scanned pages are detected and flagged for routing.
    """

    name: ClassVar[str] = "pymupdf"
    supported_extensions: ClassVar[frozenset[str]] = frozenset({"pdf"})

    def extract(self, file_path: Path) -> ExtractionResult:
        data = file_path.read_bytes()
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
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

            result = ExtractionResult(page_count=doc.page_count)
            for page_number, page in enumerate(doc, start=1):
                try:
                    page_blocks = self._extract_page(page, page_number)
                    result.blocks.extend(page_blocks)
                except Exception as exc:
                    result.errors.append(
                        DocumentError(
                            code="PAGE_EXTRACTION_FAILED",
                            message=str(exc) or "Unknown page error.",
                            page=page_number,
                        )
                    )
            return result

    # ------------------------------------------------------------------
    # Page-level extraction
    # ------------------------------------------------------------------

    def _extract_page(
        self, page: pymupdf.Page, page_number: int
    ) -> list[DocumentBlock]:
        pw, ph = page.rect.width, page.rect.height
        blocks: list[DocumentBlock] = []
        block_counter = 0

        # 1. Extract tables first — collect their bounding rects to exclude from text
        table_rects: list[pymupdf.Rect] = []
        try:
            table_finder = page.find_tables()
            for idx, table in enumerate(table_finder.tables):
                table_rects.append(pymupdf.Rect(table.bbox))
                blocks.append(self._table_block(table, page_number, block_counter, pw, ph))
                block_counter += 1
        except Exception:
            pass  # Table extraction failure is non-fatal

        # 2. Text blocks (excluding table regions)
        font_sizes: list[float] = []
        raw_blocks: list[dict] = []

        for item in page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)["blocks"]:
            if item.get("type", -1) == _IMAGE_BLOCK:
                # Image blocks → figure
                bbox_raw = (item["bbox"][0], item["bbox"][1], item["bbox"][2], item["bbox"][3])
                if not self._overlaps_tables(bbox_raw, table_rects):
                    blocks.append(
                        DocumentBlock(
                            id=f"p{page_number}-b{block_counter}",
                            type=BlockType.FIGURE,
                            content="[image]",
                            page=page_number,
                            bbox=_normalize_bbox(*bbox_raw, pw, ph),
                            confidence=None,
                            extractor=self.name,
                            metadata={
                                "image_width": item.get("width", 0),
                                "image_height": item.get("height", 0),
                            },
                        )
                    )
                    block_counter += 1
                continue

            if item.get("type", -1) != _TEXT_BLOCK:
                continue

            bbox_raw = (item["bbox"][0], item["bbox"][1], item["bbox"][2], item["bbox"][3])
            if self._overlaps_tables(bbox_raw, table_rects):
                continue

            # Collect font sizes for heading detection
            lines = item.get("lines", [])
            text_parts: list[str] = []
            max_font_size = 0.0
            is_bold = False

            for line in lines:
                for span in line.get("spans", []):
                    text_parts.append(span.get("text", ""))
                    fs = span.get("size", 0)
                    if fs > max_font_size:
                        max_font_size = fs
                    flags = span.get("flags", 0)
                    if flags & 2 ** 4:  # bold flag
                        is_bold = True

            content = " ".join("".join(text_parts).split()).strip()
            if not content:
                continue

            font_sizes.append(max_font_size)
            raw_blocks.append({
                "content": content,
                "bbox": bbox_raw,
                "max_font_size": max_font_size,
                "is_bold": is_bold,
                "block_counter": block_counter,
            })
            block_counter += 1

        # Determine heading threshold: font size > median * 1.2 or bold and short
        median_size = sorted(font_sizes)[len(font_sizes) // 2] if font_sizes else 12.0

        for rb in raw_blocks:
            is_heading = False
            fs = rb["max_font_size"]
            content = rb["content"]

            # Heading heuristics: larger font, or bold+short, or starts with numbering
            if fs > median_size * 1.2:
                is_heading = True
            elif rb["is_bold"] and len(content) < 120 and "\n" not in content:
                is_heading = True

            # Check for mathematical equations
            is_equation = self._is_math_equation(content)
            block_type = BlockType.HEADING if is_heading else (BlockType.EQUATION if is_equation else BlockType.PARAGRAPH)

            blocks.append(
                DocumentBlock(
                    id=f"p{page_number}-b{rb['block_counter']}",
                    type=block_type,
                    content=content,
                    page=page_number,
                    bbox=_normalize_bbox(*rb["bbox"], pw, ph),
                    confidence=None,
                    extractor=self.name,
                    metadata={
                        "font_size": rb["max_font_size"],
                        "is_bold": rb["is_bold"],
                    },
                )
            )

        return blocks

    # ------------------------------------------------------------------
    # Table handling
    # ------------------------------------------------------------------

    def _table_block(
        self, table, page_number: int, block_counter: int, pw: float, ph: float
    ) -> DocumentBlock:
        """Convert a PyMuPDF table to a DocumentBlock with structured content."""
        rows = table.extract()
        # Build a simple text representation and store structured data in metadata
        text_lines: list[str] = []
        for row in rows:
            cells = [str(c) if c is not None else "" for c in row]
            text_lines.append(" | ".join(cells))
        content = "\n".join(text_lines)

        return DocumentBlock(
            id=f"p{page_number}-b{block_counter}",
            type=BlockType.TABLE,
            content=content,
            page=page_number,
            bbox=_normalize_bbox(*table.bbox, pw, ph),
            confidence=None,
            extractor=self.name,
            metadata={
                "rows": rows,
                "row_count": len(rows),
                "col_count": table.col_count,
                "header": rows[0] if rows else [],
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _overlaps_tables(
        bbox: tuple[float, float, float, float],
        table_rects: list[pymupdf.Rect],
    ) -> bool:
        """Check if a text block significantly overlaps any table region."""
        r = pymupdf.Rect(bbox)
        for tr in table_rects:
            intersection = r & tr
            if not intersection.is_empty:
                overlap_area = intersection.width * intersection.height
                block_area = max(r.width * r.height, 1e-6)
                if overlap_area / block_area > 0.5:
                    return True
        return False

    @staticmethod
    def _is_math_equation(text: str) -> bool:
        """Detect if text is likely a mathematical equation using heuristics."""
        if not text or len(text) > 500:
            return False

        # Count mathematical symbols
        math_symbols = ['=', '+', '-', '*', '/', '≠', '≤', '≥', '∞', '√', '∑', '∫', 'π', 'θ', 'α', 'β', 'Δ']
        symbol_count = sum(1 for c in text if c in math_symbols)

        # Check for common equation patterns
        patterns = [
            r'[a-zA-Z]\s*[=]\s*[0-9a-zA-Z]+',  # variable = value
            r'[0-9]+\s*[=]\s*[0-9]+',  # number = number
            r'[a-zA-Z]\s*[+\-*/]\s*[a-zA-Z0-9]+',  # operations
            r'\([^)]*[=+\-*/][^)]*\)',  # parenthesized expressions
            r'\\[a-zA-Z]+',  # LaTeX-like commands
        ]

        pattern_count = sum(1 for pattern in patterns if re.search(pattern, text))

        # Heuristic: needs at least 2 math symbols OR 1 pattern match
        return symbol_count >= 2 or pattern_count >= 1

    # ------------------------------------------------------------------
    # Page analysis (used by routing)
    # ------------------------------------------------------------------

    @staticmethod
    def analyze_page(page: pymupdf.Page) -> dict:
        """Analyze a PDF page to determine its characteristics.

        Returns dict with keys: has_text, text_coverage, has_images, image_count,
        is_likely_scanned, page_width, page_height.
        """
        pw, ph = page.rect.width, page.rect.height
        page_area = pw * ph if pw > 0 and ph > 0 else 1.0

        # Text analysis
        text = page.get_text("text").strip()
        text_blocks = [
            b for b in page.get_text("blocks") if b[6] == _TEXT_BLOCK and b[4].strip()
        ]
        text_area = sum(
            (b[2] - b[0]) * (b[3] - b[1]) for b in text_blocks
        )
        text_coverage = text_area / page_area if page_area > 0 else 0.0

        # Image analysis
        images = page.get_images(full=True)
        image_blocks = [
            b for b in page.get_text("blocks") if b[6] == _IMAGE_BLOCK
        ]
        image_area = sum(
            (b[2] - b[0]) * (b[3] - b[1]) for b in image_blocks
        )
        image_coverage = image_area / page_area if page_area > 0 else 0.0

        # Scanned page heuristic: large image covering most of the page with little text
        is_likely_scanned = (
            image_coverage > 0.7 and text_coverage < 0.05 and len(images) <= 2
        )

        has_meaningful_text = len(text) > 20 and text_coverage > 0.01

        return {
            "has_text": has_meaningful_text,
            "text_char_count": len(text),
            "text_coverage": round(text_coverage, 4),
            "has_images": len(images) > 0,
            "image_count": len(images),
            "image_coverage": round(image_coverage, 4),
            "is_likely_scanned": is_likely_scanned,
            "page_width": pw,
            "page_height": ph,
        }
