"""AXTRACT Verify, layer 4: structure correctness.

Is the extracted object the right KIND of thing, with the right shape?

Checked against the source structure
  tables      rows and columns, merged cells, and row / column order (PPTX, DOCX, XLSX: exact, read
              from the file's own XML)
  objects     a heading must come out as a heading, a list item as a list item; heading levels
  charts      a native chart should be represented as a chart, not an empty image placeholder
  flattening  a source table whose text survives only as ordinary paragraphs

Evidence classes
  deterministic   PPTX / DOCX / XLSX structure comes straight from the XML
  heuristic       PDF table candidates (ruling-line detection) are evidence, not ground truth, so any
                  PDF structure finding is labelled heuristic and kept low severity where the
                  candidate and the extractor are merely two readings of the same page
  not verifiable  PDF headings/lists (font-size heuristics) and images have no independent structure
                  evidence; this is stated, never assumed

This layer deliberately does not re-report value differences (content layer) or missing objects
(completeness layer): a table whose cells changed is a content finding; one that lost a row is a
structure finding.
"""

from __future__ import annotations

from typing import Any

from models.document import BlockType as T, DocumentBlock, DocumentResponse
from verify.completeness import MatchRecord, MatchStatus, _bbox, block_text
from verify.content import is_ocr_block
from verify.ids import IdSequence
from verify.inventory.models import Contract, SourceInventory, SourceObjectType as SO
from verify.inventory.xlsx import range_bounds
from verify.layer import LayerBuilder, LayerOutcome
from verify.matching import recall
from verify.models import CheckName, CheckOutcome, EvidenceKind, Severity, UnitRef, UnitType
from verify.tables import TableDiff, diff_tables, moved

DET, HEUR = EvidenceKind.DETERMINISTIC, EvidenceKind.HEURISTIC

CODE_CATALOG: dict[str, tuple[Severity, str]] = {
    "table_dimensions_mismatch": (Severity.MEDIUM, "the extracted table has a different number of rows or columns than the source"),
    "table_row_order_changed": (Severity.MEDIUM, "the extracted table has the source's rows in a different order"),
    "table_column_order_changed": (Severity.MEDIUM, "the extracted table has the source's columns in a different order"),
    "merged_cells_mismatch": (Severity.MEDIUM, "the extracted merged-cell structure differs from the source"),
    "table_flattened_into_text": (Severity.MEDIUM, "a source table's text survives only as ordinary paragraphs"),
    "object_type_mismatch": (Severity.MEDIUM, "a heading / list item was extracted as a different kind of block"),
    "heading_level_mismatch": (Severity.MEDIUM, "the extracted heading level differs from the source's heading style"),
    "chart_not_structured": (Severity.MEDIUM, "a native chart was extracted only as an image placeholder, without its data"),
}
_OK_TYPES = {SO.HEADING: {"heading"}, SO.LIST_ITEM: {"list"}, SO.TEXT: {"paragraph", "header", "footer", "list"}}


def _regions(v) -> set[tuple[int, ...]]:
    return {tuple(int(x) for x in r) for r in (v or []) if isinstance(r, (list, tuple)) and len(r) == 4}


def _table_structure(lb: LayerBuilder, u: UnitRef, src_rows, out_rows, out_block: DocumentBlock, src_regions, *, kind: EvidenceKind,
                     engine: str, label: str, severity_dims: Severity | None = None, source_object: str | None = None,
                     precomputed: TableDiff | None = None) -> None:
    d = precomputed or diff_tables(src_rows, out_rows)
    lb.examine(u, kind, tables=1)
    common = dict(kind=kind, engine=engine, block_ids=[out_block.id], bbox=_bbox(out_block))
    ctx = {"source_object": source_object}
    if d.kind == "dimensions":
        lb.find(u, "table_dimensions_mismatch", f"{label}: the extracted table is {d.out_dims[0]}x{d.out_dims[1]}, the source {d.src_dims[0]}x{d.src_dims[1]}.",
                severity=severity_dims, source=f"{d.src_dims[0]} rows x {d.src_dims[1]} columns",
                extracted=f"{d.out_dims[0]} rows x {d.out_dims[1]} columns", detail={**ctx, "source_dims": list(d.src_dims), "output_dims": list(d.out_dims)},
                **common)
        return
    if d.kind == "rows_reordered":
        moved = [r + 1 for r in d.detail["rows_out_of_place"]]
        lb.find(u, "table_row_order_changed", f"{label}: the same rows appear in a different order.", source="source row order",
                extracted=f"rows {moved} are out of place", detail={**ctx, "rows_out_of_place": moved}, **common)
    elif d.kind == "columns_reordered":
        moved = [c + 1 for c in d.detail["columns_out_of_place"]]
        lb.find(u, "table_column_order_changed", f"{label}: the same columns appear in a different order.", source="source column order",
                extracted=f"columns {moved} are out of place", detail={**ctx, "columns_out_of_place": moved}, **common)
    have = _regions((out_block.metadata.get("merged_cells") or {}).get("merged_regions"))
    want = _regions(src_regions)
    if want != have:
        lb.find(u, "merged_cells_mismatch", f"{label}: merged cells differ (source {len(want)}, output {len(have)} region(s)).", **common,
                source=f"merged regions (row, col, rowspan, colspan): {sorted(want)}", extracted=f"{sorted(have)}",
                detail={**ctx, "missing_in_output": sorted(want - have), "extra_in_output": sorted(have - want)})


