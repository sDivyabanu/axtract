"""PPTX source inventory, read from the raw slide XML (independent of python-pptx).

Per slide: text shapes (with text), tables, pictures, native charts, grouped shapes, and the
geometry of each as a normalised bbox where the file states it.

Contract notes:
  * AXTRACT extracts text frames (one block per paragraph), tables, native charts and pictures,
    and recurses into groups, so those are EXPECTED.
  * Groups themselves, speaker notes and non-chart/non-table graphic frames (SmartArt, OLE) are not
    represented -> NOT_IN_CONTRACT, recorded so the choice is visible.
  * Placeholders that inherit their position from the layout have no xfrm of their own: bbox is
    None. Geometry is never guessed.
"""

from __future__ import annotations

import time
from pathlib import Path

from defusedxml import ElementTree as ET

from verify.inventory.models import (
    Contract, InventorySignal, SourceInventory, SourceLocator, SourceObject, SourceObjectType, SourceUnit,
)
from verify.inventory.ooxml import Package, PackageError, local, q
from verify.inventory.options import InventoryOptions
from verify.models import EvidenceKind, UnitRef, UnitType, bbox_problem

DET = EvidenceKind.DETERMINISTIC
_TABLE_URI = "http://schemas.openxmlformats.org/drawingml/2006/table"
_CHART_URI = "http://schemas.openxmlformats.org/drawingml/2006/chart"

SIGNALS = [
    InventorySignal(name="slide_count_and_order", kind=DET, engine="ooxml:ppt/presentation.xml"),
    InventorySignal(name="slide_visibility", kind=DET, engine="ooxml:ppt/slides/slide*.xml"),
    InventorySignal(name="text_shapes_and_text", kind=DET, engine="ooxml:ppt/slides/slide*.xml"),
    InventorySignal(name="tables_dimensions_and_cell_text", kind=DET, engine="ooxml:ppt/slides/slide*.xml"),
    InventorySignal(name="pictures", kind=DET, engine="ooxml:ppt/slides/slide*.xml"),
    InventorySignal(name="native_charts", kind=DET, engine="ooxml:ppt/slides/slide*.xml + ppt/charts/chart*.xml",
                    note="existence, type and series count; chart data is not read at this step"),
    InventorySignal(name="grouped_shapes", kind=DET, engine="ooxml:ppt/slides/slide*.xml"),
    InventorySignal(name="shape_geometry", kind=DET, engine="ooxml:xfrm + group transforms",
                    note="only where the shape states its own position; layout-inherited positions are None"),
    InventorySignal(name="speaker_notes", kind=DET, engine="ooxml:ppt/notesSlides/notesSlide*.xml",
                    note="recorded but outside AXTRACT's contract"),
]
LIMITATIONS = [
    "Shapes that inherit position from the slide layout have no bbox.",
    "Group rotation and shape rotation are ignored when computing bounding boxes.",
    "SmartArt, OLE objects and ink are recorded as 'other' and are outside AXTRACT's contract.",
    "Chart data is not read; only that a chart exists.",
]


class _Xf:
    """Affine map (x' = sx*x + tx, y' = sy*y + ty) from a group's child space to slide space (EMU)."""

    def __init__(self, sx=1.0, sy=1.0, tx=0.0, ty=0.0):
        self.sx, self.sy, self.tx, self.ty = sx, sy, tx, ty

    def box(self, x, y, w, h):
        return self.sx * x + self.tx, self.sy * y + self.ty, self.sx * w, self.sy * h

    def child(self, off, ext, ch_off, ch_ext) -> "_Xf":
        kx = ext[0] / ch_ext[0] if ch_ext[0] else 1.0
        ky = ext[1] / ch_ext[1] if ch_ext[1] else 1.0
        ltx, lty = off[0] - ch_off[0] * kx, off[1] - ch_off[1] * ky
        return _Xf(self.sx * kx, self.sy * ky, self.sx * ltx + self.tx, self.sy * lty + self.ty)


def _xfrm(el):
    """Return ((x, y), (cx, cy), chOff|None, chExt|None) of the first xfrm under el, or None."""
    x = el.find(f".//{q('a', 'xfrm')}")
    if x is None:
        x = el.find(q("p", "xfrm"))
    if x is None:
        return None
    off, ext = x.find(q("a", "off")), x.find(q("a", "ext"))
    if off is None or ext is None:
        return None
    try:
        o = (int(off.get("x")), int(off.get("y")))
        e = (int(ext.get("cx")), int(ext.get("cy")))
        co, ce = x.find(q("a", "chOff")), x.find(q("a", "chExt"))
        c_o = (int(co.get("x")), int(co.get("y"))) if co is not None else None
        c_e = (int(ce.get("cx")), int(ce.get("cy"))) if ce is not None else None
    except (TypeError, ValueError):
        return None
    return o, e, c_o, c_e


