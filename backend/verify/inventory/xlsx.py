"""XLSX source inventory, read from the raw OOXML (independent of openpyxl).

Observed deterministically: sheets, visibility, populated cells and their cached values, formula
presence and coordinates, merged ranges, Excel tables, charts, pictures, hidden rows/columns.

Contract notes (what AXTRACT's XLSX extractor does and does not represent):
  * one TABLE block per non-empty sheet, hidden sheets included; empty sheets yield no block
  * cell VALUES are kept (cached results); formula TEXT is not -> FORMULA objects are NOT_IN_CONTRACT
  * charts, pictures and drawings are not read by the extractor today -> they are EXPECTED here,
    so a workbook containing one is reported as possibly incomplete. That is a real content gap,
    not a verifier artefact.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from defusedxml import ElementTree as ET

from verify.inventory.models import (
    Contract, InventorySignal, SourceInventory, SourceLocator, SourceObject, SourceObjectType, SourceUnit,
)
from verify.inventory.ooxml import Package, PackageError, q, text_of, local
from verify.inventory.options import InventoryOptions
from verify.models import EvidenceKind, UnitRef, UnitType

DET = EvidenceKind.DETERMINISTIC
_REF = re.compile(r"^([A-Z]+)(\d+)$")

SIGNALS = [
    InventorySignal(name="sheet_names_and_count", kind=DET, engine="ooxml:xl/workbook.xml"),
    InventorySignal(name="sheet_visibility", kind=DET, engine="ooxml:xl/workbook.xml"),
    InventorySignal(name="populated_cells_and_coordinates", kind=DET, engine="ooxml:xl/worksheets/sheet*.xml"),
    InventorySignal(name="cached_cell_values", kind=DET, engine="ooxml:sheet*.xml + sharedStrings.xml",
                    note="raw stored values: dates and times appear as serial numbers; number formats are not interpreted"),
    InventorySignal(name="formula_presence_and_coordinates", kind=DET, engine="ooxml:xl/worksheets/sheet*.xml"),
    InventorySignal(name="merged_ranges", kind=DET, engine="ooxml:xl/worksheets/sheet*.xml"),
    InventorySignal(name="hidden_rows_and_columns", kind=DET, engine="ooxml:xl/worksheets/sheet*.xml"),
    InventorySignal(name="excel_tables", kind=DET, engine="ooxml:xl/tables/table*.xml"),
    InventorySignal(name="charts_and_pictures", kind=DET, engine="ooxml:xl/drawings/drawing*.xml",
                    note="existence, type and anchor only; chart data is not read at this step"),
]
LIMITATIONS = [
    "Cell values are the raw stored values; dates/times are serial numbers and formats are not applied.",
    "Formulas without a cached result have no value to lose; they are recorded but not counted as populated cells.",
    "Text boxes and shapes inside drawings are not inventoried.",
    "Pivot tables, data validation, comments and styles are out of scope.",
]


def col_to_num(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def num_to_col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def split_ref(ref: str) -> tuple[int, int] | None:
    m = _REF.match(ref or "")
    return (int(m.group(2)), col_to_num(m.group(1))) if m else None


def range_bounds(rng: str) -> tuple[int, int, int, int] | None:
    parts = (rng or "").replace("$", "").split(":")
    a = split_ref(parts[0])
    b = split_ref(parts[-1])
    return (a[0], a[1], b[0], b[1]) if a and b else None


def _shared_strings(pkg: Package) -> list[str]:
    if not pkg.has("xl/sharedStrings.xml"):
        return []
    t_tag, r_tag, si_tag = q("main", "t"), q("main", "r"), q("main", "si")
    out: list[str] = []
    with pkg.open("xl/sharedStrings.xml") as fh:
        for _, el in ET.iterparse(fh, events=("end",)):
            if el.tag == si_tag:
                parts: list[str] = []
                for child in el:  # direct <t> or rich-text <r><t>; phonetic <rPh> runs are skipped
                    if child.tag == t_tag:
                        parts.append(child.text or "")
                    elif child.tag == r_tag:
                        parts.extend((t.text or "") for t in child.iter(t_tag))
                out.append("".join(parts))
                el.clear()
    return out


def _parse_sheet(pkg: Package, part: str, shared: list[str], opts: InventoryOptions, budget: dict) -> dict:
    cells: dict[str, str] = {}
    types: dict[str, str] = {}  # n(umber), s(tring), b(oolean), e(rror), d(ate)
    formulas: dict[str, str] = {}
    formula_uncached = 0
    hidden_rows: list[int] = []
    hidden_cols: list[tuple[int, int]] = []
    merged: list[str] = []
    drawing_rid = None
    table_rids: list[str] = []
    min_r = min_c = 10**9
    max_r = max_c = 0
    truncated = False
    with pkg.open(part) as fh:
        for _, el in ET.iterparse(fh, events=("end",)):
            tag = el.tag
            if tag == q("main", "c"):
                ref = el.get("r", "")
                t = el.get("t")
                v, f, is_ = el.find(q("main", "v")), el.find(q("main", "f")), el.find(q("main", "is"))
                if t == "s" and v is not None and (v.text or "").isdigit() and int(v.text) < len(shared):
                    val = shared[int(v.text)]
                elif t == "inlineStr" and is_ is not None:
                    val = "".join((x.text or "") for x in is_.iter(q("main", "t")))
                else:
                    val = v.text if v is not None and v.text is not None else ""
                has_formula = f is not None
                if has_formula:
                    if len(formulas) < opts.max_listed:
                        formulas[ref] = (f.text or "")
                    if not val.strip():
                        formula_uncached += 1
                if val.strip():
                    if len(cells) < opts.max_cells:
                        cells[ref] = val
                        types[ref] = {"s": "s", "str": "s", "inlineStr": "s", "b": "b", "e": "e", "d": "d"}.get(t, "n")
                        rc = split_ref(ref)
                        if rc:
                            min_r, max_r = min(min_r, rc[0]), max(max_r, rc[0])
                            min_c, max_c = min(min_c, rc[1]), max(max_c, rc[1])
                    else:
                        truncated = True
                el.clear()
            elif tag == q("main", "row"):
                if el.get("hidden") in ("1", "true") and len(hidden_rows) < opts.max_listed and (el.get("r") or "").isdigit():
                    hidden_rows.append(int(el.get("r")))
                el.clear()
            elif tag == q("main", "col"):
                if el.get("hidden") in ("1", "true"):
                    hidden_cols.append((int(el.get("min", "0")), int(el.get("max", "0"))))
            elif tag == q("main", "mergeCell"):
                if len(merged) < opts.max_listed and el.get("ref"):
                    merged.append(el.get("ref"))
            elif tag == q("main", "drawing"):
                drawing_rid = el.get(q("r", "id"))
            elif tag == q("main", "tablePart"):
                table_rids.append(el.get(q("r", "id")))
    used = None
    if cells and max_r:
        used = f"{num_to_col(min_c)}{min_r}:{num_to_col(max_c)}{max_r}"
    return dict(cells=cells, types=types, formulas=formulas, formula_uncached=formula_uncached, hidden_rows=hidden_rows,
                hidden_cols=hidden_cols, merged=merged, drawing_rid=drawing_rid, table_rids=table_rids,
                used_range=used, truncated=truncated)


def _chart_info(pkg: Package, part: str) -> dict:
    try:
        root = pkg.xml(part)
    except Exception:  # noqa: BLE001 - a broken chart part must not stop the inventory
        return {}
    plot = root.find(f".//{q('c', 'plotArea')}")
    kinds = [local(ch.tag) for ch in plot if local(ch.tag).endswith("Chart")] if plot is not None else []
    title = root.find(f".//{q('c', 'title')}")
    return {"chart_types": kinds, "series_count": len(list(root.iter(q("c", "ser")))),
            "title": text_of(title, "t").strip() if title is not None else ""}


def _anchor_cell(anchor) -> str | None:
    frm = anchor.find(q("xdr", "from"))
    if frm is None:
        return None
    try:
        col = int(frm.find(q("xdr", "col")).text)
        row = int(frm.find(q("xdr", "row")).text)
    except (AttributeError, TypeError, ValueError):
        return None
    return f"{num_to_col(col + 1)}{row + 1}"


def _drawing_objects(pkg: Package, part: str, unit: UnitRef, sheet_no: int) -> list[SourceObject]:
    out: list[SourceObject] = []
    rels = pkg.rels(part)
    root = pkg.xml(part)
    n_chart = n_pic = 0
    for anchor in root:
        if local(anchor.tag) not in ("twoCellAnchor", "oneCellAnchor", "absoluteAnchor"):
            continue
        cell = _anchor_cell(anchor)
        for pic in anchor.iter(q("xdr", "pic")):
            n_pic += 1
            blip = pic.find(f".//{q('a', 'blip')}")
            target = rels.get(blip.get(q("r", "embed"), ""), ("", ""))[1] if blip is not None else ""
            cnv = pic.find(f".//{q('xdr', 'cNvPr')}")
            out.append(SourceObject(
                id=f"xlsx:sheet{sheet_no}:picture{n_pic}", type=SourceObjectType.PICTURE, unit=unit,
                locator=SourceLocator(part=part, position=n_pic, cell_range=cell,
                                      shape_name=cnv.get("name") if cnv is not None else None),
                kind=DET, engine=f"ooxml:{part}", metadata={"media": target, "anchor": cell}))
        for frame in anchor.iter(q("xdr", "graphicFrame")):
            ch = frame.find(f".//{q('c', 'chart')}")
            if ch is None:
                continue
            n_chart += 1
            cpart = rels.get(ch.get(q("r", "id"), ""), ("", ""))[1]
            info = _chart_info(pkg, cpart) if cpart and pkg.has(cpart) else {}
            out.append(SourceObject(
                id=f"xlsx:sheet{sheet_no}:chart{n_chart}", type=SourceObjectType.CHART, unit=unit,
                locator=SourceLocator(part=cpart or part, position=n_chart, cell_range=cell),
                text=info.get("title") or None, kind=DET, engine=f"ooxml:{cpart or part}",
                metadata={**info, "anchor": cell}))
    return out


def inventory_xlsx(path: Path, opts: InventoryOptions | None = None) -> SourceInventory:
    opts = opts or InventoryOptions()
    t0 = time.perf_counter()
    inv = SourceInventory(file_type="xlsx", signals=list(SIGNALS), limitations=list(LIMITATIONS))
    budget = {"cells": 0}
    try:
        with Package(path) as pkg:
            wb = pkg.xml("xl/workbook.xml")
            wb_rels = pkg.rels("xl/workbook.xml")
            shared = _shared_strings(pkg)
            sheets = wb.find(q("main", "sheets"))
            entries = list(sheets) if sheets is not None else []
            inv.properties["sheet_count"] = len(entries)
            wb_pr = wb.find(q("main", "workbookPr"))
            inv.properties["date1904"] = bool(wb_pr is not None and wb_pr.get("date1904") in ("1", "true"))
            for pos, sh in enumerate(entries, start=1):
                name = sh.get("name", f"Sheet{pos}")
                unit = UnitRef(type=UnitType.SHEET, index=pos, label=name)
                part = wb_rels.get(sh.get(q("r", "id"), ""), ("", ""))[1]
                su = SourceUnit(unit=unit, properties={"state": sh.get("state", "visible"), "part": part})
                if not part or not pkg.has(part):
                    su.properties["unreadable"] = True
                    inv.units.append(su)
                    continue
                s = _parse_sheet(pkg, part, shared, opts, budget)
                su.properties.update(used_range=s["used_range"], non_empty_cells=len(s["cells"]),
                                     formula_cells=len(s["formulas"]), formulas_without_cached_value=s["formula_uncached"],
                                     hidden_rows=s["hidden_rows"], hidden_columns=s["hidden_cols"],
                                     merged_range_count=len(s["merged"]), truncated=s["truncated"])
                inv.truncated = inv.truncated or s["truncated"]
                if s["cells"]:
                    su.objects.append(SourceObject(
                        id=f"xlsx:sheet{pos}:cells", type=SourceObjectType.CELL_REGION, unit=unit,
                        locator=SourceLocator(part=part, cell_range=s["used_range"]), kind=DET, engine=f"ooxml:{part}",
                        metadata={"cells": s["cells"], "cell_types": s["types"], "formula_cells": sorted(s["formulas"]),
                                  "non_empty_cells": len(s["cells"]), "truncated": s["truncated"]}))
                for i, ref in enumerate(s["merged"], start=1):
                    su.objects.append(SourceObject(
                        id=f"xlsx:sheet{pos}:merge{i}", type=SourceObjectType.MERGED_RANGE, unit=unit,
                        locator=SourceLocator(part=part, position=i, cell_range=ref), kind=DET, engine=f"ooxml:{part}"))
                if s["formulas"]:
                    su.objects.append(SourceObject(
                        id=f"xlsx:sheet{pos}:formulas", type=SourceObjectType.FORMULA, unit=unit,
                        locator=SourceLocator(part=part), kind=DET, engine=f"ooxml:{part}",
                        contract=Contract.NOT_IN_CONTRACT,
                        contract_note="AXTRACT stores cached values, not formula text",
                        metadata={"coordinates": sorted(s["formulas"]), "count": len(s["formulas"]),
                                  "without_cached_value": s["formula_uncached"]}))
                srels = pkg.rels(part)
                for i, rid in enumerate(s["table_rids"], start=1):
                    tpart = srels.get(rid, ("", ""))[1]
                    if tpart and pkg.has(tpart):
                        troot = pkg.xml(tpart)
                        su.objects.append(SourceObject(
                            id=f"xlsx:sheet{pos}:table{i}", type=SourceObjectType.TABLE, unit=unit,
                            locator=SourceLocator(part=tpart, position=i, cell_range=troot.get("ref")),
                            text=troot.get("displayName") or troot.get("name"), kind=DET, engine=f"ooxml:{tpart}",
                            metadata={"name": troot.get("name"), "ref": troot.get("ref")}))
                if s["drawing_rid"]:
                    dpart = srels.get(s["drawing_rid"], ("", ""))[1]
                    if dpart and pkg.has(dpart):
                        su.objects.extend(_drawing_objects(pkg, dpart, unit, pos))
                inv.units.append(su)
    except (PackageError, KeyError, ET.ParseError) as exc:
        inv.error = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 - an inventory failure never affects the extraction
        inv.error = f"{type(exc).__name__}: {exc}"
    inv.timing_ms = round((time.perf_counter() - t0) * 1000, 3)
    return inv