def _office(lb: LayerBuilder, inv: SourceInventory, resp: DocumentResponse, matches: list[MatchRecord]) -> None:
    blocks = {b.id: b for b in resp.blocks}
    objs = {o.id: o for o in inv.objects()}
    for m in matches:
        o = objs.get(m.object_id)
        if o is None or o.contract != Contract.EXPECTED:
            continue
        u = lb.unit(o.unit)
        got = [blocks[i] for i in m.block_ids if i in blocks]
        if m.status == MatchStatus.UNMATCHED and o.type == SO.TABLE:
            others = " ".join(block_text(b) for b in resp.blocks if b.type != T.TABLE and (inv.file_type == "docx" or b.page == u.index))
            r, _ = recall(o.text, others)
            if r >= 0.8:
                lb.find(u, "table_flattened_into_text", f"A {o.metadata.get('row_count')}x{o.metadata.get('col_count')} table was extracted as ordinary text (no table structure).",
                        kind=DET, engine=o.engine, source=f"table {o.metadata.get('row_count')}x{o.metadata.get('col_count')}",
                        extracted=f"its text is present in other blocks (recall {r:.0%}) but no table block exists",
                        detail={"text_recall_in_other_blocks": round(r, 4), "source_object": o.id}, bbox=o.locator.bbox)
            continue
        if m.status != MatchStatus.MATCHED or not got:
            continue
        b = got[0]
        if o.type == SO.TABLE:
            _table_structure(lb, u, o.metadata.get("rows"), b.metadata.get("rows"), b, o.metadata.get("merged_regions"), kind=DET, engine=o.engine,
                             label=f"Table at {o.locator.path or o.locator.shape_name}", source_object=o.id)
        elif o.type in (SO.HEADING, SO.LIST_ITEM, SO.TEXT):
            lb.examine(u, DET, objects=1)
            if b.type.value not in _OK_TYPES[o.type] or (o.type == SO.TEXT and b.type == T.HEADING):
                lb.find(u, "object_type_mismatch", f"A source {o.type.value.replace('_', ' ')} was extracted as a {b.type.value}.", kind=DET,
                        engine=o.engine, source=f"{o.type.value} ({(o.text or '')[:60]!r})", extracted=f"{b.type.value} block {b.id}",
                        block_ids=[b.id], bbox=_bbox(b), detail={"path": o.locator.path, "source_object": o.id})
            elif o.type == SO.HEADING and o.metadata.get("level") and isinstance(b.metadata, dict) and b.metadata.get("heading_level") \
                    and b.metadata["heading_level"] != o.metadata["level"]:
                lb.find(u, "heading_level_mismatch", f"Heading level is {b.metadata['heading_level']}, the source style says {o.metadata['level']}.",
                        kind=DET, engine=o.engine, source=f"level {o.metadata['level']} ({o.metadata.get('style')})",
                        extracted=f"level {b.metadata['heading_level']}", block_ids=[b.id], bbox=_bbox(b), detail={"path": o.locator.path})
        elif o.type == SO.CHART:
            lb.examine(u, DET, objects=1)
            if b.type != T.CHART:
                lb.find(u, "chart_not_structured", "A native chart was extracted only as an image placeholder, without its data.", kind=DET,
                        engine=o.engine, source=f"chart ({', '.join(o.metadata.get('chart_types', [])) or 'type unknown'}, {o.metadata.get('series_count', '?')} series)",
                        extracted=f"{b.type.value} block: {b.content[:60]!r}", block_ids=[b.id], bbox=_bbox(b), detail={"source_object": o.id})
        elif o.type in (SO.PICTURE, SO.EQUATION):
            lb.examine(u, DET, objects=1)


def _xlsx(lb: LayerBuilder, inv: SourceInventory, resp: DocumentResponse, matches: list[MatchRecord]) -> None:
    from verify.content import xlsx_grids

    blocks = {b.id: b for b in resp.blocks}
    by_obj = {m.object_id: m for m in matches}
    date1904 = bool(inv.properties.get("date1904"))
    for su in inv.units:
        u = lb.unit(su.unit)
        cells = next((o for o in su.objects if o.type == SO.CELL_REGION), None)
        m = by_obj.get(cells.id) if cells else None
        if cells is None or m is None or not m.block_ids or cells.metadata.get("truncated"):
            continue
        blk = blocks[m.block_ids[0]]
        a, b, _ = xlsx_grids(cells, blk.metadata.get("rows") or [], date1904)
        pre = None
        if diff_tables(a, b).kind not in ("identical", "rows_reordered", "columns_reordered"):
            la, lbg, _ = xlsx_grids(cells, blk.metadata.get("rows") or [], date1904, loose=True)
            if (mv := moved(a, b, la, lbg)):
                key = "rows_out_of_place" if mv[0] == "rows_reordered" else "columns_out_of_place"
                pre = TableDiff(mv[0], (len(a), len(a[0]) if a else 0), (len(b), len(b[0]) if b else 0), [], 0, {key: mv[1]})
        meta = blk.metadata
        src_regions = []
        n_out_rows = len(meta.get("rows") or [])
        for o in su.objects:
            if o.type == SO.MERGED_RANGE and (bb := range_bounds(o.locator.cell_range or "")) and (bb[0], bb[1]) != (bb[2], bb[3]) \
                    and bb[0] - 1 < n_out_rows:
                src_regions.append((bb[0] - 1, bb[1] - 1, bb[2] - bb[0] + 1, bb[3] - bb[1] + 1))
        merge_info = "merged_cells" in meta and "merged_cells_not_read_large_file" not in (meta.get("flags") or [])
        want = src_regions if merge_info else (meta.get("merged_cells") or {}).get("merged_regions")  # unread: nothing to compare
        _table_structure(lb, u, a, b, blk, want, kind=DET, engine=cells.engine, label=f"Sheet '{u.label}'", source_object=cells.id, precomputed=pre)