def _bbox(el, xf: _Xf, sw: int, sh: int):
    geom = _xfrm(el)
    if geom is None or not sw or not sh:
        return None
    (x, y), (w, h), _, _ = geom
    ax, ay, aw, ah = xf.box(x, y, w, h)
    b = (max(0.0, ax / sw), max(0.0, ay / sh), min(1.0, (ax + aw) / sw), min(1.0, (ay + ah) / sh))
    b = tuple(round(v, 6) for v in b)
    return b if b[2] > b[0] and b[3] > b[1] and bbox_problem(b) is None else None


def _para_text(p) -> str:
    out = []
    for ch in p:
        t = local(ch.tag)
        if t == "r" or t == "fld":
            out.append("".join((n.text or "") for n in ch if local(n.tag) == "t"))
        elif t == "br":
            out.append("\n")
    return "".join(out)


def _name(el, nv_tag: str) -> tuple[str | None, str]:
    nv = el.find(q("p", nv_tag))
    c = nv.find(q("p", "cNvPr")) if nv is not None else None
    return (c.get("name") if c is not None else None), (c.get("descr", "") if c is not None else "")


class _SlideReader:
    def __init__(self, pkg: Package, part: str, unit: UnitRef, sw: int, sh: int):
        self.pkg, self.part, self.unit, self.sw, self.sh = pkg, part, unit, sw, sh
        self.rels = pkg.rels(part)
        self.objects: list[SourceObject] = []
        self.n = 0

    def _add(self, type_, el, xf, name, text=None, contract=Contract.EXPECTED, note="", **meta):
        self.n += 1
        self.objects.append(SourceObject(
            id=f"pptx:slide{self.unit.index}:obj{self.n}", type=type_, unit=self.unit,
            locator=SourceLocator(part=self.part, position=self.n, bbox=_bbox(el, xf, self.sw, self.sh),
                                  page=self.unit.index, shape_name=name),
            text=text, kind=DET, engine=f"ooxml:{self.part}", contract=contract, contract_note=note, metadata=meta))

    def walk(self, container, xf: _Xf) -> None:
        for el in container:
            tag = local(el.tag)
            if tag == "grpSp":
                name, _ = _name(el, "nvGrpSpPr")
                self._add(SourceObjectType.GROUP, el.find(q("p", "grpSpPr")), xf, name, contract=Contract.NOT_IN_CONTRACT,
                          note="a group is a container; its children carry the expectations", children=len(list(el)) - 2)
                geom = _xfrm(el.find(q("p", "grpSpPr"))) if el.find(q("p", "grpSpPr")) is not None else None
                child_xf = xf
                if geom and geom[2] and geom[3]:
                    child_xf = xf.child(geom[0], geom[1], geom[2], geom[3])
                self.walk(el, child_xf)
            elif tag == "sp":
                self._shape(el, xf)
            elif tag == "graphicFrame":
                self._frame(el, xf)
            elif tag == "pic":
                name, descr = _name(el, "nvPicPr")
                blip = el.find(f".//{q('a', 'blip')}")
                media = self.rels.get(blip.get(q("r", "embed"), ""), ("", ""))[1] if blip is not None else ""
                self._add(SourceObjectType.PICTURE, el.find(q("p", "spPr")) if el.find(q("p", "spPr")) is not None else el,
                          xf, name, alt_text=descr, media=media)

    def _shape(self, el, xf: _Xf) -> None:
        body = el.find(q("p", "txBody"))
        if body is None:
            return
        paras = [t for t in (_para_text(p) for p in body.findall(q("a", "p"))) if t.strip()]
        if not paras:
            return
        name, _ = _name(el, "nvSpPr")
        ph = el.find(f".//{q('p', 'ph')}")
        is_title = ph is not None and ph.get("type") in ("title", "ctrTitle")
        sp_pr = el.find(q("p", "spPr"))
        self._add(SourceObjectType.HEADING if is_title else SourceObjectType.TEXT, sp_pr if sp_pr is not None else el, xf,
                  name, text="\n".join(paras), paragraphs=len(paras), placeholder=ph.get("type") if ph is not None else None)

    def _frame(self, el, xf: _Xf) -> None:
        name, _ = _name(el, "nvGraphicFramePr")
        data = el.find(f".//{q('a', 'graphicData')}")
        uri = data.get("uri", "") if data is not None else ""
        if uri == _TABLE_URI:
            tbl = data.find(q("a", "tbl"))
            rows: list[list[str | None]] = []
            regions: list[list[int]] = []
            for r, tr in enumerate(tbl.findall(q("a", "tr"))):
                row: list[str | None] = []
                for c, tc in enumerate(tr.findall(q("a", "tc"))):
                    if tc.get("hMerge") in ("1", "true") or tc.get("vMerge") in ("1", "true"):
                        row.append(None)  # covered by a merge: same convention as the extraction's grid
                        continue
                    row.append(" ".join(t for t in (_para_text(p) for p in tc.iter(q("a", "p"))) if t.strip()))
                    cs, rs = int(tc.get("gridSpan", "1") or 1), int(tc.get("rowSpan", "1") or 1)
                    if cs > 1 or rs > 1:
                        regions.append([r, c, rs, cs])
                rows.append(row)
            grid = tbl.find(q("a", "tblGrid"))
            cols = len(grid.findall(q("a", "gridCol"))) if grid is not None else max((len(r) for r in rows), default=0)
            self._add(SourceObjectType.TABLE, el, xf, name, text=" ".join(c for r in rows for c in r if c),
                      rows=rows, row_count=len(rows), col_count=cols, has_merged_cells=bool(regions),
                      merged_regions=sorted(regions))
        elif uri == _CHART_URI:
            ch = data.find(q("c", "chart"))
            cpart = self.rels.get(ch.get(q("r", "id"), ""), ("", ""))[1] if ch is not None else ""
            info = {}
            if cpart and self.pkg.has(cpart):
                try:
                    root = self.pkg.xml(cpart)
                    plot = root.find(f".//{q('c', 'plotArea')}")
                    info = {"chart_types": [local(c.tag) for c in plot if local(c.tag).endswith("Chart")] if plot is not None else [],
                            "series_count": len(list(root.iter(q("c", "ser"))))}
                except Exception:  # noqa: BLE001
                    info = {}
            self._add(SourceObjectType.CHART, el, xf, name, chart_part=cpart, **info)
        else:
            self._add(SourceObjectType.OTHER, el, xf, name, contract=Contract.NOT_IN_CONTRACT,
                      note="SmartArt / OLE / other graphic frames are not extracted", uri=uri)


