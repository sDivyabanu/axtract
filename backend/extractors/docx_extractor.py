"""DOCX extractor.

Walks the document body once, in order, so paragraphs, tables, equations, charts and
pictures keep their true sequence. Beyond python-docx's text/table API it reads:
  * OMML equations (m:oMath / m:oMathPara), converted to LaTeX without a model,
  * native Office charts (word/charts/chartN.xml) with their exact cached values,
  * pictures (DrawingML and legacy VML/OLE previews), whose bytes are passed to the
    region router so charts and formula images inside them are read too,
  * merged table cells (gridSpan / vMerge).
DOCX has no page coordinates, so bbox is null (the viewer locates blocks in a converted PDF).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar, Iterator

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import etree

from extractors.base import BaseExtractor, ExtractionResult
from models.document import BlockType, DocumentBlock
from models.errors import AppError, DocumentError
from services.chart_service import make_chart_block
from services.equation_service import make_equation_block
from services.vision.chart_native import parse_chart_xml
from services.vision.latex import validate_latex
from services.vision.omml import omml_to_latex
from utils import deadline

_M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_VML = "urn:schemas-microsoft-com:vml"
_OFFICE = "urn:schemas-microsoft-com:office:office"
_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"

_BULLET_RE = re.compile(r"^(?:[•●○▪◦‣∙·]|[-–—]\s)\s*\S")
_NUMBERED_RE = re.compile(r"^(?:\d{1,3}|[A-Za-z]|[ivxIVX]{1,5})[.)]\s+\S")
_EMU_PER_PT = 12700


def _ln(el) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


class DocxExtractor(BaseExtractor):
    """Extracts structured content from DOCX files."""

    name: ClassVar[str] = "python-docx"
    supported_extensions: ClassVar[frozenset[str]] = frozenset({"docx"})

    def extract(self, file_path: Path) -> ExtractionResult:
        try:
            doc = docx.Document(str(file_path))
        except Exception as exc:
            raise AppError("INVALID_FILE", f"Could not open DOCX file: {exc}", status_code=422) from exc

        self._doc = doc
        self._result = ExtractionResult(page_count=1)  # real pages come from the preview PDF
        self._counter = 0
        self._chart_no = 0

        try:
            self._walk(doc.element.body)
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._result.errors.append(
                DocumentError(code="DOCUMENT_EXTRACTION_FAILED", message=str(exc), page=1)
            )
        return self._result

    # ------------------------------------------------------------------
    # structure walk
    # ------------------------------------------------------------------

    def _next_id(self, kind: str = "b") -> str:
        bid = f"p1-{kind}{self._counter}"
        self._counter += 1
        return bid

    def _walk(self, container) -> None:
        for child in container.iterchildren():
            deadline.check()
            tag = _ln(child)
            try:
                if tag == "p":
                    self._paragraph(child)
                elif tag == "tbl":
                    self._table(child)
                elif tag == "sdt":
                    content = child.find(qn("w:sdtContent"))
                    if content is not None:
                        self._walk(content)
            except AppError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad element must not drop the document
                self._result.errors.append(
                    DocumentError(code="ELEMENT_EXTRACTION_FAILED", message=f"{tag}: {exc}", page=1)
                )

    # ------------------------------------------------------------------
    # paragraphs
    # ------------------------------------------------------------------

    def _inline_items(self, p_el) -> Iterator[tuple[str, Any]]:
        """Yield ('text', str) | ('math', Converted) | ('drawing', el) | ('vml', el) in order."""

        def run_items(r):
            for sub in r:
                t = _ln(sub)
                if t == "t":
                    yield ("text", sub.text or "")
                elif t == "tab":
                    yield ("text", "\t")
                elif t in ("br", "cr"):
                    yield ("text", "\n")
                elif t == "drawing":
                    yield ("drawing", sub)
                elif t in ("pict", "object"):
                    yield ("vml", sub)
                elif t == "AlternateContent":
                    choice = sub.find(f"{{{_MC}}}Choice")
                    if choice is not None:
                        for inner in choice:
                            if _ln(inner) == "drawing":
                                yield ("drawing", inner)
                elif t in ("oMath", "oMathPara"):
                    yield ("math", omml_to_latex(sub))

        def walk(el):
            for child in el:
                t = _ln(child)
                if t == "r":
                    yield from run_items(child)
                elif t in ("hyperlink", "ins", "smartTag", "fldSimple", "sdtContent"):
                    yield from walk(child)
                elif t == "sdt":
                    content = child.find(qn("w:sdtContent"))
                    if content is not None:
                        yield from walk(content)
                elif t in ("oMath", "oMathPara"):
                    yield ("math", omml_to_latex(child))

        yield from walk(p_el)

    def _paragraph(self, p_el) -> None:
        para = Paragraph(p_el, self._doc._body)
        style_name = (para.style.name if para.style is not None else "") or ""
        buf: list[str] = []
        inline_math: list[Any] = []

        def flush() -> None:
            text = "".join(buf).strip()
            buf.clear()
            if text:
                block = self._paragraph_block(para, p_el, text, style_name)
                self._result.blocks.append(block)
                for conv in inline_math:
                    self._emit_equation(conv, inline=True, parent=block.id)
            inline_math.clear()

        for kind, payload in self._inline_items(p_el):
            if kind == "text":
                buf.append(payload)
            elif kind == "math":
                if payload.display:
                    flush()
                    self._emit_equation(payload, inline=False)
                else:
                    # inline math stays in the text flow as $...$ and is also emitted as
                    # its own (inline) equation block right after the paragraph
                    buf.append(f" ${payload.latex}$ " if payload.latex else "")
                    inline_math.append(payload)
            elif kind in ("drawing", "vml"):
                flush()
                self._picture_or_chart(payload, kind)
        # text of a paragraph that is only inline math becomes its own equation block
        text = "".join(buf).strip()
        if text and inline_math and re.fullmatch(r"(\s*\$[^$]*\$\s*)+", text):
            buf.clear()
            for conv in inline_math:
                self._emit_equation(conv, inline=False)
            inline_math.clear()
            return
        flush()

    def _paragraph_block(self, para, p_el, text: str, style_name: str) -> DocumentBlock:
        sn = style_name.lower()
        ppr = p_el.find(qn("w:pPr"))
        num_pr = ppr.find(qn("w:numPr")) if ppr is not None else None
        outline = ppr.find(qn("w:outlineLvl")) if ppr is not None else None

        if "heading" in sn or sn == "title" or sn == "subtitle":
            block_type = BlockType.HEADING
        elif outline is not None and "list" not in sn:
            block_type = BlockType.HEADING
        elif num_pr is not None or "list" in sn or "bullet" in sn:
            block_type = BlockType.LIST
        elif _BULLET_RE.match(text) or _NUMBERED_RE.match(text):
            block_type = BlockType.LIST
        else:
            block_type = BlockType.PARAGRAPH

        metadata: dict[str, Any] = {"style": style_name or None}
        if block_type == BlockType.HEADING:
            m = re.search(r"(\d+)", style_name)
            if m:
                metadata["heading_level"] = int(m.group(1))
            elif sn == "title":
                metadata["heading_level"] = 1
            elif outline is not None:
                metadata["heading_level"] = int(outline.get(qn("w:val"), "0")) + 1
            else:
                metadata["heading_level"] = 2
        if num_pr is not None:
            ilvl = num_pr.find(qn("w:ilvl"))
            metadata["list_level"] = int(ilvl.get(qn("w:val"), "0")) if ilvl is not None else 0

        runs = para.runs
        if runs:
            run = runs[0]
            if run.font.size:
                metadata["font_size"] = run.font.size.pt
            metadata["is_bold"] = bool(run.bold)
            metadata["is_italic"] = bool(run.italic)

        return DocumentBlock(
            id=self._next_id(),
            type=block_type,
            content=text,
            page=1,
            bbox=None,
            confidence=None,
            extractor=self.name,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # equations
    # ------------------------------------------------------------------

    def _emit_equation(self, conv, inline: bool, parent: str | None = None) -> None:
        if not conv.latex:
            return
        flags = []
        confidence: float | None = 0.98  # exact structural conversion of the author's own equation
        if conv.unsupported:
            flags.append("omml_unsupported_construct")
            confidence = 0.6
        if not validate_latex(conv.latex).ok:
            flags.append("latex_parse_failed")
            confidence = 0.3
        meta = {
            "inline": inline,
            "display": conv.display,
            "confidence_source": "omml_structural_conversion",
        }
        if conv.unsupported:
            meta["omml_unsupported"] = sorted(conv.unsupported)
        if parent:
            meta["parent_block_id"] = parent
        self._result.blocks.append(
            make_equation_block(
                self._next_id("eq"), 1, None, conv.latex, "omml", confidence, flags,
                extractor="omml", extra_metadata=meta,
            )
        )

    # ------------------------------------------------------------------
    # pictures and charts
    # ------------------------------------------------------------------

    def _picture_or_chart(self, el, kind: str) -> None:
        if kind == "drawing":
            chart = next(iter(el.iter(f"{{{_C}}}chart")), None)
            if chart is not None:
                self._native_chart(chart.get(f"{{{_R}}}id"), el)
                return
            blip = next(iter(el.iter(f"{{{_A}}}blip")), None)
            rid = blip.get(f"{{{_R}}}embed") if blip is not None else None
            doc_pr = next(iter(el.iter(f"{{{_WP}}}docPr")), None)
            extent = next(iter(el.iter(f"{{{_WP}}}extent")), None)
            meta: dict[str, Any] = {"media": "picture"}
            if doc_pr is not None and doc_pr.get("descr"):
                meta["alt_text"] = doc_pr.get("descr")
            if extent is not None:
                meta["extent_pt"] = [round(int(extent.get("cx", 0)) / _EMU_PER_PT, 1),
                                     round(int(extent.get("cy", 0)) / _EMU_PER_PT, 1)]
            if rid is None:
                # SmartArt / diagrams / other graphic data: no picture to read
                self._figure(None, meta | {"flags": ["unsupported_graphic"], "needs_review": True})
                return
            self._figure(rid, meta)
        else:  # legacy VML picture or embedded OLE object with a preview image
            imagedata = next(iter(el.iter(f"{{{_VML}}}imagedata")), None)
            ole = next(iter(el.iter(f"{{{_OFFICE}}}OLEObject")), None)
            rid = imagedata.get(f"{{{_R}}}id") if imagedata is not None else None
            meta = {"media": "picture"}
            if ole is not None:
                prog = ole.get("ProgID", "")
                meta["ole_prog_id"] = prog
                if "equation" in prog.lower() or "mathtype" in prog.lower():
                    meta["hint"] = "equation"
            self._figure(rid, meta)

    def _figure(self, rid: str | None, meta: dict[str, Any]) -> None:
        blob, ext = None, ""
        if rid:
            try:
                part = self._doc.part.related_parts[rid]
                blob = part.blob
                ext = Path(str(part.partname)).suffix.lstrip(".").lower()
            except KeyError:
                meta = meta | {"flags": ["image_part_missing"], "needs_review": True}
        bid = self._next_id()
        self._result.blocks.append(
            DocumentBlock(
                id=bid,
                type=BlockType.FIGURE,
                content="[embedded image]",
                page=1,
                bbox=None,
                confidence=None,
                extractor=self.name,
                requires_review=bool(meta.get("needs_review")),
                metadata=meta,
            )
        )
        if blob:
            self._result.assets[bid] = (blob, ext)

    def _native_chart(self, rid: str | None, drawing_el) -> None:
        self._chart_no += 1
        bid = self._next_id("chart")
        try:
            part = self._doc.part.related_parts[rid]
            data = parse_chart_xml(part.blob)
        except Exception as exc:  # noqa: BLE001
            data = None
            self._result.errors.append(
                DocumentError(code="CHART_PARSE_FAILED", message=f"chart {self._chart_no}: {exc}", page=1)
            )
        extent = next(iter(drawing_el.iter(f"{{{_WP}}}extent")), None)
        meta = {"media": "native_chart"}
        if extent is not None:
            meta["extent_pt"] = [round(int(extent.get("cx", 0)) / _EMU_PER_PT, 1),
                                 round(int(extent.get("cy", 0)) / _EMU_PER_PT, 1)]
        if data is None:
            self._result.blocks.append(
                DocumentBlock(
                    id=bid, type=BlockType.FIGURE, content="[chart: no readable data]", page=1,
                    bbox=None, confidence=None, extractor=self.name, requires_review=True,
                    metadata=meta | {"flags": ["chart_data_unreadable"], "needs_review": True},
                )
            )
            return
        # Deterministic cached values: high confidence by design (see confidence_source).
        self._result.blocks.append(make_chart_block(bid, 1, None, data, 0.99, [], meta))

    # ------------------------------------------------------------------
    # tables
    # ------------------------------------------------------------------

    def _table(self, tbl_el) -> None:
        table = Table(tbl_el, self._doc._body)
        grid: list[list[Any]] = []
        for row in table.rows:
            grid.append(list(row.cells))
        if not grid:
            return

        # python-docx returns the same cell object for every grid slot a merged cell covers.
        origin: dict[int, tuple[int, int, int, int]] = {}  # id(tc) -> r0, c0, r1, c1
        for r, cells in enumerate(grid):
            for c, cell in enumerate(cells):
                key = id(cell._tc)
                if key in origin:
                    r0, c0, r1, c1 = origin[key]
                    origin[key] = (min(r0, r), min(c0, c), max(r1, r), max(c1, c))
                else:
                    origin[key] = (r, c, r, c)

        rows: list[list[str | None]] = []
        for r, cells in enumerate(grid):
            out: list[str | None] = []
            for c, cell in enumerate(cells):
                r0, c0, _, _ = origin[id(cell._tc)]
                out.append(cell.text.strip() if (r, c) == (r0, c0) else None)
            rows.append(out)

        regions = [
            (r0, c0, r1 - r0 + 1, c1 - c0 + 1)
            for (r0, c0, r1, c1) in origin.values()
            if r1 > r0 or c1 > c0
        ]
        regions.sort()

        content = "\n".join(" | ".join("" if c is None else c for c in row) for row in rows)
        self._result.blocks.append(
            DocumentBlock(
                id=self._next_id(),
                type=BlockType.TABLE,
                content=content,
                page=1,
                bbox=None,
                confidence=None,
                extractor=self.name,
                metadata={
                    "rows": rows,
                    "row_count": len(rows),
                    "col_count": max(len(r) for r in rows),
                    "header": rows[0],
                    "merged_cells": {"has_merged_cells": bool(regions), "merged_regions": regions},
                },
            )
        )
