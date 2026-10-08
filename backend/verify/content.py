"""AXTRACT Verify, layer 3: content correctness.

Compares what was extracted with INDEPENDENT source evidence and reports differences in the words
and numbers themselves: a digit changed, a word substituted or dropped, text added, a cell value
different. It builds on the completeness matches (which object corresponds to which block).

Evidence by format
  XLSX  stored cell values from the raw XML, compared coordinate by coordinate (numbers numerically,
        text exactly, booleans / errors / dates by their stored meaning)
  PPTX  text of each shape and each table cell, from the raw slide XML
  DOCX  text of each paragraph and each table cell, from the raw document XML
  PDF   the page's native text layer (pypdfium2), compared as a multiset of words and numbers
  OCR   NEVER independent evidence. Text produced by OCR (scanned pages, images, OCR'd regions) is
        reported NOT_VERIFIABLE; the OCR engine's own output is not used to vouch for itself.

What a pass means: every word and number in the examined text appears in the output, in the output
and nowhere else, after formatting-only differences (case, spacing, ligatures, hyphenation, thousands
separators, currency position) are ignored. "$18.2M" and "$13.2M" are different.

What it does NOT cover (recorded as an INFO issue, never silently skipped): chart data values,
equation conversions, text inside pictures, formula text, and anything produced by OCR.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from models.document import BlockType as T, DocumentBlock, DocumentResponse
from verify.completeness import MatchRecord, MatchStatus, _bbox
from verify.ids import IdSequence
from verify.inventory.models import Contract, SourceInventory, SourceObject, SourceObjectType as SO
from verify.inventory.xlsx import num_to_col, split_ref
from verify.layer import LayerBuilder, LayerOutcome
from verify.matching import containment, iou
from verify.models import CheckName, CheckOutcome, EvidenceKind, Severity, UnitRef, UnitType
from verify.normalize import normalize_text
from verify.tables import diff_tables, moved, ref
from verify.textdiff import TextDiff, content_tokens, diff_multiset, diff_sequence, has_digit

DET, HEUR, NA = EvidenceKind.DETERMINISTIC, EvidenceKind.HEURISTIC, EvidenceKind.UNAVAILABLE
LOST_FRACTION_HIGH = 0.30  # losing this share of a text's words is serious
EXTRA_FRACTION_MEDIUM = 0.10
MAX_LISTED = 8

CODE_CATALOG: dict[str, tuple[Severity, str]] = {
    "numeric_value_mismatch": (Severity.HIGH, "a number in the output differs from the source (a digit changed, lost or added)"),
    "content_mismatch": (Severity.MEDIUM, "words in the output differ from the source (substitution; typical of OCR-style errors)"),
    "content_words_missing": (Severity.MEDIUM, "words present in the source are absent from the output"),
    "unexpected_words": (Severity.LOW, "words in the output do not exist in the source (medium when they are a large share)"),
    "cell_value_mismatch": (Severity.MEDIUM, "a table or spreadsheet cell holds a different value than the source (high when numeric)"),
    "formula_result_mismatch": (Severity.HIGH, "the stored result of a formula cell differs from the output"),
    "unexpected_cell_value": (Severity.MEDIUM, "the output has a value in a cell that is empty in the source"),
    "content_not_compared": (Severity.INFO, "content that was NOT compared: chart values, equations, OCR text, formula text"),
}

_MATH = re.compile(r"\$[^$]*\$")
_OCR_ROUTES = {"scanned", "mixed-ocr"}


def strip_math(text: str) -> str:
    """Inline equations are rendered into paragraph text as $latex$; they are not source words."""
    return _MATH.sub(" ", text or "")


def is_ocr_block(b: DocumentBlock) -> bool:
    meta = b.metadata if isinstance(b.metadata, dict) else {}
    return b.extractor in ("rapidocr", "ocr") or meta.get("route") in _OCR_ROUTES or bool(meta.get("ocr_engine"))


def out_text(b: DocumentBlock) -> str:
    """The block's text exactly once (a table's content and its cells are the same words).

    AXTRACT's output sanitiser HTML-escapes block text (an apostrophe becomes &#x27;, & becomes &amp;).
    That is a reversible encoding for safe display, not a change of content, so it is undone here;
    otherwise every apostrophe would look like an invented word. Anything the sanitiser REMOVES (script
    tags, event handlers) is genuinely absent from the output and is still reported as missing.
    """
    rows = b.metadata.get("rows") if isinstance(b.metadata, dict) else None
    if b.type == T.TABLE and isinstance(rows, list):
        return " ".join(str(c) for r in rows if isinstance(r, list) for c in r if c)
    return html.unescape(b.content) if isinstance(b.content, str) else ""


def _clip(s: str, n: int = 200) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _report_text_diff(lb: LayerBuilder, unit: UnitRef, diff: TextDiff, *, what: str, kind: EvidenceKind, engine: str,
                      block_ids: list[str], bbox=None, base: dict[str, Any] | None = None) -> None:
    if diff.equal:
        return
    base = base or {}
    numeric = [c for c in diff.changes if c.numeric]
    changed = [c for c in diff.changes if c.kind == "changed" and not c.numeric]
    missing = [c for c in diff.changes if c.kind == "missing" and not c.numeric]
    extra = [c for c in diff.changes if c.kind == "extra" and not c.numeric]
    lost_frac = diff.lost_tokens / max(diff.src_tokens, 1)

    def emit(code, msg, changes, severity=None):
        lb.find(unit, code, msg, kind=kind, engine=engine, severity=severity, block_ids=block_ids, bbox=bbox,
                source=_clip("; ".join(" ".join(c.src) for c in changes[:MAX_LISTED] if c.src) or "(nothing)"),
                extracted=_clip("; ".join(c.describe() for c in changes[:MAX_LISTED])),
                detail={**base, "changes": [{"kind": c.kind, "source": " ".join(c.src), "extracted": " ".join(c.out)}
                                            for c in changes[:20]], "count": len(changes)})

    if numeric:
        emit("numeric_value_mismatch", f"{len(numeric)} number(s) in {what} differ from the source.", numeric)
    if changed:
        emit("content_mismatch", f"{len(changed)} word(s) in {what} differ from the source.", changed,
             Severity.HIGH if lost_frac >= LOST_FRACTION_HIGH else None)
    if missing:
        emit("content_words_missing", f"{sum(len(c.src) for c in missing)} word(s) of {what} are missing from the output.", missing,
             Severity.HIGH if lost_frac >= LOST_FRACTION_HIGH else None)
    if extra:
        n = sum(len(c.out) for c in extra)
        emit("unexpected_words", f"{n} word(s) in the output of {what} do not exist in the source.", extra,
             Severity.MEDIUM if n / max(diff.out_tokens, 1) >= EXTRA_FRACTION_MEDIUM else None)


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}:\d{2}(\.\d+)?)?$")
_TIME = re.compile(r"^\d{2}:\d{2}:\d{2}$")


def _serial_to_iso(raw: str, date1904: bool) -> str | None:
    try:
        v = float(raw)
    except ValueError:
        return None
    base = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
    return (base + timedelta(days=v)).replace(microsecond=0).isoformat(sep=" ")


def _src_canon(raw: str, ctype: str) -> str:
    """Canonical text of a source cell, by what the cell IS (a number, text, boolean, error or date)."""
    if ctype == "n":
        try:
            return format(Decimal(raw).normalize(), "f")
        except InvalidOperation:
            return normalize_text(raw)
    if ctype == "b":
        return "true" if raw in ("1", "true", "TRUE") else "false"
    if ctype == "d":
        return raw.replace("T", " ")[:19]
    return normalize_text(raw)  # text and errors compare as text


def _time_matches(raw: str, hhmmss: str) -> bool:
    try:
        h, m, s = (int(x) for x in hhmmss.split(":"))
        return abs((float(raw) % 1) - (h * 3600 + m * 60 + s) / 86400) < 1e-4
    except ValueError:
        return False


def _out_canon(out: Any, ctype: str | None, raw: str | None, date1904: bool) -> str:
    """Read an output cell the way the SOURCE cell says it should be read, so equal meanings compare equal."""
    if out is None or str(out).strip() == "":
        return ""
    s = str(out)
    if ctype == "n" and raw is not None:
        try:
            return format(Decimal(s).normalize(), "f")
        except InvalidOperation:
            pass
        s2 = s.replace("T", " ")
        if _ISO.match(s2):  # a numeric cell the extractor delivered as a date
            iso = _serial_to_iso(raw, date1904)
            if iso and (s2 if len(s2) > 10 else s2 + " 00:00:00")[:19] == iso[:19]:
                return _src_canon(raw, "n")
        elif _TIME.match(s2) and _time_matches(raw, s2):
            return _src_canon(raw, "n")
        return normalize_text(s)
    if ctype == "b":
        return {"true": "true", "false": "false", "1": "true", "0": "false"}.get(s.strip().lower(), s.strip().lower())
    if ctype == "d":
        return s.replace("T", " ")[:19]
    return normalize_text(s)


def _loose_out(v: Any) -> str:
    """Type-agnostic reading of an output cell, used only to recognise that rows/columns were MOVED."""
    s = str(v).strip()
    try:
        return format(Decimal(s).normalize(), "f")
    except InvalidOperation:
        return {"true": "true", "false": "false"}.get(s.lower(), normalize_text(s))


def xlsx_grids(cells_obj: SourceObject, out_rows: list, date1904: bool, loose: bool = False):
    """Canonical (source, output) grids of one sheet, comparable cell by cell.

    The output is read the way the SOURCE cell says it should be: a numeric source cell compares
    numerically, a text cell as text, so "007" and "7" stay different and "20.50" equals 20.5.
    """
    src, types = cells_obj.metadata["cells"], cells_obj.metadata.get("cell_types", {})
    sg: dict[tuple[int, int], str] = {}
    og: dict[tuple[int, int], str] = {}
    for r_ref, raw in src.items():
        rc = split_ref(r_ref)
        if rc:
            ctype = types.get(r_ref, "n")
            sg[(rc[0] - 1, rc[1] - 1)] = _loose_out(raw) if loose and ctype != "b" else _src_canon(raw, ctype)
    for r, row in enumerate(out_rows):
        for c, v in enumerate(row if isinstance(row, list) else []):
            if v is None or str(v).strip() == "":
                continue
            r_ref = f"{num_to_col(c + 1)}{r + 1}"
            og[(r, c)] = _loose_out(v) if loose else _out_canon(v, types.get(r_ref, "n") if r_ref in src else None, src.get(r_ref), date1904)
    n_r = max([k[0] for k in list(sg) + list(og)] + [-1]) + 1
    n_c = max([k[1] for k in list(sg) + list(og)] + [-1]) + 1
    a = [[sg.get((r, c), "") for c in range(n_c)] for r in range(n_r)]
    b = [[og.get((r, c), "") for c in range(n_c)] for r in range(n_r)]
    return a, b, sg


def _xlsx(lb: LayerBuilder, inv: SourceInventory, resp: DocumentResponse, matches: list[MatchRecord]) -> None:
    blocks = {b.id: b for b in resp.blocks}
    by_obj = {m.object_id: m for m in matches}
    date1904 = bool(inv.properties.get("date1904"))
    for su in inv.units:
        u = lb.unit(su.unit)
        cells_obj = next((o for o in su.objects if o.type == SO.CELL_REGION), None)
        out_blocks = [b for b in resp.blocks if b.type == T.TABLE and (b.metadata.get("sheet_name") == u.label or b.page == u.index)]
        if cells_obj is None:
            if not out_blocks:
                lb.examine(u, DET, vacuous=1)  # the source has no cells and the output has none
            continue
        m = by_obj.get(cells_obj.id)
        if m is None or m.status == MatchStatus.UNMATCHED or not m.block_ids:
            continue  # the sheet is missing: completeness reports it
        if cells_obj.metadata.get("truncated"):
            lb.note(u, CheckOutcome.NOT_VERIFIABLE, "the source cell inventory was truncated at its size limit", "content.xlsx")
            continue
        blk = blocks[m.block_ids[0]]
        src = cells_obj.metadata["cells"]
        formulas = set(cells_obj.metadata.get("formula_cells", []))
        a, b, sg = xlsx_grids(cells_obj, blk.metadata.get("rows") or [], date1904)
        d = diff_tables(a, b)
        lb.examine(u, DET, cells=len(sg))
        if d.kind in ("identical", "rows_reordered", "columns_reordered"):
            continue  # arrangement problems belong to the structure layer
        la, lbg, _ = xlsx_grids(cells_obj, blk.metadata.get("rows") or [], date1904, loose=True)
        if moved(a, b, la, lbg):
            continue  # the same values, moved: a structure finding, not a value change
        mism, unexp, formula_bad = [], [], []
        for cd in d.cell_diffs:
            r_ref = ref(cd.row, cd.col)
            if not cd.src:
                unexp.append(cd)
            elif not cd.out:
                continue  # an absent value is a completeness finding
            elif r_ref in formulas:
                formula_bad.append(cd)
            else:
                mism.append(cd)
        engine = cells_obj.engine

        def listing(items):
            return [{"cell": ref(c.row, c.col), "source": c.src, "extracted": c.out,
                     "is_formula": ref(c.row, c.col) in formulas} for c in items[:20]]

        if mism:
            numeric = any(c.numeric for c in mism)
            ratio = len(mism) / max(len(sg), 1)
            lb.find(u, "cell_value_mismatch", f"{len(mism)} of {len(sg)} cell value(s) differ from the source.",
                    kind=DET, engine=engine, severity=Severity.HIGH if numeric or ratio >= 0.10 else Severity.MEDIUM,
                    block_ids=[blk.id], source=_clip("; ".join(f"{ref(c.row, c.col)}={c.src!r}" for c in mism[:MAX_LISTED])),
                    extracted=_clip("; ".join(f"{ref(c.row, c.col)}={c.out!r}" for c in mism[:MAX_LISTED])),
                    detail={"cells": listing(mism), "count": len(mism), "compared": len(sg), "any_numeric": numeric})
        if formula_bad:
            lb.find(u, "formula_result_mismatch", f"{len(formula_bad)} formula cell(s) hold a different result than the one stored in the file.",
                    kind=DET, engine=engine, block_ids=[blk.id],
                    source=_clip("; ".join(f"{ref(c.row, c.col)}={c.src!r}" for c in formula_bad[:MAX_LISTED])),
                    extracted=_clip("; ".join(f"{ref(c.row, c.col)}={c.out!r}" for c in formula_bad[:MAX_LISTED])),
                    detail={"cells": listing(formula_bad), "count": len(formula_bad),
                            "note": "formula text is not extracted; the stored result is compared"})
        if unexp:
            lb.find(u, "unexpected_cell_value", f"{len(unexp)} cell(s) hold a value where the source cell is empty.",
                    kind=DET, engine=engine, block_ids=[blk.id], source="(empty cells)",
                    extracted=_clip("; ".join(f"{ref(c.row, c.col)}={c.out!r}" for c in unexp[:MAX_LISTED])),
                    detail={"cells": listing(unexp), "count": len(unexp)})
        uncached = cells_obj.metadata and su.properties.get("formulas_without_cached_value", 0)
        if uncached:
            lb.find(u, "content_not_compared", f"{uncached} formula cell(s) have no stored result, so their values cannot be compared.",
                    kind=DET, engine=engine, detail={"formulas_without_cached_value": uncached})


# ---------------------------------------------------------------------------
# PPTX / DOCX
# ---------------------------------------------------------------------------


def _office(lb: LayerBuilder, inv: SourceInventory, resp: DocumentResponse, matches: list[MatchRecord]) -> None:
    blocks = {b.id: b for b in resp.blocks}
    objs = {o.id: o for o in inv.objects()}
    for su in inv.units:
        u = lb.unit(su.unit)
        expected = [o for o in su.objects if o.contract == Contract.EXPECTED and o.type != SO.GROUP]
        if not expected and not [b for b in resp.blocks if b.page == u.index or inv.file_type == "docx"]:
            lb.examine(u, DET, vacuous=1)
        not_compared = Counter(o.type.value for o in expected if o.type in (SO.CHART, SO.EQUATION))
        if not_compared:
            lb.find(u, "content_not_compared", "Chart values and equation conversions are not compared with independent evidence.",
                    kind=DET, engine="content.office", detail=dict(not_compared))
    for m in matches:
        o = objs.get(m.object_id)
        if o is None or m.status != MatchStatus.MATCHED or o.contract != Contract.EXPECTED or not m.block_ids:
            continue
        u = lb.unit(o.unit)
        got = [blocks[i] for i in m.block_ids if i in blocks]
        if o.type in (SO.TEXT, SO.HEADING, SO.LIST_ITEM):
            text = " ".join(strip_math(out_text(b)) for b in got)
            d = diff_sequence(content_tokens(o.text), content_tokens(text))
            lb.examine(u, DET, objects=1)
            _report_text_diff(lb, u, d, what=f"{o.type.value.replace('_', ' ')} at {o.locator.path or o.locator.shape_name}",
                              kind=DET, engine=o.engine, block_ids=[b.id for b in got], bbox=_bbox(got[0]) if got else None,
                              base={"source_object": o.id, "path": o.locator.path, "shape": o.locator.shape_name})
        elif o.type == SO.TABLE:
            b = got[0]
            d = diff_tables(o.metadata.get("rows"), b.metadata.get("rows"))
            lb.examine(u, DET, objects=1, cells=d.compared_cells)
            if d.kind in ("identical", "rows_reordered", "columns_reordered") or not d.cell_diffs:
                continue
            numeric = any(c.numeric for c in d.cell_diffs)
            lb.find(u, "cell_value_mismatch", f"{len(d.cell_diffs)} table cell(s) differ from the source.", kind=DET, engine=o.engine,
                    severity=Severity.HIGH if numeric else Severity.MEDIUM, block_ids=[b.id], bbox=_bbox(b),
                    source=_clip("; ".join(f"r{c.row + 1}c{c.col + 1}={c.src!r}" for c in d.cell_diffs[:MAX_LISTED])),
                    extracted=_clip("; ".join(f"r{c.row + 1}c{c.col + 1}={c.out!r}" for c in d.cell_diffs[:MAX_LISTED])),
                    detail={"cells": [{"row": c.row + 1, "col": c.col + 1, "source": c.src, "extracted": c.out}
                                      for c in d.cell_diffs[:20]], "count": len(d.cell_diffs), "any_numeric": numeric,
                            "source_object": o.id})


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _page_groups(resp: DocumentResponse, pages: list[int]) -> list[list[int]]:
    """Pages joined by a cross-page merged table form one comparison group: its rows belong to no single page."""
    parent = {p: p for p in pages}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for b in resp.blocks:
        if b.type == T.TABLE and isinstance(b.metadata, dict) and b.metadata.get("merged_from_pages"):
            joined = [p for p in [b.page] + [x for x in b.metadata["merged_from_pages"] if isinstance(x, int)] if p in parent]
            for p in joined[1:]:
                parent[find(p)] = find(joined[0])
    groups: dict[int, list[int]] = {}
    for p in pages:
        groups.setdefault(find(p), []).append(p)
    return sorted((sorted(g) for g in groups.values()), key=lambda g: g[0])


def _pdf(lb: LayerBuilder, inv: SourceInventory, resp: DocumentResponse, matches: list[MatchRecord]) -> None:
    by_page: dict[int, list[DocumentBlock]] = {}
    for b in resp.blocks:
        by_page.setdefault(b.page, []).append(b)
    units = {su.unit.index: su for su in inv.units}
    for group in _page_groups(resp, sorted(units)):
        first = units[group[0]].unit
        lb.unit(first)
        sus = [units[p] for p in group]
        for su in sus:
            lb.unit(su.unit)
        blocked = [su for su in sus if not su.independent_text_available]
        if blocked:
            for su in sus:
                lb.note(su.unit, CheckOutcome.NOT_VERIFIABLE,
                        f"text cannot be verified: {(blocked[0].independence_note)}", "content.text")
            continue
        blocks = [b for p in group for b in by_page.get(p, [])]
        media = [b for b in blocks if b.type in (T.EQUATION, T.CHART)]
        if any(su.properties.get("rotation", 0) != 0 for su in sus) and media:
            for su in sus:
                lb.note(su.unit, CheckOutcome.NOT_VERIFIABLE,
                        "rotated page with equations/charts: their text cannot be separated from the page text", "content.text")
            continue
        textual = [b for b in blocks if b.type not in (T.EQUATION, T.CHART, T.FIGURE)]
        ocr = [b for b in blocks if is_ocr_block(b)]
        page_objs = [o for su in sus for o in su.objects if o.type == SO.TEXT and o.metadata.get("scope") == "page"]
        regions = [o for su in sus for o in su.objects if o.type == SO.TEXT and o.metadata.get("scope") == "region"]
        if not page_objs:
            if not textual and not media:
                for su in sus:
                    lb.examine(su.unit, DET, vacuous=1)  # no native text anywhere and nothing extracted
            elif textual and not all(is_ocr_block(b) for b in textual):
                toks = [t for b in textual if not is_ocr_block(b) for t in content_tokens(out_text(b))]
                for su in sus:
                    lb.examine(su.unit, DET, objects=1)
                _report_text_diff(lb, first, diff_multiset([], toks), what=f"page {first.index}", kind=DET, engine="pypdfium2",
                                  block_ids=[b.id for b in textual][:50])
            continue
        src_c: Counter = Counter()
        for o in page_objs:
            src_c += Counter(content_tokens(o.text))
        kind, excluded = DET, 0
        if media and regions:
            for reg in regions:
                if reg.locator.bbox and any(_bbox(b) and (containment(reg.locator.bbox, _bbox(b)) >= 0.5 or iou(reg.locator.bbox, _bbox(b)) >= 0.3)
                                            for b in media):
                    src_c = src_c - Counter(content_tokens(reg.text))
                    excluded += 1
            kind = HEUR  # which words belong to an equation/chart is decided by position
        out_tokens = [t for b in textual for t in content_tokens(out_text(b))]
        ocr_c = Counter(t for b in ocr for t in content_tokens(out_text(b)))
        # AXTRACT drops the repeated header row of a table continued on another page, on purpose
        dropped_headers: Counter = Counter()
        for b in blocks:
            meta = b.metadata if isinstance(b.metadata, dict) else {}
            extra_pages = [p for p in (meta.get("merged_from_pages") or [])[1:] if p in group]
            if b.type == T.TABLE and extra_pages and isinstance(meta.get("rows"), list):
                n_head = (meta.get("multi_row_header") or {}).get("header_row_count", 1)
                head = Counter(t for row in meta["rows"][:n_head] if isinstance(row, list) for c in row if c for t in content_tokens(str(c)))
                for _ in extra_pages:
                    dropped_headers += head
        diff = diff_multiset(list(src_c.elements()), out_tokens, ignore_extra=ocr_c, ignore_missing=dropped_headers)
        for su in sus:
            lb.examine(su.unit, kind, objects=1, words=sum(src_c.values()) if su is sus[0] else 0)
        bbox = None
        if not diff.equal:
            head = next((c for c in diff.changes if c.src), None)
            if head:
                reg = next((r for r in regions if set(head.src) & set(content_tokens(r.text))), None)
                bbox = reg.locator.bbox if reg else None
            else:
                tok = diff.changes[0].out[0]
                blk = next((b for b in textual if tok in content_tokens(out_text(b))), None)
                bbox = _bbox(blk) if blk else None
        _report_text_diff(lb, first, diff, what=f"page {first.index}" + (f" (pages {group[0]}-{group[-1]}, joined by a merged table)" if len(group) > 1 else ""),
                          kind=kind, engine="pypdfium2", block_ids=[b.id for b in textual][:50], bbox=bbox,
                          base={"excluded_regions": excluded, "comparison": "word multiset (order ignored)", "pages": group})
        skipped = {k: v for k, v in (("ocr_blocks", len(ocr)), ("equation_or_chart_blocks", len(media))) if v}
        if skipped:
            lb.find(first, "content_not_compared", "OCR text, equations and chart values on this page are not compared with independent evidence.",
                    kind=DET, engine="content.pdf", detail=skipped)


def _image(lb: LayerBuilder, inv: SourceInventory, resp: DocumentResponse, matches: list[MatchRecord]) -> None:
    for su in inv.units:
        lb.note(su.unit, CheckOutcome.NOT_VERIFIABLE, f"OCR text cannot be verified: {su.independence_note}", "content.text")


_COMPARATORS = {"xlsx": _xlsx, "pptx": _office, "docx": _office, "pdf": _pdf, "png": _image, "jpg": _image, "jpeg": _image}


def check_content(inv: SourceInventory, resp: DocumentResponse, matches: list[MatchRecord], ids: IdSequence | None = None) -> LayerOutcome:
    """Compare extracted content with independent source evidence. Pure: changes neither input."""
    lb = LayerBuilder(CheckName.CONTENT, CODE_CATALOG, ids)
    if inv.error:
        lb.notes.append(f"source inventory unavailable ({inv.error}); content could not be compared")
        lb.note(UnitRef(type=UnitType.DOCUMENT, index=1), CheckOutcome.NOT_VERIFIABLE, lb.notes[-1], "content")
    elif (cmp_ := _COMPARATORS.get(inv.file_type)) is None:
        lb.notes.append(f"no content comparator for .{inv.file_type}")
    else:
        try:
            cmp_(lb, inv, resp, matches)
        except Exception as exc:  # noqa: BLE001 - a comparator bug must never lose the extraction
            lb.notes.append(f"content comparison failed ({type(exc).__name__}: {exc})")
            lb._findings.clear()
            lb._examined.clear()
    return lb.build()
