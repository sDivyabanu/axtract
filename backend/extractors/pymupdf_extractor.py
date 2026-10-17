"""PyMuPDF extraction for digital PDFs with conservative layout heuristics."""

from __future__ import annotations

import json
import re
from time import perf_counter
from collections import defaultdict
from pathlib import Path
from typing import ClassVar

import pymupdf

from extractors.table_geometry import (split_stacked_rows, native_line_numeric_candidates,
                                       shadows_complete_grid, has_cell_rectangles, indexed_numeric_candidates)
from extractors.text_math import page_has_math_fonts, rebuild_page
from extractors.base import BaseExtractor, ExtractionResult
from extractors.table_geometry import has_ruled_grid, numeric_table_candidates, ruled_text_candidates, is_detached_margin_header, ruled_grid_regions, has_plot_strokes
from models.document import BBox, BlockType, DocumentBlock
from models.errors import AppError, DocumentError

_TEXT_BLOCK = 0
_IMAGE_BLOCK = 1
_FOOTNOTE = re.compile(r"\(([a-z]|\d{1,2})\)$", re.IGNORECASE)
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_NUMERIC = re.compile(r"\d|%|\$|\b(?:19|20)\d{2}\b")
_MATH_FONT = re.compile(
    r"cmmi|cmsy|cmex|msbm|msam|symbol|mathematicalpi|cambria\s*math|stix", re.I
)
_MATH_CHARS = frozenset(
    "\u2211\u220f\u222b\u222c\u222d\u222e\u221a\u221b\u221c\u221e\u2202\u2207\u00b1\u2213\u00d7\u00f7\u2248\u2260\u2261\u2264\u2265\u226a\u226b\u2208\u2209\u2282\u2286\u2283\u2287\u222a\u2229\u2227\u2228\u00ac\u2192\u21d2\u21d4\u2194\u2200\u2203\u2234\u2235\u221d\u210f\u2115\u2124\u211a\u211d\u2102"
)


def _normalize_bbox(x0: float, y0: float, x1: float, y1: float,
                    page_w: float, page_h: float) -> BBox:
    if page_w <= 0 or page_h <= 0:
        return (0.0, 0.0, 1.0, 1.0)
    return (round(x0 / page_w, 6), round(y0 / page_h, 6),
            round(x1 / page_w, 6), round(y1 / page_h, 6))


def _recover_invalid_text_bbox(content: str, page_dict: dict) -> tuple[float, float, float, float] | None:
    """Recover a sentinel text-block box from the matching valid PyMuPDF text span."""
    target = re.sub(r"\W+", " ", content.casefold()).strip()
    if not target:
        return None
    for item in page_dict.get("blocks", []):
        if item.get("type") != _TEXT_BLOCK:
            continue
        span_text = " ".join(
            span.get("text", "")
            for line in item.get("lines", [])
            for span in line.get("spans", [])
        )
        if re.sub(r"\W+", " ", span_text.casefold()).strip() != target:
            continue
        bbox = item.get("bbox")
        if bbox and len(bbox) == 4 and bbox[2] > bbox[0] and bbox[3] > bbox[1]:
            return tuple(float(v) for v in bbox)
    return None


def _align_word_block_numbers(words: list[tuple], page_dict: dict) -> list[tuple]:
    """Words count text blocks; dict/rawdict indices also include image blocks.

    Preserve source order and geometry while translating the text-only ordinal
    into the shared dict/rawdict index used for font and math provenance.
    """
    indices = [i for i,b in enumerate(page_dict.get("blocks", [])) if b.get("type") == _TEXT_BLOCK]
    return [(*w[:5], indices[w[5]] if 0 <= w[5] < len(indices) else w[5], *w[6:]) for w in words]


def _word_lines(words: list[tuple]) -> dict[int, list[tuple[float, float, str]]]:
    """Return each text block's geometrically ordered lines from PyMuPDF words."""
    grouped: dict[tuple[int, int], list[tuple[float, float, float, int, str]]] = defaultdict(list)
    for word in words:
        x0, y0, _x1, y1, text, block_no, line_no, word_no = word[:8]
        grouped[(block_no, line_no)].append((x0, y0, y1, word_no, text))
    result: dict[int, list[tuple[float, float, str]]] = defaultdict(list)
    for (block_no, _line_no), words in grouped.items():
        words.sort(key=lambda item: (item[0], item[3]))
        result[block_no].append((min(w[1] for w in words), max(w[2] for w in words),
                                 " ".join(w[4] for w in words)))
    for block_no in result:
        result[block_no].sort(key=lambda line: line[0])
    return result


