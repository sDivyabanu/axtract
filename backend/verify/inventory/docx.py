"""DOCX source inventory, read from the raw package (independent of python-docx).

DOCX has no reliable rendered page geometry, so NOTHING here carries a page or bbox. Objects are
located by their logical position in the document body ("body[12]") and their XML part.

Observed deterministically: paragraphs, headings, list items, tables, pictures, native charts,
equations (OMML), sections, and headers/footers.

Contract notes:
  * paragraphs / headings / lists / tables / pictures / charts / equations are EXPECTED
  * headers, footers, footnotes, comments and text boxes are not extracted -> NOT_IN_CONTRACT
    (headers/footers are recorded; the others are listed as limitations)
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from defusedxml import ElementTree as ET

from verify.inventory.models import (
    Contract, InventorySignal, SourceInventory, SourceLocator, SourceObject, SourceObjectType, SourceUnit,
)
from verify.inventory.ooxml import Package, PackageError, local, q
from verify.inventory.options import InventoryOptions
from verify.models import EvidenceKind, UnitRef, UnitType

DET = EvidenceKind.DETERMINISTIC
SIGNALS = [
    InventorySignal(name="paragraphs_and_text", kind=DET, engine="ooxml:word/document.xml"),
    InventorySignal(name="headings", kind=DET, engine="ooxml:word/document.xml + word/styles.xml",
                    note="from paragraph style / outline level"),
    InventorySignal(name="list_items", kind=DET, engine="ooxml:word/document.xml",
                    note="numbering properties or a list style"),
    InventorySignal(name="tables_dimensions_and_cell_text", kind=DET, engine="ooxml:word/document.xml"),
    InventorySignal(name="pictures", kind=DET, engine="ooxml:word/document.xml (DrawingML / VML)"),
    InventorySignal(name="native_charts", kind=DET, engine="ooxml:word/document.xml + word/charts/chart*.xml"),
    InventorySignal(name="equations", kind=DET, engine="ooxml:word/document.xml (m:oMath)"),
    InventorySignal(name="sections", kind=DET, engine="ooxml:w:sectPr"),
    InventorySignal(name="headers_and_footers", kind=DET, engine="ooxml:word/header*.xml, footer*.xml",
                    note="recorded but outside AXTRACT's contract"),
    InventorySignal(name="rendered_page_geometry", kind=EvidenceKind.UNAVAILABLE, engine="none", available=False,
                    note="DOCX is a flow format: pages exist only after layout, so no page or bbox is claimed"),
]
LIMITATIONS = [
    "No page numbers or coordinates: DOCX pages exist only after layout.",
    "Footnotes, endnotes, comments, text boxes and tracked deletions are not inventoried.",
    "Heading and list detection follows paragraph styles and numbering, as Word stores them.",
]
_HEADING_STYLES = ("heading", "title", "subtitle")


def _style_names(pkg: Package) -> dict[str, str]:
    if not pkg.has("word/styles.xml"):
        return {}
    out = {}
    for st in pkg.xml("word/styles.xml").iter(q("w", "style")):
        nm = st.find(q("w", "name"))
        if nm is not None:
            out[st.get(q("w", "styleId"), "")] = (nm.get(q("w", "val")) or "").lower()
    return out


class _Reader:
    def __init__(self, pkg: Package, unit: UnitRef, styles: dict[str, str]):
        self.pkg, self.unit, self.styles = pkg, unit, styles
        self.rels = pkg.rels("word/document.xml")
        self.objects: list[SourceObject] = []
        self.pos = 0  # body position of the element being read
        self.counter = 0
        self.sections = 0
        self.textboxes = 0

    def _add(self, type_, path: str, text=None, **meta) -> None:
        self.counter += 1
        self.objects.append(SourceObject(
            id=f"docx:obj{self.counter}", type=type_, unit=self.unit,
            locator=SourceLocator(part="word/document.xml", position=self.counter, path=path),
            text=text, kind=DET, engine="ooxml:word/document.xml", metadata=meta))

    # -- inline content ---------------------------------------------------------------------
    def _inline(self, el, text: list[str], found: list[tuple[str, object]]) -> None:
        for ch in el:
            t = local(ch.tag)
            if t == "r":
                for sub in ch:
                    s = local(sub.tag)
                    if s == "t":
                        text.append(sub.text or "")
                    elif s == "tab":
                        text.append("\t")
                    elif s in ("br", "cr"):
                        text.append("\n")
                    elif s == "drawing":
                        found.append(("drawing", sub))
                    elif s in ("pict", "object"):
                        found.append(("vml", sub))
                    elif s == "AlternateContent":
                        choice = sub.find(q("mc", "Choice"))
                        for inner in (choice if choice is not None else []):
                            if local(inner.tag) == "drawing":
                                found.append(("drawing", inner))
                    elif s in ("oMath", "oMathPara"):
                        found.append(("math", sub))
            elif t in ("hyperlink", "ins", "smartTag", "fldSimple", "sdtContent"):
                self._inline(ch, text, found)
            elif t == "sdt":
                c = ch.find(q("w", "sdtContent"))
                if c is not None:
                    self._inline(c, text, found)
            elif t in ("oMath", "oMathPara"):
                found.append(("math", ch))
            # w:del (tracked deletions) is deliberately skipped

    def _paragraph(self, p, path: str) -> None:
        ppr = p.find(q("w", "pPr"))
        sid = ""
        has_num = outline = False
        if ppr is not None:
            ps = ppr.find(q("w", "pStyle"))
            sid = ps.get(q("w", "val"), "") if ps is not None else ""
            has_num = ppr.find(q("w", "numPr")) is not None
            outline = ppr.find(q("w", "outlineLvl")) is not None
            if ppr.find(q("w", "sectPr")) is not None:
                self.sections += 1
        style = self.styles.get(sid, sid.lower())
        text: list[str] = []
        found: list[tuple[str, object]] = []
        self._inline(p, text, found)
        body = "".join(text).strip()
        if body:
            meta: dict = {"style": style or None}
            if any(style.startswith(h) for h in _HEADING_STYLES) or outline:
                typ = SourceObjectType.HEADING
                digits = re.search(r"(\d+)", style)
                if digits:
                    meta["level"], meta["level_source"] = int(digits.group(1)), "style name"
                elif style == "title":
                    meta["level"], meta["level_source"] = 1, "style name"
            elif has_num or "list" in style:
                typ = SourceObjectType.LIST_ITEM
                ilvl = ppr.find(q("w", "numPr") + "/" + q("w", "ilvl")) if ppr is not None else None
                if ilvl is not None:
                    meta["list_level"] = int(ilvl.get(q("w", "val"), "0"))
            else:
                typ = SourceObjectType.TEXT
            self._add(typ, path, body, **meta)
        for kind, node in found:
            if kind == "math":
                self._add(SourceObjectType.EQUATION, path, "".join((n.text or "") for n in node.iter(q("m", "t"))),
                          display=local(node.tag) == "oMathPara")
            elif kind == "drawing":
                chart = node.find(f".//{q('c', 'chart')}")
                pic = node.find(f".//{q('pic', 'pic')}")
                docpr = node.find(f".//{q('wp', 'docPr')}")
                if chart is not None:
                    self._add(SourceObjectType.CHART, path, chart_part=self.rels.get(chart.get(q("r", "id"), ""), ("", ""))[1])
                elif pic is not None:
                    self._add(SourceObjectType.PICTURE, path, name=docpr.get("name") if docpr is not None else None,
                              alt_text=docpr.get("descr", "") if docpr is not None else "")
                else:
                    self.textboxes += 1  # shapes / text boxes: outside the contract and not inventoried
            elif kind == "vml" and node.find(f".//{q('v', 'imagedata')}") is not None:
                self._add(SourceObjectType.PICTURE, path, legacy_vml=True)

    # -- tables -------------------------------------------------------------------------------
    def _table(self, tbl, path: str) -> None:
        """Rows expanded to the table grid; cells covered by a merge are None (the extraction's convention)."""
        grid_el = tbl.find(q("w", "tblGrid"))
        n_cols = len(grid_el.findall(q("w", "gridCol"))) if grid_el is not None else 0
        raw: list[list[dict]] = []
        nested = 0
        for tr in tbl.findall(q("w", "tr")):
            cells, col = [], 0
            for tc in tr.findall(q("w", "tc")):
                pr = tc.find(q("w", "tcPr"))
                gs = pr.find(q("w", "gridSpan")) if pr is not None else None
                vm = pr.find(q("w", "vMerge")) if pr is not None else None
                span = int(gs.get(q("w", "val"), "1")) if gs is not None else 1
                nested += len(list(tc.iter(q("w", "tbl"))))
                text = " ".join(
                    t for t in ("".join((n.text or "") for n in p.iter(q("w", "t"))) for p in tc.iter(q("w", "p"))) if t.strip())
                vmerge = None if vm is None else ("restart" if vm.get(q("w", "val")) == "restart" else "continue")
                cells.append({"col": col, "span": span, "vmerge": vmerge, "text": text})
                col += span
            n_cols = max(n_cols, col)
            raw.append(cells)
        rows: list[list[str | None]] = [[None] * n_cols for _ in raw]
        regions: list[list[int]] = []
        for r, cells in enumerate(raw):
            for cell in cells:
                if cell["vmerge"] == "continue":
                    continue
                rows[r][cell["col"]] = cell["text"]
                rowspan = 1
                if cell["vmerge"] == "restart":
                    while r + rowspan < len(raw) and any(
                            o["col"] == cell["col"] and o["vmerge"] == "continue" for o in raw[r + rowspan]):
                        rowspan += 1
                if rowspan > 1 or cell["span"] > 1:
                    regions.append([r, cell["col"], rowspan, cell["span"]])
        self._add(SourceObjectType.TABLE, path, " ".join(c for r in rows for c in r if c),
                  rows=rows, row_count=len(rows), col_count=n_cols, has_merged_cells=bool(regions),
                  merged_regions=sorted(regions), nested_tables=nested)

    def walk(self, container, prefix: str = "body") -> None:
        for i, ch in enumerate(container, start=1):
            t, path = local(ch.tag), f"{prefix}[{i}]"
            if t == "p":
                self._paragraph(ch, path)
            elif t == "tbl":
                self._table(ch, path)
            elif t == "sdt":
                c = ch.find(q("w", "sdtContent"))
                if c is not None:
                    self.walk(c, path)
            elif t == "sectPr":
                self.sections += 1


def inventory_docx(path: Path, opts: InventoryOptions | None = None) -> SourceInventory:
    t0 = time.perf_counter()
    inv = SourceInventory(file_type="docx", signals=list(SIGNALS), limitations=list(LIMITATIONS))
    unit = UnitRef(type=UnitType.SECTION, index=1)
    try:
        with Package(path) as pkg:
            body = pkg.xml("word/document.xml").find(q("w", "body"))
            reader = _Reader(pkg, unit, _style_names(pkg))
            if body is not None:
                reader.walk(body)
            # headers and footers: recorded, outside AXTRACT's contract
            seen: set[str] = set()
            for rid, (typ, target) in reader.rels.items():
                if typ.endswith(("/header", "/footer")) and target not in seen and pkg.has(target):
                    seen.add(target)
                    txt = " ".join(
                        "".join((n.text or "") for n in p.iter(q("w", "t"))) for p in pkg.xml(target).iter(q("w", "p"))).strip()
                    if txt:
                        reader.counter += 1
                        reader.objects.append(SourceObject(
                            id=f"docx:obj{reader.counter}", type=SourceObjectType.TEXT, unit=unit, text=txt,
                            locator=SourceLocator(part=target), kind=DET, engine=f"ooxml:{target}",
                            contract=Contract.NOT_IN_CONTRACT, contract_note="headers and footers are not extracted",
                            metadata={"role": "header" if typ.endswith("/header") else "footer"}))
            inv.properties.update(sections=max(reader.sections, 1), text_boxes_not_inventoried=reader.textboxes)
            inv.units.append(SourceUnit(unit=unit, objects=reader.objects, properties={"sections": max(reader.sections, 1)}))
    except (PackageError, KeyError, ET.ParseError) as exc:
        inv.error = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 - an inventory failure never affects the extraction
        inv.error = f"{type(exc).__name__}: {exc}"
    inv.timing_ms = round((time.perf_counter() - t0) * 1000, 3)
    return inv
