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

# Fonts that carry typeset math (Computer Modern math, Symbol, Cambria Math, ...).
_MATH_FONT = re.compile(r"cmmi|cmsy|cmex|msbm|msam|symbol|mathematicalpi|cambria\s*math|stix", re.I)
# Strong math operators / symbols (Unicode math operators + a few common ones).
_MATH_CHARS = frozenset(
    "∑∏∫∬∭∮√∛∞∂∇±∓×÷≈≠≡≤≥≪≫∈∉⊂⊆⊃⊇∪∩∧∨¬→⇒⇔↔∀∃∴∵∝ℝℕℤℚℂ"
)


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

        # 2. Text and image blocks (excluding table regions).
        # NOTE: do not pass custom `flags` here: that replaces PyMuPDF's defaults and
        # silently drops image blocks and the mediabox clip.
        raw_blocks: list[dict] = []
        span_sizes: list[tuple[float, int]] = []

        for item in page.get_text("dict")["blocks"]:
            bbox_raw = (item["bbox"][0], item["bbox"][1], item["bbox"][2], item["bbox"][3])

            if item.get("type", -1) == _IMAGE_BLOCK:
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
            if self._overlaps_tables(bbox_raw, table_rects):
                continue

            lines = item.get("lines", [])
            content = self._join_lines(lines)
            if not content:
                continue

            max_font_size = 0.0
            bold_chars = 0
            total_chars = 0
            math_font = False
            for line in lines:
                for span in line.get("spans", []):
                    n = len(span.get("text", "").strip())
                    if n == 0:
                        continue
                    size = float(span.get("size", 0))
                    max_font_size = max(max_font_size, size)
                    span_sizes.append((size, n))
                    total_chars += n
                    if span.get("flags", 0) & 16 or "bold" in span.get("font", "").lower():
                        bold_chars += n
                    if _MATH_FONT.search(span.get("font", "")):
                        math_font = True

            raw_blocks.append({
                "content": content,
                "bbox": bbox_raw,
                "max_font_size": max_font_size,
                "all_bold": total_chars > 0 and bold_chars / total_chars >= 0.9,
                "line_count": len(lines),
                "math_font": math_font,
                "block_counter": block_counter,
            })
            block_counter += 1

        body_size = self._body_font_size(span_sizes)

        for rb in raw_blocks:
            content = rb["content"]
            fs = rb["max_font_size"]
            short = len(content) <= 200 and rb["line_count"] <= 3

            is_heading = short and fs > body_size * 1.15
            if not is_heading and rb["all_bold"] and len(content) < 100 and rb["line_count"] <= 2:
                is_heading = not content.endswith((".", ",", ";"))

            metadata = {
                "font_size": round(fs, 2),
                "is_bold": rb["all_bold"],
                "body_font_size": round(body_size, 2),
            }
            if not is_heading and self._is_formula_candidate(content, rb["math_font"]):
                # Text-layer math is unreliable; the region router re-reads the rendered
                # crop with the formula model before this becomes an equation block.
                metadata["formula_candidate"] = True

            blocks.append(
                DocumentBlock(
                    id=f"p{page_number}-b{rb['block_counter']}",
                    type=BlockType.HEADING if is_heading else BlockType.PARAGRAPH,
                    content=content,
                    page=page_number,
                    bbox=_normalize_bbox(*rb["bbox"], pw, ph),
                    confidence=None,
                    extractor=self.name,
                    metadata=metadata,
                )
            )

        for b in blocks:
            # bbox is normalized (top-left origin); bbox_pt = bbox * page_size_pt
            b.metadata["page_size_pt"] = [round(pw, 2), round(ph, 2)]
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
    def _join_lines(lines: list[dict]) -> str:
        """Join the lines of a text block: spans concatenate, lines are space-separated,
        and a hyphen at a line end is merged with the next lowercase word."""
        parts: list[str] = []
        for line in lines:
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if not text:
                continue
            if (
                parts
                and len(parts[-1]) > 1
                and parts[-1].endswith("-")
                and parts[-1][-2].isalpha()
                and text[0].islower()
            ):
                parts[-1] = parts[-1][:-1] + text
            else:
                parts.append(text)
        return " ".join(" ".join(parts).split())

    @staticmethod
    def _body_font_size(span_sizes: list[tuple[float, int]]) -> float:
        """Character-weighted median span size: the page's body text size."""
        if not span_sizes:
            return 12.0
        ordered = sorted(span_sizes)
        half = sum(n for _, n in ordered) / 2
        acc = 0
        for size, n in ordered:
            acc += n
            if acc >= half:
                return size or 12.0
        return ordered[-1][0] or 12.0

    @staticmethod
    def _is_formula_candidate(text: str, math_font: bool) -> bool:
        """Cheap pre-filter: does this block look like it carries typeset math?

        Deliberately conservative and NOT used to type the block as an equation.
        It only decides which blocks are worth re-reading with the formula model.
        """
        if not text or len(text) > 160:
            return False
        math_chars = sum(1 for c in text if c in _MATH_CHARS)
        return math_font or math_chars >= 1

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