def _detach_footnote_markers(text: str) -> tuple[str, list[str]]:
    """Detach parenthesized references anywhere in a line, including fused suffixes."""
    refs: list[str] = []
    words: list[str] = []
    for word in text.split():
        if _FOOTNOTE.fullmatch(word):
            refs.append(word[1:-1])
            continue
        while match := re.search(r"\(([a-z]|\d{1,2})\)$", word, re.IGNORECASE):
            refs.append(match.group(1))
            word = word[:match.start()]
        if word:
            words.append(word)
    return " ".join(words), refs


_SUPERSCRIPT_DIGITS = str.maketrans("0123456789", "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079")


def strip_superscript_refs(content: str, refs: list[str]) -> str:
    """Remove detected superscript footnote markers without touching the surrounding punctuation.

    A marker LIST such as ``work1,2`` is removed as one run (its inner comma goes with it), but a comma that follows the
    last marker belongs to the sentence (``work2, showed`` -> ``work, showed``). Deterministic: markers are matched
    longest-first from a sorted list, never in set-iteration order."""
    digits = sorted({r for r in refs if r.isdigit()}, key=lambda r: (-len(r), r))
    if not digits:
        return content
    alt = "|".join(re.escape(r) for r in digits)
    # A raised digit right after a short unit or variable (m2, cm3, x2) is an exponent: keep it as a real superscript
    # character (m\u00b2). Footnote markers follow words and punctuation, so they are still removed below.
    content = re.sub(rf"(?<![A-Za-z])([A-Za-z]{{1,2}})({alt})(?!\w)", lambda m: m.group(1) + m.group(2).translate(_SUPERSCRIPT_DIGITS), content)
    content = re.sub(rf"(?<=[A-Za-z])(?:{alt})(?:,(?:{alt}))*(?!\w)", "", content)
    for ref in digits:
        # a marker glued to punctuation or a closing bracket ("al.2", "(see)3"); a digit set off by white space is a real
        # number ("3 m", "page 2") and is never deleted just because a footnote with the same digit exists in the block
        content = re.sub(rf"(?<=[^\w\s]){re.escape(ref)}(?!\w)", "", content)
    return content


def _cell_token_count(cell: object) -> int:
    return len(str(cell).split()) if cell is not None else 0


def table_plausibility(rows: list[list[object]], col_count: int) -> tuple[bool, list[str]]:
    """Assess cell content structure without treating one warning as conclusive."""
    row_count = len(rows)
    total = sum(len(row) for row in rows)
    populated = [[cell for cell in row if cell is not None and str(cell).strip()] for row in rows]
    nonempty_count = sum(len(row) for row in populated)
    empty_ratio = 1 - nonempty_count / total if total else 1.0
    populated_rows = sum(bool(row) for row in populated)
    repeated_columns = sum(
        sum(col < len(row) and row[col] is not None and bool(str(row[col]).strip()) for row in rows) >= 2
        for col in range(col_count)
    )
    numeric_columns = sum(
        sum(col < len(row) and row[col] is not None
            and bool(re.search(r"\d", str(row[col]))) for row in rows) >= 2
        for col in range(col_count)
    )
    token_counts = [_cell_token_count(cell) for row in rows for cell in row if cell is not None]
    total_tokens = sum(token_counts)
    max_cell_tokens = max(token_counts, default=0)
    coherent_columns = repeated_columns >= min(2, col_count)
    coherent_rows = populated_rows >= min(2, row_count)
    structured_grid = coherent_columns and coherent_rows and nonempty_count >= 4
    numeric_grid = numeric_columns >= 2 and coherent_rows
    header_like = bool(rows and any(
        cell is not None and re.search(r"[A-Za-z]{2,}", str(cell))
        for cell in rows[0]
    ))
    grouped_year_headers = any(
        sum(cell is not None and bool(str(cell).strip()) for cell in row) == 1
        and any(cell is not None and _YEAR.fullmatch(str(cell).strip()) for cell in row)
        for row in rows
    )
    reasons: list[str] = []
    if grouped_year_headers:
        reasons.append("grouped_header_rows")
    if total and empty_ratio > 0.6:
        reasons.append("sparse_grid")
    elif total and empty_ratio >= 0.2:
        # Repeated structure can still make this a valid table, but a high
        # share of blank cells often reflects merged cells or grouped headers
        # that the flat cell grid cannot represent cleanly.
        reasons.append("sparse_cells")
    giant_dominates = max_cell_tokens > 25 and max_cell_tokens >= max(1, total_tokens * 0.75)
    if giant_dominates:
        reasons.append("giant_cell")
    if row_count < 2 or col_count < 2:
        reasons.append("insufficient_dimensions")
    # Sparse and long cells can be valid when multiple rows and columns repeat.
    strong_table_structure = structured_grid or (numeric_grid and header_like)
    rejected = (("sparse_grid" in reasons or "giant_cell" in reasons) and not strong_table_structure)
    rejected = rejected or "insufficient_dimensions" in reasons
    return not rejected, reasons