def _pdf(lb: LayerBuilder, inv: SourceInventory, resp: DocumentResponse, matches: list[MatchRecord]) -> None:
    blocks = {b.id: b for b in resp.blocks}
    by_obj = {m.object_id: m for m in matches}
    for su in inv.units:
        u = lb.unit(su.unit)
        page_blocks = [b for b in resp.blocks if b.page == u.index]
        if any(b.type in (T.HEADING, T.LIST) for b in page_blocks):
            lb.note(u, CheckOutcome.NOT_VERIFIABLE,
                    "heading and list structure of PDFs is inferred from font size and bullets: there is no independent evidence of it", "structure.pdf")
        for o in su.objects:
            if o.type != SO.TABLE:
                continue
            m = by_obj.get(o.id)
            if m is None:
                continue
            if m.status == MatchStatus.MATCHED and m.block_ids:
                b = blocks[m.block_ids[0]]
                lb.examine(u, HEUR, tables=1)
                if (o.metadata["row_count"], o.metadata["col_count"]) != (b.metadata.get("row_count"), b.metadata.get("col_count")) \
                        and not b.metadata.get("is_cross_page_merged"):
                    lb.find(u, "table_dimensions_mismatch",
                            f"A ruled table candidate is {o.metadata['row_count']}x{o.metadata['col_count']}; the extracted table is {b.metadata.get('row_count')}x{b.metadata.get('col_count')}.",
                            kind=HEUR, engine=o.engine, severity=Severity.LOW, block_ids=[b.id], bbox=o.locator.bbox,
                            source=f"{o.metadata['row_count']}x{o.metadata['col_count']} (pdfplumber candidate)",
                            extracted=f"{b.metadata.get('row_count')}x{b.metadata.get('col_count')}",
                            detail={"limitation": "two readers of the same page may segment a table differently"})
            elif m.status == MatchStatus.UNMATCHED:
                others = " ".join(block_text(x) for x in page_blocks if x.type != T.TABLE)
                r, _ = recall(o.text, others)
                if r >= 0.8:
                    lb.find(u, "table_flattened_into_text", "A ruled table candidate was extracted as ordinary text (no table structure).",
                            kind=HEUR, engine=o.engine, bbox=o.locator.bbox, source=f"{o.metadata['row_count']}x{o.metadata['col_count']} table candidate",
                            extracted=f"its text is present in other blocks (recall {r:.0%}) but no table block exists",
                            detail={"text_recall_in_other_blocks": round(r, 4), "limitation": o.metadata.get("limitation")})


def _image(lb: LayerBuilder, inv: SourceInventory, resp: DocumentResponse, matches: list[MatchRecord]) -> None:
    for su in inv.units:
        lb.note(su.unit, CheckOutcome.NOT_VERIFIABLE, "layout structure of an image cannot be verified without an independent reader", "structure.image")


_COMPARATORS = {"xlsx": _xlsx, "pptx": _office, "docx": _office, "pdf": _pdf, "png": _image, "jpg": _image, "jpeg": _image}


def check_structure(inv: SourceInventory, resp: DocumentResponse, matches: list[MatchRecord], ids: IdSequence | None = None) -> LayerOutcome:
    """Compare the structure of the extraction with the structure of the source. Pure."""
    lb = LayerBuilder(CheckName.STRUCTURE, CODE_CATALOG, ids)
    if inv.error:
        lb.notes.append(f"source inventory unavailable ({inv.error}); structure could not be compared")
        lb.note(UnitRef(type=UnitType.DOCUMENT, index=1), CheckOutcome.NOT_VERIFIABLE, lb.notes[-1], "structure")
    elif (cmp_ := _COMPARATORS.get(inv.file_type)) is None:
        lb.notes.append(f"no structure comparator for .{inv.file_type}")
    else:
        try:
            cmp_(lb, inv, resp, matches)
        except Exception as exc:  # noqa: BLE001
            lb.notes.append(f"structure comparison failed ({type(exc).__name__}: {exc})")
            lb._findings.clear()
            lb._examined.clear()
    return lb.build()
