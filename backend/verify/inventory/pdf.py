"""PDF source inventory using readers independent of PyMuPDF (the extractor's engine).

  pypdfium2   native text per page, image objects with bounds, page size/rotation
  pdfplumber  word positions (grouped into text regions) and ruled-line table candidates

Evidence classes, stated honestly:
  * the native TEXT of a page is read deterministically from the file's text layer
  * grouping words into regions is a HEURISTIC (the file has no notion of "paragraph")
  * a pdfplumber table is a CANDIDATE found from ruling lines: it is evidence, not ground truth,
    and borderless tables are not found at all
  * a page with no usable text layer is a SCANNED candidate (heuristic). For such a page there is
    NO independent text reader here: the only one available is OCR, which is the engine AXTRACT
    itself uses, and agreeing with oneself proves nothing. The page is marked accordingly.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from verify.inventory.models import (
    InventorySignal, SourceInventory, SourceLocator, SourceObject, SourceObjectType, SourceUnit,
)
from verify.inventory.options import InventoryOptions
from verify.models import EvidenceKind, UnitRef, UnitType, bbox_problem

DET, HEUR, MODEL, NA = EvidenceKind.DETERMINISTIC, EvidenceKind.HEURISTIC, EvidenceKind.MODEL_BASED, EvidenceKind.UNAVAILABLE

SIGNALS = [
    InventorySignal(name="native_text_per_page", kind=DET, engine="pypdfium2",
                    note="the file's own text layer; exact where a layer exists"),
    InventorySignal(name="image_objects_and_bounds", kind=DET, engine="pypdfium2",
                    note="existence and bounds; whether an image is decorative is not known"),
    InventorySignal(name="page_size_and_rotation", kind=DET, engine="pypdfium2"),
    InventorySignal(name="page_class_digital_scanned_mixed", kind=HEUR, engine="pypdfium2 text length + image coverage",
                    note="thresholds, not a measurement"),
    InventorySignal(name="text_regions", kind=HEUR, engine="pdfplumber words grouped by proximity",
                    note="word text and positions are exact; grouping into regions is a heuristic"),
    InventorySignal(name="table_candidates", kind=HEUR, engine="pdfplumber ruling-line detection",
                    note="a candidate, not ground truth; borderless tables are not detected"),
    InventorySignal(name="scanned_page_text", kind=NA, engine="none", available=False,
                    note="the only reader would be OCR, the same engine AXTRACT uses: not independent"),
    InventorySignal(name="charts_figures_equations", kind=NA, engine="none", available=False,
                    note="would need a vision model; deliberately not used at this step"),
]
LIMITATIONS = [
    "Scanned pages have no independent text evidence; their text can only be compared with itself.",
    "Table candidates come from ruling lines; borderless tables and tables drawn as images are not found.",
    "Rotated pages are inventoried by text only; their coordinates are not compared.",
    "Pages beyond the detailed-page limit, or after the time budget, get native text but no regions or tables.",
    "Charts, figures and equations cannot be detected without a model; only image objects are known.",
]
_WORD = re.compile(r"\w+", re.UNICODE)
MIN_NATIVE_WORDS_DIGITAL = 5


def _norm_box(x0, top, x1, bottom, w, h):
    if not w or not h:
        return None
    b = (max(0.0, x0 / w), max(0.0, top / h), min(1.0, x1 / w), min(1.0, bottom / h))
    b = tuple(round(v, 6) for v in b)
    return b if b[2] > b[0] and b[3] > b[1] and bbox_problem(b) is None else None


def _group_regions(words: list[dict]) -> list[dict]:
    """Words -> text regions. Lines by baseline, split at wide gaps (columns), then stacked by proximity."""
    if not words:
        return []
    words = sorted(words, key=lambda w: (round(w["top"], 0), w["x0"]))
    lines: list[list[dict]] = []
    for w in words:
        mid = (w["top"] + w["bottom"]) / 2
        for ln in lines[-4:]:
            lmid = (ln[0]["top"] + ln[0]["bottom"]) / 2
            if abs(mid - lmid) <= 0.5 * (ln[0]["bottom"] - ln[0]["top"]):
                ln.append(w)
                break
        else:
            lines.append([w])
    segs: list[dict] = []
    for ln in lines:
        ln.sort(key=lambda w: w["x0"])
        cur = [ln[0]]
        for prev, w in zip(ln, ln[1:]):
            gap = w["x0"] - prev["x1"]
            height = max(prev["bottom"] - prev["top"], 1.0)
            if gap > max(18.0, 2.5 * height):
                segs.append(_seg(cur))
                cur = []
            cur.append(w)
        segs.append(_seg(cur))
    segs.sort(key=lambda s: (s["x0"], s["top"]))
    regions: list[dict] = []
    for s in sorted(segs, key=lambda s: (s["top"], s["x0"])):
        for r in reversed(regions):
            vgap = s["top"] - r["bottom"]
            overlap = min(s["x1"], r["x1"]) - max(s["x0"], r["x0"])
            if -1.0 <= vgap <= 0.9 * s["h"] and overlap >= 0.4 * min(s["x1"] - s["x0"], r["x1"] - r["x0"]):
                r["lines"].append(s["text"])
                r["x0"], r["x1"] = min(r["x0"], s["x0"]), max(r["x1"], s["x1"])
                r["bottom"] = max(r["bottom"], s["bottom"])
                break
        else:
            regions.append({**s, "lines": [s["text"]]})
    return regions


def _seg(ws: list[dict]) -> dict:
    return {"text": " ".join(w["text"] for w in ws), "x0": min(w["x0"] for w in ws), "x1": max(w["x1"] for w in ws),
            "top": min(w["top"] for w in ws), "bottom": max(w["bottom"] for w in ws),
            "h": max(w["bottom"] - w["top"] for w in ws)}


def inventory_pdf(path: Path, opts: InventoryOptions | None = None) -> SourceInventory:
    opts = opts or InventoryOptions()
    t0 = time.perf_counter()
    inv = SourceInventory(file_type="pdf", signals=list(SIGNALS), limitations=list(LIMITATIONS))
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_c

        from services.preview_service import _pdfium_lock  # pdfium is not thread-safe; share the app's lock

        pages: list[dict] = []
        with _pdfium_lock:
            pdf = pdfium.PdfDocument(str(path))
            try:
                for i in range(len(pdf)):
                    page = pdf[i]
                    w, h = page.get_size()
                    rot = page.get_rotation()
                    tp = page.get_textpage()
                    try:
                        text = tp.get_text_range() or ""
                    finally:
                        tp.close()
                    imgs, area = [], 0.0
                    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]):
                        l, b, r, t = obj.get_bounds()
                        if r - l < 4 or t - b < 4:
                            continue
                        box = _norm_box(l, h - t, r, h - b, w, h)
                        if box:
                            frac = (box[2] - box[0]) * (box[3] - box[1])
                            area += frac
                            imgs.append((box, frac))
                    pages.append(dict(w=w, h=h, rot=rot, text=text, imgs=imgs, coverage=min(area, 1.0)))
            finally:
                pdf.close()
        inv.properties["page_count"] = len(pages)

        plumber = None
        try:
            import pdfplumber
            plumber = pdfplumber.open(str(path))
        except Exception:  # noqa: BLE001
            inv.limitations.append("pdfplumber could not open this file: no word regions or table candidates.")
        try:
            for i, pg in enumerate(pages, start=1):
                unit = UnitRef(type=UnitType.PAGE, index=i)
                words_n = len(_WORD.findall(pg["text"]))
                if words_n < MIN_NATIVE_WORDS_DIGITAL and pg["coverage"] >= 0.4:
                    cls = "scanned"
                elif pg["coverage"] >= 0.3 and words_n < 40:
                    cls = "mixed"
                else:
                    cls = "digital"
                scanned = cls == "scanned"
                su = SourceUnit(
                    unit=unit,
                    properties=dict(page_class=cls, page_class_kind=HEUR.value, native_words=words_n, rotation=pg["rot"],
                                    image_coverage=round(pg["coverage"], 4), width_pt=pg["w"], height_pt=pg["h"]),
                    independent_text_available=not scanned,
                    independence_note=("no text layer: the only reader is OCR, the same engine AXTRACT uses; "
                                       "agreement with itself would prove nothing") if scanned else
                                      ("native text is independent; text inside images is not" if cls == "mixed" else ""),
                )
                if words_n:
                    su.objects.append(SourceObject(
                        id=f"pdf:page{i}:text", type=SourceObjectType.TEXT, unit=unit, text=pg["text"],
                        locator=SourceLocator(page=i), kind=DET, engine="pypdfium2",
                        metadata={"words": words_n, "scope": "page"}))
                for n, (box, frac) in enumerate(pg["imgs"], start=1):
                    su.objects.append(SourceObject(
                        id=f"pdf:page{i}:image{n}", type=SourceObjectType.PICTURE, unit=unit,
                        locator=SourceLocator(page=i, bbox=box, position=n), kind=DET, engine="pypdfium2",
                        metadata={"area_fraction": round(frac, 5), "decorative_size": frac < opts.pdf_image_min_area,
                                  "full_page_background": frac >= 0.8}))
                detailed = plumber is not None and i <= opts.max_pdf_pages_detailed and (time.perf_counter() - t0) < opts.time_budget_s
                if plumber is not None and not detailed:
                    inv.truncated = True
                    su.properties["detail"] = "native text only (page limit or time budget reached)"
                if detailed and pg["rot"] == 0:
                    try:
                        pp = plumber.pages[i - 1]
                        for n, r in enumerate(_group_regions(pp.extract_words(use_text_flow=False)), start=1):
                            txt = "\n".join(r["lines"])
                            box = _norm_box(r["x0"], r["top"], r["x1"], r["bottom"], pp.width, pp.height)
                            su.objects.append(SourceObject(
                                id=f"pdf:page{i}:region{n}", type=SourceObjectType.TEXT, unit=unit, text=txt,
                                locator=SourceLocator(page=i, bbox=box, position=n), kind=HEUR, engine="pdfplumber",
                                metadata={"scope": "region", "text_kind": "deterministic", "grouping_kind": "heuristic",
                                          "chars": len(txt)}))
                        for n, tb in enumerate(pp.find_tables()[:20], start=1):
                            rows = [[(c or "").strip() for c in row] for row in tb.extract()]
                            box = _norm_box(*tb.bbox, pp.width, pp.height)
                            su.objects.append(SourceObject(
                                id=f"pdf:page{i}:table{n}", type=SourceObjectType.TABLE, unit=unit,
                                text=" ".join(c for r in rows for c in r),
                                locator=SourceLocator(page=i, bbox=box, position=n), kind=HEUR, engine="pdfplumber",
                                metadata={"rows": rows, "row_count": len(rows), "col_count": max((len(r) for r in rows), default=0),
                                          "limitation": "ruling-line candidate, not ground truth"}))
                    except Exception as exc:  # noqa: BLE001 - one page failing must not stop the inventory
                        su.properties["detail_error"] = f"{type(exc).__name__}: {exc}"
                elif detailed:
                    su.properties["detail"] = "rotated page: coordinates not compared"
                inv.units.append(su)
        finally:
            if plumber is not None:
                plumber.close()
    except Exception as exc:  # noqa: BLE001 - an inventory failure never affects the extraction
        inv.error = f"{type(exc).__name__}: {exc}"
    inv.timing_ms = round((time.perf_counter() - t0) * 1000, 3)
    return inv