def _has_local_chart_signal(words: list[tuple], drawings: list[dict],
                            bbox: tuple[float, float, float, float]) -> bool:
    """Find chart-like marks without mistaking a ruled table for vector art."""
    plot_strokes = has_plot_strokes(drawings, bbox)
    if has_ruled_grid(drawings, bbox) and not plot_strokes:
        return False
    region = pymupdf.Rect(bbox)
    local_drawings = 0
    horizontal_rules = 0
    vertical_rules = 0
    chart_marks = 0
    region_area = max(region.width * region.height, 1.0)
    for drawing in drawings:
        rect = drawing.get("rect")
        if rect is not None:
            rect = pymupdf.Rect(rect)
            overlap = rect & region
            if not overlap.is_empty and overlap.get_area() > 0:
                local_drawings += 1
                if rect.width >= 0.4 * region.width and rect.height <= 2.0:
                    horizontal_rules += 1
                if rect.height >= 0.4 * region.height and rect.width <= 2.0:
                    vertical_rules += 1
                if rect.width >= 5.0 and rect.height >= 5.0 \
                        and rect.width * rect.height >= 0.001 * region_area:
                    chart_marks += 1
    # Repeated horizontal and vertical rules are direct table structure. They
    # outweigh numeric density, which is common in financial tables and charts.
    if horizontal_rules >= 2 and vertical_rules >= 2 and not plot_strokes:
        return False
    numeric_labels = sum(
        bool(_NUMERIC.search(str(word[4]))) and len(str(word[4])) <= 16
        and word[0] >= bbox[0] and word[2] <= bbox[2]
        and word[1] >= bbox[1] and word[3] <= bbox[3]
        for word in words
    )
    return ((local_drawings >= 8 and chart_marks >= 6) or plot_strokes) and numeric_labels >= 6


def _is_formula_candidate(text: str, math_font: bool) -> bool:
    """Cheap conservative pre-filter for text-layer math."""
    if not text or len(text) > 160:
        return False
    math_chars = sum(1 for c in text if c in _MATH_CHARS)
    return math_font or math_chars >= 1


def _has_repeated_numeric_columns(words: list[tuple]) -> bool:
    """Look for numeric x positions recurring on separate text lines."""
    numeric = sorted((word[0], word[6]) for word in words
                     if _NUMERIC.search(str(word[4])))
    clusters: list[list[tuple[float, int]]] = []
    for x0, line_no in numeric:
        if not clusters or x0 - clusters[-1][-1][0] > 9.0:
            clusters.append([])
        clusters[-1].append((x0, line_no))
    repeated = sum(len({line for _, line in cluster}) >= 3 for cluster in clusters)
    return repeated >= 2


