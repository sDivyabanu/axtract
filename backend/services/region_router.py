"""Region router: sends every figure-like region, in every format, to the right reader.

Runs after extraction (while the uploaded file still exists). For each region it decides:
  chart     -> exact data (native Office XML / PDF vector primitives) or, for pictures, the
               raster chart reader (OpenCV + OCR)
  equation  -> OMML (done in the extractors), text-layer math, or the local formula model on a
               rendered crop; always validated and cross-checked
  otherwise -> stays a figure

The same classifier handles pictures from PDF pages, DOCX/PPTX media, scanned pages and
standalone images, so a chart or formula is read the same way wherever it appears.
Nothing is invented: unreadable content stays a figure / is flagged for review.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from extractors.base import ExtractionResult
from models.document import BBox, BlockType, DocumentBlock
from models.errors import DocumentError
from services import preview_service
from services.chart_service import has_numeric_values, make_chart_block
from services.equation_service import make_equation_block
from services.vision import models
from services.vision.chart_raster import read_chart
from services.vision.formula import FormulaResult, recognize_formula
from services.vision.latex import crosscheck, find_inline_equations, plain_to_latex, validate_latex
from utils import deadline

logger = logging.getLogger(__name__)

RENDER_DPI = 200
MAX_FIGURES_ANALYZED = 24  # cost guard: each figure may need OCR + a model call
_MIN_BUDGET = 6.0  # seconds that must remain before starting another model call


# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------


def _iou(a: BBox, b: BBox) -> float:
    ix0, iy0, ix1, iy1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _inside(box: BBox, inner: BBox, frac: float = 0.5) -> bool:
    """True when at least `frac` of `inner` lies inside `box`."""
    ix0, iy0, ix1, iy1 = max(box[0], inner[0]), max(box[1], inner[1]), min(box[2], inner[2]), min(box[3], inner[3])
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return area > 0 and max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0) / area >= frac


def _center_in(box: BBox, inner: BBox) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def _crop(img: Image.Image, bbox: BBox, pad: float = 0.004) -> Image.Image:
    w, h = img.size
    x0, y0 = max(0, int((bbox[0] - pad) * w)), max(0, int((bbox[1] - pad) * h))
    x1, y1 = min(w, int((bbox[2] + pad) * w) + 1), min(h, int((bbox[3] + pad) * h) + 1)
    return img.crop((x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)))


def _budget_ok() -> bool:
    return deadline.remaining() > _MIN_BUDGET


def _is_monochrome(img: Image.Image) -> bool:
    arr = np.asarray(img.convert("RGB").resize((min(img.width, 200), min(img.height, 200))), dtype=np.int16)
    sat = arr.max(axis=2) - arr.min(axis=2)
    return float((sat > 40).mean()) < 0.02


def _ocr_char_count(fr: FormulaResult) -> int:
    return len("".join(fr.ocr_text.split()))


# ---------------------------------------------------------------------------
# classification of one image region
# ---------------------------------------------------------------------------


def _classify(img: Image.Image, hint: str | None = None, layout_class: str | None = None,
              caption: str = "", rel_height: float | None = None):
    """Return ("chart", ChartReading) | ("equation", FormulaResult) | None."""
    w, h = img.size
    if min(w, h) < 40 or not _budget_ok():
        return None

    if hint != "equation" and layout_class != "equation" and w * h >= 150 * 150:
        try:
            reading = read_chart(img, caption)
        except Exception:  # noqa: BLE001 - a bad image must not fail the document
            logger.exception("chart reader failed")
            reading = None
        if reading is not None and has_numeric_values(reading.data):
            return "chart", reading

    aspect = w / h
    formula_like = (
        hint == "equation"
        or layout_class == "equation"
        or (_is_monochrome(img) and 0.8 <= aspect <= 10 and (rel_height is None or rel_height <= 0.3) and h <= 900)
    )
    if formula_like and models.latex_model_available() and _budget_ok():
        fr = recognize_formula(img)
        # Only document structure (an "Equation N" caption, an OLE equation object) justifies
        # trusting the region; the layout detector alone never does.
        trusted = hint == "equation"
        if fr.latex and fr.valid and fr.mathy and not fr.looks_like_prose:
            if trusted or (fr.agree >= 0.2 and _ocr_char_count(fr) <= 80):
                return "equation", fr
        elif fr.latex and trusted:
            return "equation", fr  # known formula that did not validate: keep, flagged low
    return None


def _to_chart(block: DocumentBlock, reading, extra: dict | None = None) -> DocumentBlock:
    meta = {k: v for k, v in block.metadata.items() if k in ("alt_text", "media", "shape_name", "slide_number",
                                                             "route", "page_size_pt", "image_width", "image_height")}
    meta.update(extra or {})
    return make_chart_block(block.id, block.page, block.bbox, reading.data, reading.confidence,
                            reading.flags, meta)


def _to_equation(block: DocumentBlock, fr: FormulaResult, extra: dict | None = None) -> DocumentBlock:
    meta = {k: v for k, v in block.metadata.items() if k in ("alt_text", "media", "shape_name", "slide_number",
                                                             "route", "page_size_pt", "ole_prog_id")}
    meta["crop_ref"] = {"page": block.page, "bbox": list(block.bbox) if block.bbox else None}
    if fr.ocr_text:
        meta["ocr_crosscheck_text"] = fr.ocr_text
    meta.update(extra or {})
    return make_equation_block(block.id, block.page, block.bbox, fr.latex, "pix2tex_onnx", fr.confidence,
                               fr.flags, extractor="pix2tex_onnx", extra_metadata=meta)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def route_regions(result: ExtractionResult, file_path: Path, file_type: str) -> None:
    """Mutates result.blocks (and result.errors)."""
    try:
        if file_type == "pdf":
            _route_pdf(result, file_path)
        elif file_type in ("docx", "pptx"):
            _route_office_pictures(result)
        elif file_type in ("jpg", "jpeg", "png"):
            _route_image(result, file_path)
    except Exception as exc:  # noqa: BLE001
        if exc.__class__.__name__ == "AppError":
            raise
        logger.exception("region routing failed")
        result.errors.append(DocumentError(code="REGION_ROUTING_FAILED", message=str(exc), page=None))


# ---------------------------------------------------------------------------
# Office pictures
# ---------------------------------------------------------------------------


def _load_picture(blob: bytes, ext: str) -> Image.Image | None:
    if ext in ("emf", "wmf", "emz", "wmz", "svg"):
        png = preview_service.convert_media_to_png(blob, ext)
        if png is None:
            return None
        blob = png
    try:
        img = Image.open(io.BytesIO(blob))
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            bg = Image.new("RGB", rgba.size, "white")
            bg.paste(rgba, mask=rgba.split()[-1])
            return bg
        return img.convert("RGB")
    except Exception:  # noqa: BLE001
        return None


def _route_office_pictures(result: ExtractionResult) -> None:
    analyzed = 0
    for i, block in enumerate(result.blocks):
        if block.type != BlockType.FIGURE or block.id not in result.assets:
            continue
        deadline.check()
        blob, ext = result.assets[block.id]
        img = _load_picture(blob, ext)
        if img is None:
            block.requires_review = True
            block.metadata.setdefault("flags", []).append("picture_not_decoded" if ext not in ("emf", "wmf") else "emf_not_converted")
            block.metadata["needs_review"] = True
            continue
        block.metadata["image_width"], block.metadata["image_height"] = img.size
        if analyzed >= MAX_FIGURES_ANALYZED:
            block.metadata.setdefault("flags", []).append("not_analyzed_figure_limit")
            continue
        analyzed += 1
        caption = _caption_before(result.blocks, i)
        res = _classify(img, hint=block.metadata.get("hint") or _caption_hint(caption), caption=caption)
        if res is None:
            continue
        kind, payload = res
        result.blocks[i] = _to_chart(block, payload) if kind == "chart" else _to_equation(block, payload)


_EQ_CAPTION = re.compile(r"\b(equation|formula|eq\.)\b", re.I)


def _caption_hint(caption: str) -> str | None:
    """A caption such as 'Equation 3' is document-structure evidence that the image is a formula."""
    return "equation" if caption and _EQ_CAPTION.search(caption) else None


def _caption_before(blocks: list[DocumentBlock], i: int) -> str:
    for b in reversed(blocks[max(0, i - 3): i]):
        if b.type in (BlockType.HEADING, BlockType.PARAGRAPH) and len(b.content) < 160:
            return b.content
    return ""


# ---------------------------------------------------------------------------
# standalone images (JPG / PNG)
# ---------------------------------------------------------------------------


def _route_image(result: ExtractionResult, file_path: Path) -> None:
    try:
        img = Image.open(file_path)
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:  # noqa: BLE001
        return
    page = 1
    ocr_blocks = [b for b in result.blocks if b.extractor == "rapidocr"]

    # whole image is one chart?
    res = _classify(img) if _budget_ok() else None
    if res is not None and res[0] == "chart":
        reading = res[1]
        texts = [b.content for b in ocr_blocks]
        result.blocks = [b for b in result.blocks if b not in ocr_blocks]
        result.blocks.append(make_chart_block(
            "p1-chart0", page, (0.0, 0.0, 1.0, 1.0), reading.data, reading.confidence, reading.flags,
            {"ocr_text_lines": texts, "ocr_blocks_merged": len(texts), "image_width": img.width,
             "image_height": img.height},
        ))
        return

    _route_regions_on_image(result, img, page)
    if res is not None and res[0] == "equation" and not any(b.type == BlockType.EQUATION for b in result.blocks):
        # the whole image is a single formula
        fr = res[1]
        if fr.valid and fr.confidence >= 0.45 and _ocr_char_count(fr) <= 80 and not fr.looks_like_prose:
            texts = [b.content for b in ocr_blocks]
            result.blocks = [b for b in result.blocks if b not in ocr_blocks]
            result.blocks.append(make_equation_block(
                "p1-eq0", page, (0.0, 0.0, 1.0, 1.0), fr.latex, "pix2tex_onnx", fr.confidence, fr.flags,
                extractor="pix2tex_onnx",
                extra_metadata={"ocr_crosscheck_text": fr.ocr_text, "ocr_text_lines": texts,
                                "crop_ref": {"page": page, "bbox": [0, 0, 1, 1]}},
            ))


def _route_regions_on_image(result: ExtractionResult, img: Image.Image, page: int) -> None:
    """Layout-detected equation / figure regions on a scanned page or photo of a page."""
    if not _budget_ok():
        return
    try:
        regions = models.detect_layout(img)
    except Exception:  # noqa: BLE001
        logger.exception("layout detection failed")
        return
    n_eq = n_ch = 0
    for r in regions:
        if not _budget_ok():
            break
        if r.cls == "equation" and r.score >= 0.5 and models.latex_model_available():
            fr = recognize_formula(_crop(img, r.bbox))
            if fr.latex and fr.valid and fr.mathy and fr.agree >= 0.2 and not fr.looks_like_prose:
                _replace_ocr_inside(result, page, r.bbox)
                result.blocks.append(make_equation_block(
                    f"p{page}-eq{n_eq}", page, r.bbox, fr.latex, "pix2tex_onnx", fr.confidence, fr.flags,
                    extractor="pix2tex_onnx",
                    extra_metadata={"layout_class": "equation", "layout_score": round(r.score, 3),
                                    "ocr_crosscheck_text": fr.ocr_text,
                                    "crop_ref": {"page": page, "bbox": list(r.bbox)}},
                ))
                n_eq += 1
        elif r.cls == "figure" and r.score >= 0.5:
            reading = None
            try:
                reading = read_chart(_crop(img, r.bbox))
            except Exception:  # noqa: BLE001
                logger.exception("chart reader failed")
            if reading is not None and has_numeric_values(reading.data):
                _replace_ocr_inside(result, page, r.bbox)
                result.blocks.append(make_chart_block(
                    f"p{page}-chart{n_ch}", page, r.bbox, reading.data, reading.confidence, reading.flags,
                    {"layout_class": "figure", "layout_score": round(r.score, 3)},
                ))
                n_ch += 1


def _replace_ocr_inside(result: ExtractionResult, page: int, region: BBox) -> None:
    """Drop OCR text blocks that sit inside a region now represented by a chart/equation block."""
    result.blocks = [
        b for b in result.blocks
        if not (b.page == page and b.extractor == "rapidocr" and b.bbox and _center_in(region, b.bbox))
    ]


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _native_image(doc, pno: int, bbox: BBox) -> Image.Image | None:
    """The embedded image behind a PDF figure block, at its native resolution."""
    import pymupdf

    try:
        page = doc[pno - 1]
        pw, ph = page.rect.width, page.rect.height
        want = pymupdf.Rect(bbox[0] * pw, bbox[1] * ph, bbox[2] * pw, bbox[3] * ph)
        best, best_iou = None, 0.0
        for info in page.get_images(full=True):
            xref = info[0]
            for rect in page.get_image_rects(xref):
                inter = (rect & want).get_area()
                union = rect.get_area() + want.get_area() - inter
                iou = inter / union if union > 0 else 0.0
                if iou > best_iou:
                    best, best_iou = xref, iou
        if best is None or best_iou < 0.6:
            return None
        raw = doc.extract_image(best)
        img = Image.open(io.BytesIO(raw["image"]))
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            bg = Image.new("RGB", rgba.size, "white")
            bg.paste(rgba, mask=rgba.split()[-1])
            return bg
        return img.convert("RGB")
    except Exception:  # noqa: BLE001
        return None


def _route_pdf(result: ExtractionResult, file_path: Path) -> None:
    import pdfplumber
    import pymupdf

    blocks = result.blocks
    figure_pages = {b.page for b in blocks if b.type == BlockType.FIGURE}
    candidate_pages = {b.page for b in blocks if b.metadata.get("formula_candidate")}
    scanned_pages = {b.page for b in blocks if b.metadata.get("route") in ("scanned", "mixed-ocr")}
    inline_pages = {b.page for b in blocks if b.type in (BlockType.PARAGRAPH, BlockType.LIST)}

    # 1. vector charts (pdfplumber) on pages that actually have drawing primitives
    vector_pages: list[int] = []
    with pymupdf.open(str(file_path)) as doc:
        for pno in range(1, doc.page_count + 1):
            try:
                if len(doc[pno - 1].get_cdrawings()) >= 8:
                    vector_pages.append(pno)
            except Exception:  # noqa: BLE001
                continue
    vector_found: dict[int, list] = {}
    if vector_pages:
        from services.vision.chart_pdf_vector import read_vector_charts

        with pdfplumber.open(str(file_path)) as plumber:
            for pno in vector_pages:
                deadline.check()
                try:
                    page = plumber.pages[pno - 1]
                    charts = read_vector_charts(page)
                    if charts:
                        vector_found[pno] = [(c, page.width, page.height) for c in charts]
                except Exception:  # noqa: BLE001
                    logger.exception("vector chart reader failed on page %s", pno)

    for pno, items in vector_found.items():
        for k, (c, pw, ph) in enumerate(items):
            x0, t, x1, b = c["bbox_pt"]
            bbox: BBox = (max(0.0, x0 / pw), max(0.0, t / ph), min(1.0, x1 / pw), min(1.0, b / ph))
            blocks[:] = [
                blk for blk in blocks
                if not (blk.page == pno and blk.bbox and blk.type != BlockType.CHART
                        and (_center_in(bbox, blk.bbox) or (blk.type == BlockType.TABLE and _inside(bbox, blk.bbox))
                             or (blk.type == BlockType.FIGURE and _iou(bbox, blk.bbox) > 0.3)))
            ]
            blocks.append(make_chart_block(
                f"p{pno}-vchart{k}", pno, tuple(round(v, 6) for v in bbox), c["data"], c["confidence"],
                c["flags"], {"route": "digital", "page_size_pt": [round(pw, 2), round(ph, 2)]},
            ))

    # 2. pages that need pixels: figures, formula candidates, scanned pages
    work_pages = sorted(figure_pages | candidate_pages | scanned_pages)
    page_img: dict[int, Image.Image] = {}

    def image_of(pno: int) -> Image.Image:
        if pno not in page_img:
            page_img[pno] = preview_service.render_page_pil(file_path, pno, RENDER_DPI)
        return page_img[pno]

    analyzed = 0
    fitz_doc = pymupdf.open(str(file_path))
    for pno in work_pages:
        deadline.check()
        if not _budget_ok():
            result.errors.append(DocumentError(code="REGION_ROUTING_SKIPPED", message="Time budget reached; remaining pages were not analysed for charts/equations.", page=pno))
            break
        try:
            img = image_of(pno)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(DocumentError(code="PAGE_RENDER_FAILED", message=str(exc), page=pno))
            continue

        regions = []
        try:
            regions = models.detect_layout(img) if (figure_pages | candidate_pages | scanned_pages) else []
        except Exception:  # noqa: BLE001
            logger.exception("layout detection failed on page %s", pno)

        def layout_class_for(bbox: BBox | None) -> str | None:
            if bbox is None:
                return None
            best = max(regions, key=lambda r: _iou(r.bbox, bbox), default=None)
            return best.cls if best is not None and _iou(best.bbox, bbox) >= 0.3 else None

        # 2a. figures
        for i, blk in enumerate(list(blocks)):
            if blk.page != pno or blk.type != BlockType.FIGURE or blk.bbox is None:
                continue
            if analyzed >= MAX_FIGURES_ANALYZED:
                blk.metadata.setdefault("flags", []).append("not_analyzed_figure_limit")
                continue
            analyzed += 1
            crop = _native_image(fitz_doc, pno, blk.bbox) or _crop(img, blk.bbox, pad=0.002)
            rel_h = blk.bbox[3] - blk.bbox[1]
            idx = blocks.index(blk)
            caption = _caption_before(blocks, idx)
            res = _classify(crop, hint=_caption_hint(caption), layout_class=layout_class_for(blk.bbox),
                            rel_height=rel_h, caption=caption)
            if res is None:
                continue
            kind, payload = res
            blocks[idx] = _to_chart(blk, payload) if kind == "chart" else _to_equation(blk, payload)

        # 2b. formula candidates in the text layer: re-read the rendered crop
        for blk in list(blocks):
            if blk.page != pno or not blk.metadata.get("formula_candidate") or blk.bbox is None:
                continue
            if not _budget_ok() or not models.latex_model_available():
                blk.metadata.setdefault("flags", []).append("formula_not_checked")
                continue
            fr = recognize_formula(_crop(img, blk.bbox, pad=0.003))
            if fr.latex and fr.valid and fr.mathy and not fr.looks_like_prose and crosscheck(fr.latex, blk.content) >= 0.5:
                idx = blocks.index(blk)
                blocks[idx] = _to_equation(blk, fr, {"text_layer": blk.content})

        # 2c. layout-detected equation regions nothing else covers (vector-drawn / no text layer)
        n_eq = 0
        for r in regions:
            if r.cls != "equation" or r.score < 0.5 or not _budget_ok() or not models.latex_model_available():
                continue
            if any(b.page == pno and b.bbox and b.type in (BlockType.EQUATION, BlockType.FIGURE, BlockType.CHART)
                   and _iou(r.bbox, b.bbox) > 0.25 for b in blocks):
                continue
            covering = [b for b in blocks if b.page == pno and b.bbox and b.extractor == "rapidocr"
                        and _center_in(r.bbox, b.bbox)]
            text_cover = [b for b in blocks if b.page == pno and b.bbox and b.extractor == "pymupdf"
                          and b.type in (BlockType.PARAGRAPH, BlockType.HEADING) and _inside(r.bbox, b.bbox, 0.6)]
            if text_cover:
                continue  # ordinary text the layout model mistook for math
            fr = recognize_formula(_crop(img, r.bbox))
            if fr.latex and fr.valid and fr.mathy and fr.agree >= 0.2 and not fr.looks_like_prose:
                blocks[:] = [b for b in blocks if b not in covering]
                blocks.append(make_equation_block(
                    f"p{pno}-eq{n_eq}", pno, r.bbox, fr.latex, "pix2tex_onnx", fr.confidence, fr.flags,
                    extractor="pix2tex_onnx",
                    extra_metadata={"layout_class": "equation", "layout_score": round(r.score, 3),
                                    "route": "digital" if pno not in scanned_pages else "scanned",
                                    "ocr_crosscheck_text": fr.ocr_text,
                                    "crop_ref": {"page": pno, "bbox": list(r.bbox)}},
                ))
                n_eq += 1

        # 2d. scanned / image-only pages: figure regions may be charts
        if pno in scanned_pages:
            for r in regions:
                if r.cls == "figure" and r.score >= 0.5 and _budget_ok():
                    try:
                        reading = read_chart(_crop(img, r.bbox))
                    except Exception:  # noqa: BLE001
                        reading = None
                    if reading is not None and has_numeric_values(reading.data):
                        blocks[:] = [b for b in blocks if not (b.page == pno and b.extractor == "rapidocr"
                                                              and b.bbox and _center_in(r.bbox, b.bbox))]
                        blocks.append(make_chart_block(
                            f"p{pno}-chart{analyzed}", pno, r.bbox, reading.data, reading.confidence,
                            reading.flags, {"route": "scanned", "layout_class": "figure"}))
                        analyzed += 1

    fitz_doc.close()

    # 3. inline equations written as plain text ("x^2 + y^2 = r^2"), located via the text layer
    _inline_text_equations(result, file_path, inline_pages)


def _inline_text_equations(result: ExtractionResult, file_path: Path, pages: set[int]) -> None:
    import pypdfium2 as pdfium

    from services.preview_service import _find, _pdfium_lock, _range_box

    targets = []
    for blk in result.blocks:
        if blk.page in pages and blk.type in (BlockType.PARAGRAPH, BlockType.LIST) and blk.extractor == "pymupdf":
            found = find_inline_equations(blk.content)
            if found:
                targets.append((blk, found))
    if not targets:
        return

    with _pdfium_lock:
        pdf = pdfium.PdfDocument(str(file_path))
        try:
            n = 0
            for blk, found in targets:
                for expr in found:
                    latex = plain_to_latex(expr)
                    if not validate_latex(latex).ok:
                        continue
                    hit = _find(pdf, expr, blk.page - 1)
                    pw_ph = blk.metadata.get("page_size_pt")
                    bbox = _range_box(pdf, hit[0], hit[1], hit[2]) if hit and hit[0] == blk.page - 1 else None
                    result.blocks.append(make_equation_block(
                        f"p{blk.page}-ieq{n}", blk.page, bbox or blk.bbox, latex, "text_layer", 0.8, [],
                        extractor="text_layer",
                        extra_metadata={"inline": True, "parent_block_id": blk.id, "source_text": expr,
                                        "route": blk.metadata.get("route", "digital"),
                                        "page_size_pt": pw_ph,
                                        "confidence_source": "deterministic_text_conversion"},
                    ))
                    n += 1
        finally:
            pdf.close()
