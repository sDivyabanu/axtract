"""PPTX extractor.

Per slide: titles, text, lists, tables (with merged cells), pictures (bytes passed to the
region router), native charts (exact cached values) and OMML equations (a14:m).
Shapes inside groups are visited with their coordinates transformed to slide space. Every
shape is isolated: one broken shape is reported in `errors` and never drops the slide.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from lxml import etree
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Emu

from extractors.base import BaseExtractor, ExtractionResult
from models.document import BBox, BlockType, DocumentBlock
from models.errors import AppError, DocumentError
from services.chart_service import make_chart_block
from services.equation_service import make_equation_block
from services.vision.chart_native import parse_chart_xml
from services.vision.latex import validate_latex
from services.vision.omml import omml_to_latex
from utils import deadline

_M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"

_BULLETS = ("•", "-", "–", "●", "○", "▪", "◦")


def _ln(el) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


class _Xform:
    """Affine map from a group's child coordinate space to slide space (EMU)."""

    def __init__(self, sx=1.0, sy=1.0, tx=0.0, ty=0.0):
        self.sx, self.sy, self.tx, self.ty = sx, sy, tx, ty

    def apply(self, left, top, width, height):
        return (left * self.sx + self.tx, top * self.sy + self.ty, width * self.sx, height * self.sy)

    def child(self, group) -> "_Xform":
        """Transform for the children of `group` (whose own geometry is in this space)."""
        xfrm = group._element.grpSpPr.find(f"{{{_A}}}xfrm")
        if xfrm is None:
            return self
        off, ext = xfrm.find(f"{{{_A}}}off"), xfrm.find(f"{{{_A}}}ext")
        ch_off, ch_ext = xfrm.find(f"{{{_A}}}chOff"), xfrm.find(f"{{{_A}}}chExt")
        if None in (off, ext, ch_off, ch_ext) or int(ch_ext.get("cx", 0)) == 0 or int(ch_ext.get("cy", 0)) == 0:
            return self
        gl, gt = self.apply(int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy")))[:2]
        gw, gh = int(ext.get("cx")) * self.sx, int(ext.get("cy")) * self.sy
        csx, csy = gw / int(ch_ext.get("cx")), gh / int(ch_ext.get("cy"))
        return _Xform(csx, csy, gl - int(ch_off.get("x")) * csx, gt - int(ch_off.get("y")) * csy)


class PptxExtractor(BaseExtractor):
    """Extracts structured content from PPTX files."""

    name: ClassVar[str] = "python-pptx"
    supported_extensions: ClassVar[frozenset[str]] = frozenset({"pptx"})

    def extract(self, file_path: Path) -> ExtractionResult:
        try:
            prs = Presentation(str(file_path))
        except Exception as exc:
            raise AppError("INVALID_FILE", f"Could not open PPTX file: {exc}", status_code=422) from exc

        self._w = int(prs.slide_width or Emu(9144000))
        self._h = int(prs.slide_height or Emu(6858000))
        result = ExtractionResult(page_count=len(prs.slides))
        self._result = result

        for slide_number, slide in enumerate(prs.slides, start=1):
            deadline.check()
            self._slide_no = slide_number
            self._counter = 0
            try:
                title_id = slide.shapes.title.shape_id if slide.shapes.title is not None else None
            except Exception:  # noqa: BLE001
                title_id = None
            self._visit(slide.shapes, _Xform(), title_id)
        return result

    # ------------------------------------------------------------------

    def _bid(self, kind: str = "b") -> str:
        bid = f"s{self._slide_no}-{kind}{self._counter}"
        self._counter += 1
        return bid

    def _bbox(self, shape, xf: _Xform) -> BBox | None:
        try:
            if shape.left is None or shape.top is None or shape.width is None or shape.height is None:
                return None
            l, t, w, h = xf.apply(int(shape.left), int(shape.top), int(shape.width), int(shape.height))
        except Exception:  # noqa: BLE001
            return None
        x0, y0 = max(0.0, l / self._w), max(0.0, t / self._h)
        x1, y1 = min(1.0, (l + w) / self._w), min(1.0, (t + h) / self._h)
        if x1 <= x0 or y1 <= y0:
            return None
        return (round(x0, 6), round(y0, 6), round(x1, 6), round(y1, 6))

    def _visit(self, shapes, xf: _Xform, title_id) -> None:
        for shape in shapes:
            try:
                try:
                    is_group = shape.shape_type == MSO_SHAPE_TYPE.GROUP
                except NotImplementedError:  # some placeholders have no shape_type
                    is_group = False
                if is_group:
                    self._visit(shape.shapes, xf.child(shape), title_id)
                    continue
                self._shape(shape, xf, title_id)
            except AppError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._result.errors.append(
                    DocumentError(
                        code="SHAPE_EXTRACTION_FAILED",
                        message=f"{getattr(shape, 'name', 'shape')}: {exc}",
                        page=self._slide_no,
                    )
                )

    def _shape(self, shape, xf: _Xform, title_id) -> None:
        bbox = self._bbox(shape, xf)
        n = self._slide_no

        if getattr(shape, "has_chart", False) and shape.has_chart:
            self._chart(shape, bbox)

        if getattr(shape, "has_table", False) and shape.has_table:
            self._table(shape, bbox)

        if shape.has_text_frame:
            self._text_frame(shape, bbox, shape.shape_id == title_id)

        is_picture = False
        try:
            is_picture = shape.shape_type == MSO_SHAPE_TYPE.PICTURE or hasattr(shape, "image")
        except NotImplementedError:
            is_picture = hasattr(shape, "image")
        if is_picture:
            try:
                img = shape.image
                blob, ext = img.blob, img.ext
            except Exception:  # noqa: BLE001
                blob, ext = None, ""
            bid = self._bid()
            self._result.blocks.append(
                DocumentBlock(
                    id=bid, type=BlockType.FIGURE, content=f"[image: {shape.name}]", page=n, bbox=bbox,
                    confidence=None, extractor=self.name,
                    metadata={"shape_name": shape.name, "slide_number": n, "media": "picture",
                              "alt_text": self._alt_text(shape)},
                )
            )
            if blob:
                self._result.assets[bid] = (blob, (ext or "").lower())

    @staticmethod
    def _alt_text(shape) -> str:
        try:
            return shape._element.xpath(".//p:cNvPr")[0].get("descr", "") or ""
        except Exception:  # noqa: BLE001
            return ""

    # ------------------------------------------------------------------

    def _text_frame(self, shape, bbox: BBox | None, is_title: bool) -> None:
        n = self._slide_no
        for para in shape.text_frame.paragraphs:
            p_el = para._p
            text_parts: list[str] = []
            maths = []
            for child in p_el:
                t = _ln(child)
                if t == "r":
                    text_parts.append("".join(x.text or "" for x in child if _ln(x) == "t"))
                elif t == "br":
                    text_parts.append("\n")
                elif t == "fld":
                    text_parts.append("".join(x.text or "" for x in child if _ln(x) == "t"))
                elif t == "AlternateContent":
                    choice = child.find(f"{{{_MC}}}Choice")
                    if choice is not None:  # prefer the OMML over the fallback picture
                        for m in choice.iter(f"{{{_M}}}oMathPara", f"{{{_M}}}oMath"):
                            if m.getparent() is not None and _ln(m.getparent()) == "oMathPara":
                                continue
                            maths.append(omml_to_latex(m))
                elif t in ("oMath", "oMathPara"):
                    maths.append(omml_to_latex(child))

            text = "".join(text_parts).strip()
            if text:
                if is_title:
                    block_type = BlockType.HEADING
                elif any(text.startswith(c) for c in _BULLETS) and (len(text) > 1 and text[1] in " \t" or text[0] != "-"):
                    block_type = BlockType.LIST
                elif para.level > 0:
                    block_type = BlockType.LIST
                else:
                    block_type = BlockType.PARAGRAPH
                metadata: dict[str, Any] = {
                    "slide_number": n, "shape_name": shape.name, "indent_level": para.level,
                }
                if is_title:
                    metadata["heading_level"] = 1
                if para.runs:
                    run = para.runs[0]
                    if run.font.size:
                        metadata["font_size"] = run.font.size.pt
                    metadata["is_bold"] = bool(run.font.bold)
                self._result.blocks.append(
                    DocumentBlock(
                        id=self._bid(), type=block_type, content=text, page=n, bbox=bbox,
                        confidence=None, extractor=self.name, metadata=metadata,
                    )
                )
            for conv in maths:
                if not conv.latex:
                    continue
                flags = ["omml_unsupported_construct"] if conv.unsupported else []
                conf: float | None = 0.6 if conv.unsupported else 0.98
                if not validate_latex(conv.latex).ok:
                    flags.append("latex_parse_failed")
                    conf = 0.3
                self._result.blocks.append(
                    make_equation_block(
                        self._bid("eq"), n, bbox, conv.latex, "omml", conf, flags, extractor="omml",
                        extra_metadata={"slide_number": n, "display": conv.display, "inline": bool(text),
                                        "confidence_source": "omml_structural_conversion"},
                    )
                )

    def _table(self, shape, bbox: BBox | None) -> None:
        n = self._slide_no
        table = shape.table
        rows: list[list[str | None]] = []
        regions: list[tuple[int, int, int, int]] = []
        for r, row in enumerate(table.rows):
            out: list[str | None] = []
            for c, cell in enumerate(row.cells):
                if cell.is_spanned:
                    out.append(None)
                    continue
                out.append(cell.text.strip())
                if cell.is_merge_origin:
                    regions.append((r, c, cell.span_height, cell.span_width))
            rows.append(out)
        if not rows:
            return
        self._result.blocks.append(
            DocumentBlock(
                id=self._bid(), type=BlockType.TABLE,
                content="\n".join(" | ".join("" if c is None else c for c in row) for row in rows),
                page=n, bbox=bbox, confidence=None, extractor=self.name,
                metadata={
                    "rows": rows, "row_count": len(rows), "col_count": len(rows[0]), "header": rows[0],
                    "slide_number": n,
                    "merged_cells": {"has_merged_cells": bool(regions), "merged_regions": regions},
                },
            )
        )

    def _chart(self, shape, bbox: BBox | None) -> None:
        n = self._slide_no
        bid = self._bid("chart")
        try:
            xml = etree.tostring(shape.chart._chartSpace)
            data = parse_chart_xml(xml)
        except Exception as exc:  # noqa: BLE001
            data = None
            self._result.errors.append(
                DocumentError(code="CHART_PARSE_FAILED", message=f"{shape.name}: {exc}", page=n)
            )
        meta = {"slide_number": n, "shape_name": shape.name, "media": "native_chart"}
        if data is None:
            self._result.blocks.append(
                DocumentBlock(
                    id=bid, type=BlockType.FIGURE, content="[chart: no readable data]", page=n, bbox=bbox,
                    confidence=None, extractor=self.name, requires_review=True,
                    metadata=meta | {"flags": ["chart_data_unreadable"], "needs_review": True},
                )
            )
            return
        self._result.blocks.append(make_chart_block(bid, n, bbox, data, 0.99, [], meta))