def _has_aligned_text_columns(words: list[tuple]) -> bool:
    """Find a repeated cell gutter, not merely repeated word positions in prose."""
    lines: dict[tuple[int, int], list[tuple[float, float, float]]] = defaultdict(list)
    heights = []
    for word in words:
        x0, y0, x1, y1, _text, block_no, line_no = word[:7]
        lines[(block_no, line_no)].append((float(x0), float(x1), float(y1) - float(y0)))
        heights.append(float(y1) - float(y0))
    if len(lines) < 3 or not heights:
        return False

    median_height = sorted(heights)[len(heights) // 2]
    gutter_threshold = max(18.0, median_height * 1.5)
    gutter_starts: list[tuple[float, tuple[int, int]]] = []
    for line_key, line_words in lines.items():
        line_words.sort(key=lambda item: item[0])
        for prior, current in zip(line_words, line_words[1:]):
            if current[0] - prior[1] >= gutter_threshold:
                gutter_starts.append((current[0], line_key))

    # A real text table repeats the start of a second cell on separate lines.
    # Prose word positions drift with word lengths and normally do not repeat a
    # large horizontal gutter at a stable x coordinate.
    gutter_starts.sort()
    clusters: list[list[tuple[float, tuple[int, int]]]] = []
    for item in gutter_starts:
        if not clusters or item[0] - clusters[-1][-1][0] > 12.0:
            clusters.append([])
        clusters[-1].append(item)
    return any(len({line for _, line in cluster}) >= 3 for cluster in clusters)


class PyMuPDFExtractor(BaseExtractor):
    """Extract text, tables and figure candidates from digital PDFs."""

    name: ClassVar[str] = "pymupdf"
    supported_extensions: ClassVar[frozenset[str]] = frozenset({"pdf"})

    def __init__(self) -> None:
        self.page_diagnostics: dict[int, dict[str, float | int | bool]] = {}

    def extract(self, file_path: Path) -> ExtractionResult:
        try:
            doc = pymupdf.open(stream=file_path.read_bytes(), filetype="pdf")
        except Exception as exc:
            raise AppError("INVALID_FILE", "The PDF could not be opened.", status_code=422) from exc
        with doc:
            if doc.needs_pass:
                raise AppError("ENCRYPTED_FILE", "Password-protected PDFs are not supported yet.", status_code=422)
            result = ExtractionResult(page_count=doc.page_count)
            for number, page in enumerate(doc, start=1):
                try:
                    result.blocks.extend(self._extract_page(page, number))
                except Exception as exc:
                    result.errors.append(DocumentError(code="PAGE_EXTRACTION_FAILED",
                                                       message=str(exc) or "Unknown page error.", page=number))
            return result

    def _extract_page(self, page: pymupdf.Page, page_number: int) -> list[DocumentBlock]:
        page_started = perf_counter()
        pw, ph = page.rect.width, page.rect.height
        # Preserve PyMuPDF's dict defaults (including image blocks) while
        # retaining whitespace. Passing only the whitespace flag overrides
        # the defaults and silently drops embedded PDF figures.
        page_dict = page.get_text(
            "dict", flags=pymupdf.TEXTFLAGS_DICT | pymupdf.TEXT_PRESERVE_WHITESPACE
        )
        raw_words = _align_word_block_numbers(page.get_text("words"), page_dict)
        source_vocabulary = {str(w[4]).strip(".,;:!?()[]\"\'").casefold() for w in raw_words}
        word_lines = _word_lines(raw_words)
        spans_by_block: dict[int, list[dict]] = defaultdict(list)
        for index, item in enumerate(page_dict["blocks"]):
            for line in item.get("lines", []):
                for span in line.get("spans", []):
                    spans_by_block[index].append(span)

        body_sizes = [s.get("size", 0) for spans in spans_by_block.values() for s in spans
                      if s.get("text", "").strip() and s.get("size", 0) > 0]
        median_size = sorted(body_sizes)[len(body_sizes) // 2] if body_sizes else 12.0
        lines_by_block = word_lines
        blocks: list[DocumentBlock] = []
        table_rects: list[pymupdf.Rect] = []
        counter = 0
        drawing_started = perf_counter()
        drawings = page.get_drawings()
        drawing_seconds = perf_counter() - drawing_started
        chart_signal = len(drawings) >= 20
        short_numeric_labels = sum(bool(_NUMERIC.search(str(w[4]))) and len(str(w[4]).split()) <= 3
                                   for w in raw_words) >= 12
        chart_signal = chart_signal and short_numeric_labels

        # Keep plausible grids as tables. Any rejected grid is retained as a reviewable figure.
        table_started = perf_counter()
        fallback_used = False
        table_detection_count = 0
        aligned_columns = _has_aligned_text_columns(raw_words)
        numeric_columns = _has_repeated_numeric_columns(raw_words)
        # Chart-like pages still need table candidates examined so implausible
        # numeric grids can be retained as reviewable figures. The tighter
        # aligned-column guard is only the fallback for borderless text tables.
        has_table_evidence = (chart_signal or numeric_columns or aligned_columns
                              or bool(ruled_grid_regions(drawings)) or has_cell_rectangles(drawings))
        found_tables = None
        if has_table_evidence:
            table_detection_count = 1
            try:
                # Pathological drawing count guard: if drawing primitives exceed 20,000,
                # scanning vector curves for line borders causes a 15-25s C-level stall.
                # Use text-based table strategy instead on such vector-dense pages.
                cdrawings_count = len(page.get_cdrawings())
                if cdrawings_count <= 20000:
                    found_tables = page.find_tables()
                else:
                    found_tables = page.find_tables(strategy="text")
            except Exception:
                found_tables = None
        if aligned_columns and (not found_tables or not found_tables.tables):
            # Text-rule candidate discovery can recover borderless grids. The
            # plausibility gate below rejects page-wide pseudo-tables. Ignore
            # broad, high-column candidates that are usually prose segmentation.
            try:
                fallback_used = True
                table_detection_count += 1
                text_candidates = page.find_tables(strategy="text")
                page_area = max(pw * ph, 1.0)
                candidates = [table for table in text_candidates.tables
                              if table.col_count <= 6
                              and (table.bbox[2] - table.bbox[0]) * (table.bbox[3] - table.bbox[1])
                              / page_area < 0.5]
                found_tables = type("TableCandidates", (), {"tables": candidates})()
            except Exception:
                found_tables = found_tables
        table_seconds = perf_counter() - table_started
        if found_tables:
            for table in found_tables.tables:
                rows = table.extract()
                is_plausible, reasons = table_plausibility(rows, table.col_count)
                bbox = tuple(table.bbox)
                # Page-level vector density is only a hint. A detected grid with
                # repeated rows/columns remains a table; sparse/giant candidates
                # on a chart-like page are more likely chart artifacts.
                region_chart_signal = _has_local_chart_signal(raw_words, drawings, bbox)
                if region_chart_signal:
                    is_plausible = False
                    reasons.append("chart_signal")
                # Rejected text grids must not suppress their original words.
                # Genuine chart regions retain the existing chart routing behavior.
                if is_plausible or region_chart_signal:
                    table_rects.append(pymupdf.Rect(table.bbox))
                if is_plausible:
                    block = self._table_block(
                        table, page_number, counter, pw, ph,
                        [span for spans in spans_by_block.values() for span in spans],
                        source_words=raw_words,
                    )
                    if reasons:
                        block.requires_review = True
                        block.metadata["reason_flags"] = sorted(set(reasons))
                else:
                    if not region_chart_signal:
                        # Original text is emitted below. A duplicate figure can
                        # win layout deduplication and hide that text in Markdown.
                        continue
                    labels = self._raw_region_text(raw_words, bbox)
                    block = DocumentBlock(
                        id=f"p{page_number}-b{counter}", type=BlockType.FIGURE,
                        content=labels or "[figure/chart region]", page=page_number,
                        bbox=_normalize_bbox(*bbox, pw, ph), confidence=None, extractor=self.name,
                        requires_review=True,
                        metadata={"raw_labels": labels[:5000], "crop_bbox": _normalize_bbox(*bbox, pw, ph),
                                  "reason_flags": sorted(set(reasons)), "row_count": len(rows),
                                  "col_count": table.col_count},
                    )
                blocks.append(block)
                counter += 1

        # Recover borderless numeric grids from remaining native words. This
        # bypasses neither accepted ruled tables nor genuine chart regions.
        remaining = self._words_outside_tables(raw_words, table_rects)
        candidates = [candidate for candidate in ruled_text_candidates(remaining, drawings)
                      if not any((pymupdf.Rect(candidate["bbox"]) & rect).get_area() > 1 for rect in table_rects)
                      and not _has_local_chart_signal(raw_words, drawings, candidate["bbox"])]
        for candidate in candidates:
            table_rects.append(pymupdf.Rect(candidate["bbox"]))
        # Native cell lines disambiguate tightly spaced numeric rows next to
        # unrelated prose, before the broader word-alignment fallback is used.
        word_candidates = numeric_table_candidates(self._words_outside_tables(raw_words, table_rects), drawings)
        remaining_words = self._words_outside_tables(raw_words, table_rects)
        for candidate in (native_line_numeric_candidates(remaining_words)
                          + indexed_numeric_candidates(remaining_words, drawings)):
            if shadows_complete_grid(candidate, word_candidates):
                continue
            if (not any((pymupdf.Rect(candidate["bbox"]) & rect).get_area() > 1 for rect in table_rects)
                    and not _has_local_chart_signal(raw_words, drawings, candidate["bbox"])):
                candidates.append(candidate)
                table_rects.append(pymupdf.Rect(candidate["bbox"]))
        numeric_candidates = [candidate for candidate in numeric_table_candidates(self._words_outside_tables(raw_words, table_rects), drawings)
                              if not any((pymupdf.Rect(candidate["bbox"]) & rect).get_area() > 1 for rect in table_rects)]
        for candidate in candidates + numeric_candidates:
            bbox = candidate["bbox"]
            if _has_local_chart_signal(raw_words, drawings, bbox):
                continue
            rows = candidate["rows"]
            blocks.append(DocumentBlock(
                id=f"p{page_number}-b{counter}", type=BlockType.TABLE,
                content="\n".join(" | ".join(row) for row in rows), page=page_number,
                bbox=_normalize_bbox(*bbox, pw, ph), confidence=None, extractor=self.name,
                requires_review=True,
                metadata={"rows": rows, "row_count": len(rows), "col_count": len(rows[0]),
                          "header": rows[0], "source": candidate.get("source", "native_numeric_geometry"),
                          "cell_bboxes": [[_normalize_bbox(*cell, pw, ph) if cell else None
                                           for cell in row] for row in candidate["cell_bboxes"]],
                          "row_pages": [page_number] * len(rows)},
            ))
            table_rects.append(pymupdf.Rect(bbox))
            counter += 1

        # A text block can straddle a table and surrounding prose. Remove only
        # words fully contained in accepted table/chart regions, not whole blocks.
        lines_by_block = _word_lines(self._words_outside_tables(raw_words, table_rects))
        # Rebuild only native-owned glyphs; accepted table/chart text has another owner.
        page_math = None
        if page_has_math_fonts(page_dict):
            try:
                raw = page.get_text(
                    "rawdict", flags=pymupdf.TEXTFLAGS_DICT | pymupdf.TEXT_PRESERVE_WHITESPACE
                )
                if len(raw.get("blocks", [])) == len(page_dict["blocks"]):
                    retained = set(self._words_outside_tables(raw_words, table_rects))
                    owned = [word[:4] for word in raw_words if word not in retained]
                    page_math = rebuild_page(raw, drawings, pw, excluded_rects=owned,
                                             excluded_rule_rects=table_rects)
            except Exception:  # noqa: BLE001 - preserve the plain-text fallback
                page_math = None

        font_sizes: list[float] = []
        raw_blocks: list[dict] = []
        for block_index, item in enumerate(page_dict["blocks"]):
            if item.get("type", -1) == _IMAGE_BLOCK:
                bbox = tuple(item["bbox"])
                if not self._overlaps_tables(bbox, table_rects):
                    blocks.append(DocumentBlock(id=f"p{page_number}-b{counter}", type=BlockType.FIGURE,
                        content="[image]", page=page_number, bbox=_normalize_bbox(*bbox, pw, ph),
                        confidence=None, extractor=self.name,
                        metadata={"image_width": item.get("width", 0), "image_height": item.get("height", 0)}))
                    counter += 1
                continue
            if item.get("type", -1) != _TEXT_BLOCK:
                continue
            bbox = tuple(item["bbox"])
            if page_math is not None and block_index in page_math.blocks:
                content = page_math.blocks[block_index]
                if not content.strip():
                    continue  # consumed once into a neighbouring reconstructed block
                spans = spans_by_block.get(block_index, [])
                max_size = max((span.get("size", 0) for span in spans), default=0.0)
                font_sizes.append(max_size)
                raw_blocks.append({"content": content.strip(), "bbox": bbox, "font_size": max_size,
                                   "is_bold": any(span.get("flags", 0) & (1 << 4) for span in spans),
                                   "math_font": True, "text_layer_math": True,
                                   "source_block_indices": page_math.source_blocks.get(block_index, [block_index]),
                                   "footnote_refs": [], "superscript_spans": []})
                continue
            lines = lines_by_block.get(block_index, [])
            if not lines:
                continue
            heights = [max(1.0, y1 - y0) for y0, y1, _ in lines]
            median_line_height = sorted(heights)[len(heights) // 2]
            parts: list[str] = []
            block_refs: list[str] = []
            wrap_repairs: list[str] = []
            native_math = any(_MATH_FONT.search(span.get("font", "")) for span in spans_by_block.get(block_index, []))
            for i, (y0, _y1, text) in enumerate(lines):
                prior_word = re.search(r"([A-Za-z]+)-$", parts[-1]) if parts else None
                next_word = re.match(r"([a-z]+)", text)
                joined = prior_word.group(1)+next_word.group(1) if prior_word and next_word else ""
                if (joined and not native_math and joined.casefold() in source_vocabulary
                        and y0-lines[i-1][1]<=.5*median_line_height):
                    parts[-1] = parts[-1][:-1]
                    wrap_repairs.append(joined)
                elif parts and y0 - lines[i - 1][1] > 0.5 * median_line_height:
                    parts.append("\n")
                elif parts:
                    parts.append(" ")
                text, refs = _detach_footnote_markers(text)
                block_refs.extend(refs)
                parts.append(text)
            content = "".join(parts)
            if not content.strip():
                continue
            spans = spans_by_block.get(block_index, [])
            max_size = max((span.get("size", 0) for span in spans), default=0.0)
            is_bold = any(span.get("flags", 0) & (1 << 4) for span in spans)
            math_font = any(_MATH_FONT.search(span.get("font", "")) for span in spans)
            superscript_refs = self._superscript_refs(spans, median_size)
            content = strip_superscript_refs(content, superscript_refs)
            font_sizes.append(max_size)
            raw_blocks.append({"content": content.strip(), "bbox": bbox, "font_size": max_size,
                               "is_bold": is_bold, "math_font": math_font,
                               "footnote_refs": block_refs,
                               "superscript_spans": superscript_refs, "line_wrap_repairs": wrap_repairs})

        if chart_signal:
            # PyMuPDF sometimes leaves numeric chart labels outside its guessed
            # table rectangle. Fold nearby number-only text into the chart's raw
            # labels so it is not presented as detached prose.
            # Only rejected table/chart candidates carry a crop_bbox. Embedded
            # image figures are independent regions and must not receive
            # nearby numeric labels or enter this geometry adjustment.
            figures = [block for block in blocks if block.type == BlockType.FIGURE
                       and "crop_bbox" in block.metadata]
            retained: list[dict] = []
            for raw in raw_blocks:
                tokens = raw["content"].split()
                numeric_only = bool(tokens) and all(
                    re.fullmatch(r"\$?\d[\d,]*(?:\.\d+)?%?", token) or token == "%"
                    for token in tokens
                )
                if not numeric_only or not figures:
                    retained.append(raw)
                    continue
                x0, y0, x1, y1 = raw["bbox"]
                candidates = []
                for figure in figures:
                    fx0, fy0, fx1, fy1 = figure.bbox
                    gap = max(0.0, fy0 - y1 / ph, y0 / ph - fy1)
                    candidates.append((gap, figure))
                gap, figure = min(candidates, key=lambda item: item[0])
                if gap > 0.12:
                    retained.append(raw)
                    continue
                label = raw["content"]
                prior_labels = figure.metadata.get("raw_labels", "")
                if label not in prior_labels:
                    figure.metadata["raw_labels"] = (prior_labels + " " + label).strip()[:5000]
                figure.content = figure.metadata["raw_labels"]
                fx0, fy0, fx1, fy1 = figure.metadata["crop_bbox"]
                normalized = _normalize_bbox(x0, y0, x1, y1, pw, ph)
                crop = (min(fx0, normalized[0]), min(fy0, normalized[1]),
                        max(fx1, normalized[2]), max(fy1, normalized[3]))
                figure.bbox = crop
                figure.metadata["crop_bbox"] = crop
            raw_blocks = retained

        page_median = sorted(font_sizes)[len(font_sizes) // 2] if font_sizes else median_size
        for raw in raw_blocks:
            content = raw["content"]
            bbox = raw["bbox"]
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                bbox = _recover_invalid_text_bbox(content, page_dict) or bbox
            word_count = len(content.split())
            has_numeric_run = bool(_YEAR.search(content)) or len(_NUMERIC.findall(content)) >= 3
            clearly_large = raw["font_size"] >= max(page_median * 1.25, page_median + 2.0)
            heading = clearly_large and word_count <= 12 and not has_numeric_run
            metadata = {
                "font_size": round(raw["font_size"], 2),
                "is_bold": raw["is_bold"],
                "body_font_size": round(page_median, 2),
                "line_wrap_repairs": raw.get("line_wrap_repairs", []),
            }
            if raw.get("text_layer_math"):
                metadata["text_layer_math"] = True
                metadata["source_block_indices"] = raw["source_block_indices"]
                heading = heading and "$" not in content
            elif not heading and _is_formula_candidate(content, raw.get("math_font", False)):
                metadata["formula_candidate"] = True
            refs = list(raw["footnote_refs"])
            refs.extend(raw["superscript_spans"])
            if refs:
                metadata["footnote_refs"] = list(dict.fromkeys(refs))
            margin_header = not heading and not metadata.get("formula_candidate") and is_detached_margin_header(raw, raw_blocks, pw, ph, page_median)
            if margin_header:
                metadata["margin_header_evidence"] = "detached_small_full_width_above_multicolumn_body"
            blocks.append(DocumentBlock(id=f"p{page_number}-b{counter}",
                type=BlockType.HEADER if margin_header else (BlockType.HEADING if heading else BlockType.PARAGRAPH),
                content=content, page=page_number, bbox=_normalize_bbox(*bbox, pw, ph),
                confidence=None, extractor=self.name, requires_review=margin_header, metadata=metadata))
            counter += 1
        for block in blocks:
            block.metadata["page_size_pt"] = [round(pw, 2), round(ph, 2)]
        self.page_diagnostics[page_number] = {
            "word_count": len(raw_words),
            "span_count": sum(len(spans) for spans in spans_by_block.values()),
            "drawing_count": len(drawings),
            "fallback_used": fallback_used,
            "table_detection_count": table_detection_count,
            "page_seconds": perf_counter() - page_started,
            "drawing_seconds": drawing_seconds,
            "table_seconds": table_seconds,
            "block_count": len(blocks),
        }
        return blocks

    # ------------------------------------------------------------------
    # Table handling
    # ------------------------------------------------------------------

    def _table_block(
        self, table, page_number: int, block_counter: int, pw: float, ph: float,
        source_spans: list[dict] | None = None,
        source_words: list[tuple] | None = None,
    ) -> DocumentBlock:
        """Convert a PyMuPDF table to a DocumentBlock with structured content."""
        rows = table.extract()
        rows, symbol_recoveries = self._recover_table_symbols(table, rows, source_spans or [])
        source_boxes = [[cell for cell in row.cells] for row in getattr(table, "rows", [])]
        rows, source_boxes, row_repairs = split_stacked_rows(rows, source_boxes, source_words or [])
        text_lines: list[str] = []
        for row in rows:
            cells = [str(c) if c is not None else "" for c in row]
            text_lines.append(" | ".join(cells))
        content = "\n".join(text_lines)

        cell_bboxes: list[list] = []
        try:
            for row in source_boxes:
                cell_bboxes.append([
                    list(_normalize_bbox(*cell, pw, ph)) if cell is not None else None
                    for cell in row
                ])
        except Exception:  # noqa: BLE001 - cell geometry is a nicety, never fatal
            cell_bboxes = []

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
                "symbol_recoveries": symbol_recoveries,
                "structural_repairs": row_repairs,
                "cell_bboxes": cell_bboxes if len(cell_bboxes) == len(rows) else [],
                "row_pages": [page_number] * len(rows),
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _superscript_refs(spans: list[dict], median_size: float) -> list[str]:
        refs: list[str] = []
        for span in spans:
            text = (span.get("text") or "").strip()
            size = span.get("size", median_size)
            flags = span.get("flags", 0)
            if text and (flags & 1) and median_size and size < median_size * 0.75:
                cleaned = text.strip("()[]* ,")
                refs.extend(re.findall(r"\d{1,2}|[a-z]", cleaned, re.IGNORECASE))
        return refs

    @staticmethod
    def _recover_table_symbols(table, rows: list[list], spans: list[dict]) -> tuple[list[list], list[dict]]:
        """Decode a verified Wingdings arrow only inside its original table cell.

        Do not globally replace private-use characters: the same codepoint in a
        different font can mean something else. Missing geometry is left alone.
        """
        recovered = [list(row) for row in rows]
        changes: list[dict] = []
        geometry = getattr(table, "rows", [])
        for r, row in enumerate(rows):
            boxes = getattr(geometry[r], "cells", []) if r < len(geometry) else []
            for c, text in enumerate(row):
                if not isinstance(text, str) or "\uf0e0" not in text or c >= len(boxes) or not boxes[c]:
                    continue
                rect = pymupdf.Rect(boxes[c])
                relevant = []
                for span in spans:
                    bbox = span.get("bbox")
                    if bbox and "\uf0e0" in span.get("text", ""):
                        center = pymupdf.Point((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
                        if center in rect:
                            relevant.append(span)
                if (relevant and all(span.get("font", "").split("+")[-1].casefold() == "wingdings" for span in relevant)
                        and sum(span["text"].count("\uf0e0") for span in relevant) == text.count("\uf0e0")):
                    recovered[r][c] = text.replace("\uf0e0", "\u2192")
                    changes.append({"row": r, "column": c, "font": "Wingdings",
                                    "source": "\uf0e0", "unicode": "\u2192"})
        return recovered, changes

    @staticmethod
    def _raw_region_text(words: list[tuple], bbox: tuple[float, float, float, float]) -> str:
        x0, y0, x1, y1 = bbox
        selected = [str(w[4]) for w in words if w[0] >= x0 - 1 and w[2] <= x1 + 1
                    and w[1] >= y0 - 1 and w[3] <= y1 + 1]
        return " ".join(selected)

    @staticmethod
    def _words_outside_tables(words: list[tuple], table_rects: list[pymupdf.Rect]) -> list[tuple]:
        """Keep source words unless a replacement region fully contains them."""
        return [word for word in words if not any(
            word[0] >= rect.x0 - 1 and word[2] <= rect.x1 + 1
            and word[1] >= rect.y0 - 1 and word[3] <= rect.y1 + 1
            for rect in table_rects
        )]

    @staticmethod
    def _overlaps_tables(bbox: tuple[float, float, float, float], table_rects: list[pymupdf.Rect]) -> bool:
        rect = pymupdf.Rect(bbox)
        for table_rect in table_rects:
            intersection = rect & table_rect
            if not intersection.is_empty:
                area = intersection.width * intersection.height
                if area / max(rect.width * rect.height, 1e-6) > 0.5:
                    return True
        return False

    @staticmethod
    def analyze_page(page: pymupdf.Page) -> dict:
        pw, ph = page.rect.width, page.rect.height
        area = pw * ph if pw > 0 and ph > 0 else 1.0
        # One text-layout parse provides both content and geometry. Avoid repeated
        # page parsing before the extractor processes the page itself.
        text_dict = page.get_text(
            "dict", flags=pymupdf.TEXTFLAGS_DICT | pymupdf.TEXT_PRESERVE_WHITESPACE
        )
        text_blocks = [
            block for block in text_dict["blocks"]
            if block.get("type") == _TEXT_BLOCK
            and any(span.get("text", "").strip()
                    for line in block.get("lines", [])
                    for span in line.get("spans", []))
        ]
        text_char_count = sum(
            len(span.get("text", ""))
            for block in text_blocks
            for line in block.get("lines", [])
            for span in line.get("spans", [])
        )
        text_area = sum(
            (block["bbox"][2] - block["bbox"][0])
            * (block["bbox"][3] - block["bbox"][1])
            for block in text_blocks
        )
        text_coverage = text_area / area
        images = page.get_images(full=True)
        image_blocks = [b for b in text_dict["blocks"] if b.get("type") == _IMAGE_BLOCK]
        image_area = sum(
            (block["bbox"][2] - block["bbox"][0])
            * (block["bbox"][3] - block["bbox"][1])
            for block in image_blocks
        )
        image_coverage = image_area / area
        return {
            "has_text": text_char_count > 20 and text_coverage > 0.01,
            "text_char_count": text_char_count,
            "text_coverage": round(text_coverage, 4),
            "has_images": len(images) > 0,
            "image_count": len(images),
            "image_coverage": round(image_coverage, 4),
            "is_likely_scanned": image_coverage > 0.7 and text_coverage < 0.05 and len(images) <= 2,
            "page_width": pw,
            "page_height": ph,
        }