def inventory_pptx(path: Path, opts: InventoryOptions | None = None) -> SourceInventory:
    t0 = time.perf_counter()
    inv = SourceInventory(file_type="pptx", signals=list(SIGNALS), limitations=list(LIMITATIONS))
    try:
        with Package(path) as pkg:
            pres = pkg.xml("ppt/presentation.xml")
            rels = pkg.rels("ppt/presentation.xml")
            size = pres.find(q("p", "sldSz"))
            sw, sh = int(size.get("cx", 0)), int(size.get("cy", 0))
            ids = pres.find(q("p", "sldIdLst"))
            slides = [rels.get(s.get(q("r", "id"), ""), ("", ""))[1] for s in (list(ids) if ids is not None else [])]
            inv.properties.update(slide_count=len(slides), slide_width_emu=sw, slide_height_emu=sh)
            for i, part in enumerate(slides, start=1):
                unit = UnitRef(type=UnitType.SLIDE, index=i)
                su = SourceUnit(unit=unit, properties={"part": part})
                if not part or not pkg.has(part):
                    su.properties["unreadable"] = True
                    inv.units.append(su)
                    continue
                root = pkg.xml(part)
                su.properties["hidden"] = root.get("show") in ("0", "false")
                reader = _SlideReader(pkg, part, unit, sw, sh)
                tree = root.find(f"{q('p', 'cSld')}/{q('p', 'spTree')}")
                if tree is not None:
                    reader.walk(tree, _Xf())
                for rid, (typ, target) in pkg.rels(part).items():
                    if typ.endswith("/notesSlide") and pkg.has(target):
                        notes = " ".join(
                            "".join((n.text or "") for n in sp.iter(q("a", "t"))) for sp in pkg.xml(target).iter(q("p", "sp"))
                            if (sp.find(f".//{q('p', 'ph')}") is not None and sp.find(f".//{q('p', 'ph')}").get("type") == "body"))
                        if notes.strip():
                            reader.objects.append(SourceObject(
                                id=f"pptx:slide{i}:notes", type=SourceObjectType.TEXT, unit=unit, text=notes.strip(),
                                locator=SourceLocator(part=target, page=i), kind=DET, engine=f"ooxml:{target}",
                                contract=Contract.NOT_IN_CONTRACT, contract_note="speaker notes are not extracted",
                                metadata={"role": "speaker_notes"}))
                su.objects = reader.objects
                inv.units.append(su)
    except (PackageError, KeyError, ET.ParseError) as exc:
        inv.error = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 - an inventory failure never affects the extraction
        inv.error = f"{type(exc).__name__}: {exc}"
    inv.timing_ms = round((time.perf_counter() - t0) * 1000, 3)
    return inv
