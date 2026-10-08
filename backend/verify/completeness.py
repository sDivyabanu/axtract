"""AXTRACT Verify, layer 2: completeness.

Compares an INDEPENDENT source inventory (what the original file contains) with AXTRACT's blocks
(what was extracted) and reports source content that appears not to be represented.

What a result means:
  * an unmatched source object is SUSPICIOUS, not proof that AXTRACT is wrong. Issues say
    "possible_missing_*" and lead to REVIEW_REQUIRED or, later, a deeper second opinion.
  * a matched object means "something in the output represents this". It does NOT mean the output
    is correct: counts and presence are not proof. Completeness is a count-level layer in the rollup
    policy, so it can never make a unit VERIFIED on its own.
  * objects outside AXTRACT's extraction contract (formula text, speaker notes, headers/footers,
    SmartArt ...) are recorded as NOT_EXPECTED and never reported as missing.
  * where no independent reader exists (scanned pages, images) the answer is NOT_VERIFIABLE, never a pass.

Every match records WHY it was made (which signals agreed), and matching is one-to-one.
"""

from __future__ import annotations

import html
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from models.document import BlockType, DocumentBlock, DocumentResponse
from verify.ids import IdSequence
from verify.inventory.models import Contract, SourceInventory, SourceObject, SourceObjectType, SourceUnit
from verify.inventory.xlsx import num_to_col, range_bounds, split_ref
from verify.matching import Pair, assign, containment, iou, recall, similarity
from verify.models import (
    CheckName, CheckOutcome, CheckResult, EvidenceKind, Issue, IssueEvidence, Locator, RecommendedAction,
    Severity, UnitRef, UnitType, bbox_problem,
)
from verify.rollup import UnitChecks

T = BlockType
SO = SourceObjectType
DET, HEUR, NA = EvidenceKind.DETERMINISTIC, EvidenceKind.HEURISTIC, EvidenceKind.UNAVAILABLE

TEXT_RECALL_OK = 0.85  # share of a source text's words that must appear in the matched output
PDF_REGION_OK = 0.80
PDF_PAGE_FALLBACK_OK = 0.90  # a region's text found elsewhere on the same page counts as present
MIN_REGION_CHARS = 15
MIN_FIGURE_AREA = 0.02  # PDF image objects below this page fraction are treated as decorative
MAX_ISSUES_PER_UNIT_CODE = 20
TEXTLIKE = frozenset({T.HEADING, T.PARAGRAPH, T.LIST, T.HEADER, T.FOOTER})
MEDIA = frozenset({T.FIGURE, T.CHART, T.EQUATION})
_REVIEW_OR_WORSE = {Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL}

CODE_CATALOG: dict[str, tuple[Severity, EvidenceKind, str]] = {
    "unit_count_mismatch": (Severity.HIGH, DET, "the source has more pages/slides than the extraction reports"),
    "possible_missing_sheet": (Severity.HIGH, DET, "a populated sheet has no extracted table"),
    "possible_missing_cells": (Severity.MEDIUM, DET, "populated source cells are empty or absent in the extracted table (high when 10% or more)"),
    "possible_missing_merged_range": (Severity.MEDIUM, DET, "a merged range in the source is not reported by the extraction"),
    "possible_missing_slide_content": (Severity.HIGH, DET, "a slide with content produced no blocks at all"),
    "possible_missing_text": (Severity.MEDIUM, DET, "source text is not found in the extracted blocks (heuristic region match on PDFs)"),
    "possible_missing_heading": (Severity.MEDIUM, DET, "a source heading has no matching extracted block"),
    "possible_missing_list": (Severity.MEDIUM, DET, "a source list item has no matching extracted block"),
    "possible_missing_table": (Severity.MEDIUM, DET, "a source table has no matching extracted table (a PDF candidate is only evidence)"),
    "possible_missing_chart": (Severity.MEDIUM, DET, "a source chart has no matching extracted chart/figure"),
    "possible_missing_image": (Severity.MEDIUM, DET, "a source picture has no matching extracted figure"),
    "possible_missing_equation": (Severity.MEDIUM, DET, "a source equation has no matching extracted equation"),
}


class MatchStatus(StrEnum):
    MATCHED = "matched"
    UNMATCHED = "unmatched"  # expected, and nothing represents it
    NOT_EXPECTED = "not_expected"  # outside AXTRACT's extraction contract
    NOT_VERIFIABLE = "not_verifiable"  # no independent evidence to compare against


class MatchRecord(BaseModel):
    object_id: str
    object_type: SourceObjectType
    unit: str
    status: MatchStatus
    block_ids: list[str] = Field(default_factory=list)
    score: float | None = None
    reasons: list[str] = Field(default_factory=list)
    evidence_kind: EvidenceKind
    engine: str


@dataclass
class _Finding:
    unit: UnitRef
    code: str
    severity: Severity
    heuristic: bool
    message: str
    obj: SourceObject | None = None
    block_ids: list[str] = field(default_factory=list)
    source: str | None = None
    extracted: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    bbox: tuple | None = None
    order: int = 0


@dataclass
class CompletenessOutcome:
    units: list[UnitChecks]
    issues: list[Issue]
    matches: list[MatchRecord]
    duration_ms: float
    stats: dict[str, int]
    notes: list[str]


def block_text(b: DocumentBlock) -> str:
    # undo the output sanitiser's reversible HTML escaping (see verify.content.out_text)
    parts = [html.unescape(b.content) if isinstance(b.content, str) else ""]
    rows = b.metadata.get("rows") if isinstance(b.metadata, dict) else None
    if b.type == T.TABLE and isinstance(rows, list):
        parts.append(" ".join(str(c) for r in rows if isinstance(r, list) for c in r if c))
    return " ".join(parts)


def _bbox(b: DocumentBlock):
    bb = getattr(b, "bbox", None)
    try:
        bb = tuple(bb) if bb is not None else None
    except TypeError:
        return None
    return bb if bb is not None and bbox_problem(bb) is None else None


class _Ctx:
    def __init__(self, inv: SourceInventory, resp: DocumentResponse):
        self.inv, self.resp = inv, resp
        self.doc_unit = UnitRef(type=UnitType.DOCUMENT, index=1)
        self.findings: list[_Finding] = []
        self.matches: list[MatchRecord] = []
        self.examined: dict[str, Counter] = defaultdict(Counter)  # unit key -> {"det":n, "heur":n}
        self.by_page: dict[int, list[DocumentBlock]] = defaultdict(list)
        for b in resp.blocks:
            if isinstance(b.page, int):
                self.by_page[b.page].append(b)
        self._n = 0

    def match(self, obj: SourceObject, status: MatchStatus, blocks=(), score=None, reasons=()):
        heur = obj.kind != DET
        self.matches.append(MatchRecord(
            object_id=obj.id, object_type=obj.type, unit=obj.unit.key, status=status, block_ids=[b.id for b in blocks],
            score=round(score, 4) if score is not None else None, reasons=list(reasons), evidence_kind=obj.kind, engine=obj.engine))
        if status in (MatchStatus.MATCHED, MatchStatus.UNMATCHED):
            self.examined[obj.unit.key]["heur" if heur else "det"] += 1

    def find(self, unit: UnitRef, code: str, message: str, *, obj=None, severity=None, heuristic=None, **kw):
        default_sev, kind, _ = CODE_CATALOG[code]
        if heuristic is None:
            heuristic = bool(obj is not None and obj.kind != DET)
        self._n += 1
        self.findings.append(_Finding(unit=unit, code=code, severity=severity or default_sev, heuristic=heuristic,
                                      message=message, obj=obj, order=self._n, **kw))


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------


def _out_cells(block: DocumentBlock) -> set[tuple[int, int]]:
    rows = block.metadata.get("rows") if isinstance(block.metadata, dict) else None
    out = set()
    for r, row in enumerate(rows or []):
        if isinstance(row, list):
            for c, v in enumerate(row):
                if v is not None and str(v).strip():
                    out.add((r + 1, c + 1))
    return out


def _sheet_block(ctx: _Ctx, unit: UnitRef) -> DocumentBlock | None:
    tables = [b for b in ctx.resp.blocks if b.type == T.TABLE]
    named = [b for b in tables if isinstance(b.metadata, dict) and b.metadata.get("sheet_name") == unit.label]
    return (named or [b for b in tables if b.page == unit.index] or [None])[0]


def _refs(coords, limit=10) -> list[str]:
    return [f"{num_to_col(c)}{r}" for r, c in sorted(coords)[:limit]]


def _pair_media(objs: list[SourceObject], blocks: list[DocumentBlock], prefer: set[T]):
    """Positional one-to-one pairing for objects that have no text to compare (charts, pictures)."""
    pairs = []
    for i, o in enumerate(objs):
        for j, b in enumerate(blocks):
            pairs.append(Pair(o.id, b.id, 1.0 - 0.01 * abs(i - j) + (0.05 if b.type in prefer else 0.0),
                              [f"unit-local order {i + 1}->{j + 1}", f"block type {b.type.value}"]))
    return assign(pairs, 0.5)


def _xlsx(ctx: _Ctx) -> None:
    for su in ctx.inv.units:
        u = su.unit
        blk = _sheet_block(ctx, u)
        cells = next((o for o in su.objects if o.type == SO.CELL_REGION), None)
        out = _out_cells(blk) if blk is not None else set()
        src: set[tuple[int, int]] = set()
        if cells is not None:
            src = {rc for ref in cells.metadata["cells"] if (rc := split_ref(ref))}
            if blk is None:
                ctx.find(u, "possible_missing_sheet", f"Sheet '{u.label}' has populated cells but no extracted table.", obj=cells,
                         source=f"{len(src)} populated cells in {cells.locator.cell_range}", extracted="no table block for this sheet",
                         detail={"populated_cells": len(src), "sheet_state": su.properties.get("state")})
                ctx.match(cells, MatchStatus.UNMATCHED, reasons=["no TABLE block carries this sheet's name or number"])
            elif cells.metadata.get("truncated"):
                ctx.match(cells, MatchStatus.NOT_VERIFIABLE, [blk], reasons=["source cell inventory was truncated at the size limit"])
            else:
                missing = src - out
                if missing:
                    ratio = len(missing) / len(src)
                    ctx.find(u, "possible_missing_cells",
                             f"{len(missing)} of {len(src)} populated cells are not present in the extracted table.", obj=cells,
                             severity=Severity.HIGH if ratio >= 0.10 else Severity.MEDIUM, block_ids=[blk.id],
                             source=f"{len(src)} populated cells in {cells.locator.cell_range}",
                             extracted=f"{len(src) - len(missing)} of them present; missing e.g. {', '.join(_refs(missing))}",
                             detail={"missing": len(missing), "populated": len(src), "missing_ratio": round(ratio, 4),
                                     "first_missing": _refs(missing)})
                    ctx.match(cells, MatchStatus.UNMATCHED, [blk], 1 - ratio, [f"{len(missing)} populated cells absent"])
                else:
                    ctx.match(cells, MatchStatus.MATCHED, [blk], 1.0, [f"all {len(src)} populated cells present in the sheet's table",
                                                                       "presence only: values are not compared here"])
        for o in su.objects:
            if o.type == SO.FORMULA:
                ctx.match(o, MatchStatus.NOT_EXPECTED, reasons=[o.contract_note, f"{o.metadata.get('count')} formula cell(s) recorded as evidence"])
            elif o.type == SO.MERGED_RANGE:
                b0 = range_bounds(o.locator.cell_range or "")
                if not b0 or (b0[0] == b0[2] and b0[1] == b0[3]):
                    continue
                if blk is None:
                    continue
                meta = blk.metadata if isinstance(blk.metadata, dict) else {}
                flags = meta.get("flags") or []
                if "merged_cells_not_read_large_file" in flags or "merged_cells" not in meta:
                    ctx.match(o, MatchStatus.NOT_VERIFIABLE, [blk], reasons=["the extraction did not read merged ranges for this sheet"])
                    continue
                want = (b0[0] - 1, b0[1] - 1, b0[2] - b0[0] + 1, b0[3] - b0[1] + 1)
                if want[0] >= len(blk.metadata.get("rows") or []):
                    ctx.match(o, MatchStatus.NOT_EXPECTED, [blk], reasons=["range lies beyond the last populated row, which the extraction trims"])
                    continue
                have = {tuple(r) for r in (meta["merged_cells"] or {}).get("merged_regions", [])}
                if want in have:
                    ctx.match(o, MatchStatus.MATCHED, [blk], 1.0, [f"merged range {o.locator.cell_range} reported by the extraction"])
                else:
                    ctx.find(u, "possible_missing_merged_range", f"Merged range {o.locator.cell_range} is not reported in the output.",
                             obj=o, block_ids=[blk.id], source=o.locator.cell_range,
                             extracted=f"{len(have)} merged region(s) reported", detail={"expected": list(want)})
                    ctx.match(o, MatchStatus.UNMATCHED, [blk], reasons=["range absent from the output's merged regions"])
            elif o.type == SO.TABLE:
                b0 = range_bounds(o.locator.cell_range or "")
                if blk is None or b0 is None or cells is None:
                    ctx.match(o, MatchStatus.UNMATCHED if cells is not None else MatchStatus.NOT_VERIFIABLE, reasons=["the sheet itself is missing"] if blk is None else [])
                    continue
                inside = {rc for rc in src if b0[0] <= rc[0] <= b0[2] and b0[1] <= rc[1] <= b0[3]}
                gone = inside - out
                if gone:
                    ctx.find(u, "possible_missing_table", f"Excel table '{o.text}' ({o.locator.cell_range}): {len(gone)} of {len(inside)} populated cells absent.",
                             obj=o, block_ids=[blk.id], source=f"{o.locator.cell_range}, {len(inside)} populated cells",
                             extracted=f"missing e.g. {', '.join(_refs(gone))}", detail={"missing": len(gone), "populated": len(inside)})
                    ctx.match(o, MatchStatus.UNMATCHED, [blk], 1 - len(gone) / len(inside), [f"{len(gone)} cells of the table range are absent"])
                else:
                    ctx.match(o, MatchStatus.MATCHED, [blk], 1.0, [f"every populated cell of range {o.locator.cell_range} is in the sheet's table"])
        # charts and pictures: the extractor reads neither today, so a workbook that has one is flagged.
        pool = [b for b in ctx.by_page.get(u.index, []) if b.type in (T.CHART, T.FIGURE, T.EQUATION)]
        charts = sorted((o for o in su.objects if o.type == SO.CHART), key=lambda o: o.locator.position or 0)
        pics = sorted((o for o in su.objects if o.type == SO.PICTURE), key=lambda o: o.locator.position or 0)
        got = _pair_media(charts, pool, {T.CHART, T.FIGURE})
        left = [b for b in pool if b.id not in {p.block_id for p in got.values()}]
        got_p = _pair_media(pics, left, {T.FIGURE})
        for objs, matched, code, label in ((charts, got, "possible_missing_chart", "chart"), (pics, got_p, "possible_missing_image", "picture")):
            for o in objs:
                pair = matched.get(o.id)
                if pair:
                    ctx.match(o, MatchStatus.MATCHED, [b for b in pool if b.id == pair.block_id], pair.score, pair.reasons)
                else:
                    title = f" '{o.text}'" if o.text else ""
                    ctx.find(u, code, f"The sheet contains a {label}{title} that has no extracted representation.", obj=o,
                             source=f"{label} anchored at {o.locator.cell_range}", extracted="no chart/figure block for this sheet",
                             detail={k: v for k, v in o.metadata.items() if k in ("chart_types", "series_count", "media")})
                    ctx.match(o, MatchStatus.UNMATCHED, reasons=[f"no unclaimed chart/figure block on sheet {u.index}"])


# ---------------------------------------------------------------------------
# PPTX
# ---------------------------------------------------------------------------


def _table_score(o: SourceObject, b: DocumentBlock, use_bbox: bool) -> tuple[float, list[str]]:
    reasons, score = [], 0.0
    sb, bb = o.locator.bbox, _bbox(b)
    if use_bbox and sb and bb:
        v = iou(sb, bb)
        score += 0.4 * v
        reasons.append(f"bbox_iou={v:.2f}")
    rows = b.metadata.get("rows") if isinstance(b.metadata, dict) else None
    if isinstance(rows, list) and o.metadata.get("row_count") == len(rows) and o.metadata.get("col_count") == max((len(r) for r in rows if isinstance(r, list)), default=0):
        score += 0.2
        reasons.append(f"dimensions {o.metadata.get('row_count')}x{o.metadata.get('col_count')} equal")
    r, _ = recall(o.text, block_text(b))
    score += 0.4 * r
    reasons.append(f"cell_text_recall={r:.2f}")
    return score, reasons


def _pptx(ctx: _Ctx) -> None:
    src_n = ctx.inv.properties.get("slide_count", 0)
    if src_n > ctx.resp.page_count:
        ctx.find(ctx.doc_unit, "unit_count_mismatch", f"The presentation has {src_n} slides; the extraction reports {ctx.resp.page_count}.",
                 heuristic=False, source=f"{src_n} slides", extracted=f"page_count={ctx.resp.page_count}")
    for su in ctx.inv.units:
        u = su.unit
        blocks = ctx.by_page.get(u.index, [])
        exp = [o for o in su.objects if o.contract == Contract.EXPECTED and o.type != SO.GROUP]
        for o in su.objects:
            if o.contract == Contract.NOT_IN_CONTRACT:
                ctx.match(o, MatchStatus.NOT_EXPECTED, reasons=[o.contract_note])
        if exp and not blocks:
            ctx.find(u, "possible_missing_slide_content", f"Slide {u.index} has {len(exp)} content object(s) but produced no blocks.",
                     obj=exp[0], source="; ".join(f"{o.type.value}" + (f" '{(o.text or '')[:30]}'" if o.text else "") for o in exp[:5]),
                     extracted="0 blocks", detail={"objects": len(exp), "hidden": su.properties.get("hidden")})
            for o in exp:
                ctx.match(o, MatchStatus.UNMATCHED, reasons=["the slide produced no blocks"])
            continue
        # --- text shapes: each block is claimed by its single best shape -------------------
        shapes = [o for o in exp if o.type in (SO.TEXT, SO.HEADING)]
        text_blocks = [b for b in blocks if b.type in TEXTLIKE]
        claim: dict[str, tuple[str, float, list[str]]] = {}
        for b in text_blocks:
            best = None
            for o in shapes:
                if o.locator.shape_name and b.metadata.get("shape_name") == o.locator.shape_name:
                    cand = (1.0, ["shape name equal"])
                elif o.locator.bbox and _bbox(b) and containment(_bbox(b), o.locator.bbox) >= 0.8:
                    cand = (0.9, [f"block inside shape bbox ({containment(_bbox(b), o.locator.bbox):.2f})"])
                else:
                    r, _ = recall(block_text(b), o.text)
                    cand = (0.7 * r, [f"block text found in shape ({r:.2f})"]) if r >= 0.9 else (0.0, [])
                if cand[0] > 0 and (best is None or (cand[0], o.id) > (best[1], best[0])):
                    best = (o.id, cand[0], cand[1])
            if best:
                claim[b.id] = best
        for o in shapes:
            mine = [b for b in text_blocks if claim.get(b.id, ("",))[0] == o.id]
            code = "possible_missing_heading" if o.type == SO.HEADING else "possible_missing_text"
            if not mine:
                ctx.find(u, code, f"Text on slide {u.index} ('{(o.text or '')[:40]}') has no matching block.", obj=o,
                         source=(o.text or "")[:200], extracted="no block claims this shape")
                ctx.match(o, MatchStatus.UNMATCHED, reasons=["no extracted text block matches by name, position or text"])
                continue
            r, miss = recall(o.text, " ".join(block_text(b) for b in mine))
            reasons = sorted({x for b in mine for x in claim[b.id][2]}) + [f"text_recall={r:.2f}"]
            if r >= TEXT_RECALL_OK:
                ctx.match(o, MatchStatus.MATCHED, mine, r, reasons)
            else:
                ctx.find(u, "possible_missing_text", f"Only {r:.0%} of the words of a text shape appear in its blocks.", obj=o,
                         severity=Severity.HIGH if r < 0.4 else Severity.MEDIUM, block_ids=[b.id for b in mine],
                         source=(o.text or "")[:200], extracted=" ".join(block_text(b) for b in mine)[:200],
                         detail={"recall": round(r, 4), "missing_words": miss[:20]})
                ctx.match(o, MatchStatus.UNMATCHED, mine, r, reasons)
        # --- tables, pictures, charts -----------------------------------------------------------
        tables = [o for o in exp if o.type == SO.TABLE]
        tbl_blocks = [b for b in blocks if b.type == T.TABLE]
        pairs = []
        for o in tables:
            for b in tbl_blocks:
                s, why = _table_score(o, b, True)
                pairs.append(Pair(o.id, b.id, s, why))
        got = assign(pairs, 0.5)
        for o in tables:
            p = got.get(o.id)
            if p:
                ctx.match(o, MatchStatus.MATCHED, [b for b in tbl_blocks if b.id == p.block_id], p.score, p.reasons)
            else:
                flat, _ = recall(o.text, " ".join(block_text(b) for b in blocks if b.type != T.TABLE))
                ctx.find(u, "possible_missing_table", f"A {o.metadata.get('row_count')}x{o.metadata.get('col_count')} table on slide {u.index} has no matching extracted table"
                         + (" (its text is present as ordinary blocks; see the structure layer)." if flat >= 0.8 else "."),
                         obj=o, severity=Severity.LOW if flat >= 0.8 else None, source=(o.text or "")[:200],
                         extracted=f"{len(tbl_blocks)} table block(s) on the slide, none matches",
                         detail={"text_present_as_other_blocks": flat >= 0.8})
                ctx.match(o, MatchStatus.UNMATCHED, reasons=["no unclaimed table block matches by position, size and cell text"])
        for typ, code, label, allowed in ((SO.CHART, "possible_missing_chart", "chart", {T.CHART, T.FIGURE}),
                                          (SO.PICTURE, "possible_missing_image", "picture", MEDIA)):
            objs = [o for o in exp if o.type == typ]
            cands = [b for b in blocks if b.type in allowed]
            pairs = []
            for o in objs:
                for b in cands:
                    if o.locator.shape_name and b.metadata.get("shape_name") == o.locator.shape_name:
                        pairs.append(Pair(o.id, b.id, 1.0, ["shape name equal"]))
                    elif o.locator.bbox and _bbox(b) and (v := iou(o.locator.bbox, _bbox(b))) >= 0.5:
                        pairs.append(Pair(o.id, b.id, v, [f"bbox_iou={v:.2f}"]))
            got = assign(pairs, 0.5)
            for o in objs:
                p = got.get(o.id)
                if p:
                    ctx.match(o, MatchStatus.MATCHED, [b for b in cands if b.id == p.block_id], p.score, p.reasons)
                else:
                    ctx.find(u, code, f"A {label} ('{o.locator.shape_name}') on slide {u.index} has no matching extracted block.", obj=o,
                             source=f"{label} '{o.locator.shape_name}'", extracted=f"{len(cands)} candidate block(s), none matches",
                             detail={k: v for k, v in o.metadata.items() if k in ("chart_types", "series_count", "alt_text")})
                    ctx.match(o, MatchStatus.UNMATCHED, reasons=["no unclaimed block matches by shape name or position"])


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


def _docx(ctx: _Ctx) -> None:
    su = ctx.inv.units[0]
    u = su.unit
    blocks = list(ctx.resp.blocks)
    for o in su.objects:
        if o.contract == Contract.NOT_IN_CONTRACT:
            ctx.match(o, MatchStatus.NOT_EXPECTED, reasons=[o.contract_note])
    exp = [o for o in su.objects if o.contract == Contract.EXPECTED]
    # text-like objects: exact normalised text first, then fuzzy within a window around the same relative position
    texts = [o for o in exp if o.type in (SO.TEXT, SO.HEADING, SO.LIST_ITEM)]
    cands = [b for b in blocks if b.type in TEXTLIKE]
    from verify.normalize import normalize_text

    def key(s):
        return normalize_text(s or "", case_sensitive=False, canonical_numbers=True)

    exact: dict[str, list[DocumentBlock]] = defaultdict(list)
    for b in cands:
        exact[key(block_text(b))].append(b)
    taken: dict[str, tuple[DocumentBlock, float, list[str]]] = {}
    used: set[str] = set()
    for o in texts:
        pool = [b for b in exact.get(key(o.text), []) if b.id not in used]
        if pool:
            taken[o.id] = (pool[0], 1.0, ["normalised text equal"])
            used.add(pool[0].id)
    rest_o = [o for o in texts if o.id not in taken]
    rest_b = [b for b in cands if b.id not in used]
    pairs = []
    for i, o in enumerate(rest_o):
        centre = i * len(rest_b) / max(len(rest_o), 1)
        for j, b in enumerate(rest_b):
            if abs(j - centre) > 60:
                continue
            s = similarity(o.text, block_text(b))
            r, _ = recall(o.text, block_text(b))
            sc = max(s, 0.95 * r if r >= 0.9 else 0.0)
            if sc >= 0.6:
                pairs.append(Pair(o.id, b.id, sc, [f"text_similarity={s:.2f}", f"text_recall={r:.2f}"]))
    by_id = {b.id: b for b in cands}
    for oid, p in assign(pairs, 0.75).items():
        taken[oid] = (by_id[p.block_id], p.score, p.reasons)
    for o in texts:
        hit = taken.get(o.id)
        if hit:
            b, sc, why = hit
            ok_types = {SO.HEADING: {"heading"}, SO.LIST_ITEM: {"list"}, SO.TEXT: {"paragraph", "header", "footer"}}[o.type]
            note = [] if b.type.value in ok_types else [f"type differs: source {o.type.value}, output {b.type.value}"]
            ctx.match(o, MatchStatus.MATCHED, [b], sc, why + note)
        else:
            code = {SO.HEADING: "possible_missing_heading", SO.LIST_ITEM: "possible_missing_list"}.get(o.type, "possible_missing_text")
            ctx.find(u, code, f"A {o.type.value.replace('_', ' ')} at {o.locator.path} has no matching extracted block.", obj=o,
                     source=(o.text or "")[:200], extracted="no unclaimed text block is similar enough",
                     detail={"path": o.locator.path, "position": o.locator.position})
            ctx.match(o, MatchStatus.UNMATCHED, reasons=["no unclaimed text block is similar enough"])
    # tables
    tables = [o for o in exp if o.type == SO.TABLE]
    tblb = [b for b in blocks if b.type == T.TABLE]
    pairs = []
    for o in tables:
        for b in tblb:
            sc, why = _table_score(o, b, False)
            if recall(o.text, block_text(b))[0] >= 0.6:  # same words => same table, even if rows/columns were lost
                pairs.append(Pair(o.id, b.id, sc, why))
    got = assign(pairs, 0.25)
    for o in tables:
        p = got.get(o.id)
        if p:
            ctx.match(o, MatchStatus.MATCHED, [b for b in tblb if b.id == p.block_id], p.score, p.reasons)
        else:
            flat, _ = recall(o.text, " ".join(block_text(b) for b in blocks if b.type != T.TABLE))
            ctx.find(u, "possible_missing_table", f"A {o.metadata.get('row_count')}x{o.metadata.get('col_count')} table at {o.locator.path} has no matching extracted table"
                     + (" (its text is present as ordinary blocks; see the structure layer)." if flat >= 0.8 else "."),
                     obj=o, severity=Severity.LOW if flat >= 0.8 else None, source=(o.text or "")[:200],
                     extracted=f"{len(tblb)} table block(s), none matches",
                     detail={"path": o.locator.path, "text_present_as_other_blocks": flat >= 0.8})
            ctx.match(o, MatchStatus.UNMATCHED, reasons=["no unclaimed table matches by dimensions and cell text"])
    # pictures / charts / equations: in document order against blocks that came from the same kind of source
    for typ, code, label, sel in (
        (SO.PICTURE, "possible_missing_image", "picture", lambda b: b.type in MEDIA and b.metadata.get("media") == "picture"),
        (SO.CHART, "possible_missing_chart", "chart", lambda b: b.type in (T.CHART, T.FIGURE) and b.metadata.get("media") == "native_chart"),
        (SO.EQUATION, "possible_missing_equation", "equation", lambda b: b.type == T.EQUATION and b.extractor == "omml"),
    ):
        objs = [o for o in exp if o.type == typ]
        pool = [b for b in blocks if sel(b)]
        for i, o in enumerate(objs):
            if i < len(pool):
                ctx.match(o, MatchStatus.MATCHED, [pool[i]], 1.0, [f"{label} #{i + 1} in document order -> block {pool[i].id}"])
            else:
                ctx.find(u, code, f"A {label} at {o.locator.path} has no extracted counterpart ({len(pool)} of {len(objs)} {label}s extracted).",
                         obj=o, source=f"{len(objs)} {label}(s) in the document", extracted=f"{len(pool)} extracted",
                         detail={"path": o.locator.path, "text": (o.text or "")[:80]})
                ctx.match(o, MatchStatus.UNMATCHED, reasons=[f"only {len(pool)} {label} block(s) exist for {len(objs)} source {label}(s)"])


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _pdf(ctx: _Ctx) -> None:
    src_n = ctx.inv.properties.get("page_count", 0)
    if src_n > ctx.resp.page_count:
        ctx.find(ctx.doc_unit, "unit_count_mismatch", f"The PDF has {src_n} pages; the extraction reports {ctx.resp.page_count}.",
                 heuristic=False, source=f"{src_n} pages", extracted=f"page_count={ctx.resp.page_count}")
    merged_pages: dict[int, list[DocumentBlock]] = defaultdict(list)
    for b in ctx.resp.blocks:
        if b.type == T.TABLE and isinstance(b.metadata, dict):
            for pg in b.metadata.get("merged_from_pages") or []:
                if isinstance(pg, int) and pg != b.page:
                    merged_pages[pg].append(b)
    for su in ctx.inv.units:
        u = su.unit
        blocks = ctx.by_page.get(u.index, []) + merged_pages.get(u.index, [])
        page_text = " ".join(block_text(b) for b in blocks)
        rotated = su.properties.get("rotation", 0) != 0
        # --- text ---------------------------------------------------------------------------------
        page_obj = next((o for o in su.objects if o.type == SO.TEXT and o.metadata.get("scope") == "page"), None)
        regions = [o for o in su.objects if o.type == SO.TEXT and o.metadata.get("scope") == "region"]
        if not su.independent_text_available:
            ctx.matches.append(MatchRecord(object_id=f"pdf:page{u.index}:text", object_type=SO.TEXT, unit=u.key,
                                           status=MatchStatus.NOT_VERIFIABLE, evidence_kind=NA, engine="none",
                                           reasons=[su.independence_note]))
        elif page_obj is not None:
            r_page, miss_page = recall(page_obj.text, page_text)
            localized = 0
            if regions and not rotated:
                for reg in regions:
                    if reg.metadata.get("chars", 0) < MIN_REGION_CHARS:
                        continue
                    near = [b for b in blocks if _bbox(b) and reg.locator.bbox and (
                        iou(reg.locator.bbox, _bbox(b)) > 0.05 or containment(reg.locator.bbox, _bbox(b)) >= 0.5
                        or containment(_bbox(b), reg.locator.bbox) >= 0.5)]
                    r_loc, miss = recall(reg.text, " ".join(block_text(b) for b in near))
                    if r_loc >= PDF_REGION_OK:
                        ctx.match(reg, MatchStatus.MATCHED, near, r_loc, [f"text_recall={r_loc:.2f} in {len(near)} overlapping block(s)"])
                        continue
                    r_any, miss_any = recall(reg.text, page_text)
                    if r_any >= PDF_PAGE_FALLBACK_OK:
                        ctx.match(reg, MatchStatus.MATCHED, near, r_any, [f"text found on the page (recall {r_any:.2f}) but not at this position"])
                        continue
                    localized += 1
                    ctx.find(u, "possible_missing_text", f"A text region on page {u.index} is missing from the extraction ({r_any:.0%} of its words found).",
                             obj=reg, severity=Severity.HIGH if r_any < 0.3 else Severity.MEDIUM, block_ids=[b.id for b in near],
                             bbox=reg.locator.bbox, source=reg.text[:200], extracted=" ".join(block_text(b) for b in near)[:200] or "no block here",
                             detail={"recall_on_page": round(r_any, 4), "missing_words": miss_any[:20],
                                     "grouping_kind": "heuristic"})
                    ctx.match(reg, MatchStatus.UNMATCHED, near, r_any, [f"only {r_any:.0%} of the region's words occur on the page"])
            if page_obj is not None:
                ok = r_page >= TEXT_RECALL_OK
                if not ok and not localized:
                    ctx.find(u, "possible_missing_text", f"Only {r_page:.0%} of the page's native text appears in the extraction.",
                             obj=page_obj, severity=Severity.HIGH if r_page < 0.5 else Severity.MEDIUM,
                             source=page_obj.text[:200], extracted=page_text[:200],
                             detail={"recall": round(r_page, 4), "missing_words": miss_page[:20]})
                ctx.match(page_obj, MatchStatus.MATCHED if ok else MatchStatus.UNMATCHED, blocks, r_page,
                          [f"page text recall={r_page:.2f} (pypdfium2 text layer vs extracted blocks)"])
        # --- tables (candidates, not ground truth) -----------------------------------------------------
        tcands = [o for o in su.objects if o.type == SO.TABLE]
        tblocks = [b for b in blocks if b.type == T.TABLE]
        pairs = []
        for o in tcands:
            for b in tblocks:
                boxes = [_bbox(b)] + [pb.get("bbox") for pb in (b.metadata.get("part_bboxes") or []) if isinstance(pb, dict) and pb.get("page") == u.index]
                v = max((max(iou(o.locator.bbox, x), 0.9 * containment(o.locator.bbox, x)) for x in boxes if x), default=0.0)
                r, _ = recall(o.text, block_text(b))
                s = max(v, 0.8 * r if r >= 0.7 else 0.0)
                if s > 0:
                    pairs.append(Pair(o.id, b.id, s, [f"bbox overlap={v:.2f}", f"cell_text_recall={r:.2f}"]))
        got = assign(pairs, 0.3)
        for o in tcands:
            p = got.get(o.id)
            if p:
                ctx.match(o, MatchStatus.MATCHED, [b for b in tblocks if b.id == p.block_id], p.score, p.reasons + ["candidate only: not ground truth"])
            else:
                other = " ".join(block_text(b) for b in blocks if b.type != T.TABLE)
                r_else, _ = recall(o.text, other)
                found_elsewhere = r_else >= 0.8
                ctx.find(u, "possible_missing_table",
                         f"A ruled table candidate on page {u.index} ({o.metadata.get('row_count')}x{o.metadata.get('col_count')}) has no extracted table"
                         + (" (its text is present as ordinary blocks)." if found_elsewhere else "."),
                         obj=o, severity=Severity.LOW if found_elsewhere else Severity.MEDIUM, bbox=o.locator.bbox,
                         source=(o.text or "")[:200], extracted="no table block overlaps this region",
                         detail={"text_present_as_other_blocks": found_elsewhere, "limitation": o.metadata.get("limitation")})
                ctx.match(o, MatchStatus.UNMATCHED, reasons=["no unclaimed table block overlaps the candidate or shares its cell text"])
        # --- images -------------------------------------------------------------------------------
        pics = [o for o in su.objects if o.type == SO.PICTURE and not o.metadata.get("full_page_background")
                and o.metadata.get("area_fraction", 0) >= MIN_FIGURE_AREA]
        for o in su.objects:
            if o.type == SO.PICTURE and o not in pics:
                why = "full-page background (scan layer)" if o.metadata.get("full_page_background") else "below the decorative-size threshold"
                ctx.match(o, MatchStatus.NOT_EXPECTED, reasons=[why])
        figs = [b for b in blocks if b.type in MEDIA]
        pairs = []
        for o in pics:
            for b in figs:
                bb = _bbox(b)
                if bb and o.locator.bbox:
                    v = max(iou(o.locator.bbox, bb), 0.9 * containment(o.locator.bbox, bb))
                    if v >= 0.3:
                        pairs.append(Pair(o.id, b.id, v, [f"bbox overlap={v:.2f}"]))
        got = assign(pairs, 0.3)
        for o in pics:
            p = got.get(o.id)
            if p:
                ctx.match(o, MatchStatus.MATCHED, [b for b in figs if b.id == p.block_id], p.score, p.reasons)
            else:
                ctx.find(u, "possible_missing_image", f"An image covering {o.metadata['area_fraction']:.0%} of page {u.index} has no extracted figure.",
                         obj=o, heuristic=True, severity=Severity.MEDIUM if o.metadata["area_fraction"] >= 0.05 else Severity.LOW,
                         bbox=o.locator.bbox, source=f"image object, {o.metadata['area_fraction']:.1%} of the page",
                         extracted=f"{len(figs)} figure block(s), none overlaps it")
                ctx.match(o, MatchStatus.UNMATCHED, reasons=["no unclaimed figure/chart block overlaps the image"])


def _image(ctx: _Ctx) -> None:
    for su in ctx.inv.units:
        for o in su.objects:
            ctx.matches.append(MatchRecord(
                object_id=o.id, object_type=o.type, unit=su.unit.key, status=MatchStatus.NOT_VERIFIABLE, evidence_kind=NA, engine="none",
                reasons=[su.independence_note, f"{o.metadata.get('width_px')}x{o.metadata.get('height_px')} px {o.metadata.get('format')} recorded"]))


_COMPARATORS = {"xlsx": _xlsx, "pptx": _pptx, "docx": _docx, "pdf": _pdf, "png": _image, "jpg": _image, "jpeg": _image}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def check_completeness(inv: SourceInventory, resp: DocumentResponse, ids: IdSequence | None = None) -> CompletenessOutcome:
    """Compare an independent inventory with an extraction. Pure: changes neither."""
    t0 = time.perf_counter()
    ids = ids or IdSequence("cmp")
    ctx = _Ctx(inv, resp)
    notes: list[str] = []
    if inv.error:
        notes.append(f"source inventory unavailable ({inv.error}); completeness could not be assessed")
    elif (cmp_ := _COMPARATORS.get(inv.file_type)) is None:
        notes.append(f"no completeness comparator for .{inv.file_type}")
    else:
        try:
            cmp_(ctx)
        except Exception as exc:  # noqa: BLE001 - a comparator bug must never lose the extraction
            notes.append(f"completeness comparison failed ({type(exc).__name__}: {exc})")
            ctx.findings.clear()
    unavailable = bool(inv.error) or bool(notes)

    # ---- findings -> issues --------------------------------------------------------------
    order = {su.unit.key: i for i, su in enumerate(inv.units)}
    ctx.findings.sort(key=lambda f: (f.unit.type != UnitType.DOCUMENT, order.get(f.unit.key, 10**6), f.code, f.order))
    issues: list[Issue] = []
    by_unit: dict[str, list[tuple[bool, Issue]]] = defaultdict(list)
    seen: Counter = Counter()
    last: dict[tuple[str, str], Issue] = {}
    for f in ctx.findings:
        k = (f.unit.key, f.code)
        seen[k] += 1
        if seen[k] > MAX_ISSUES_PER_UNIT_CODE:
            iss = last[k]
            iss.evidence[0].detail["additional_not_itemised"] = seen[k] - MAX_ISSUES_PER_UNIT_CODE
            continue
        obj = f.obj
        kind = HEUR if f.heuristic else DET
        engine = obj.engine if obj is not None else "completeness"
        bbox = f.bbox or (obj.locator.bbox if obj is not None else None)
        page = f.unit.index if f.unit.type in (UnitType.PAGE, UnitType.SLIDE, UnitType.SHEET, UnitType.SECTION, UnitType.IMAGE) else None
        issue = Issue(
            id=ids.next(), code=f.code, severity=f.severity, layer=CheckName.COMPLETENESS, message=f.message,
            locator=Locator(unit=f.unit, page=page, bbox=bbox if bbox and bbox_problem(bbox) is None else None, block_ids=f.block_ids[:50]),
            evidence=[IssueEvidence(kind=kind, engine=engine, source=f.source, extracted=f.extracted or "(nothing)",
                                    detail={**f.detail, **({"source_object": obj.id, "locator": obj.locator.model_dump(mode="json", exclude_none=True)} if obj else {})})],
            recommended_action=RecommendedAction.SECONDARY_EXTRACTION,
        )
        last[k] = issue
        issues.append(issue)
        by_unit[f.unit.key].append((f.heuristic, issue))

    # ---- per-unit check results ------------------------------------------------------------
    units: list[UnitChecks] = []
    keys = [su.unit for su in inv.units]
    if any(f.unit.type == UnitType.DOCUMENT for f in ctx.findings) or unavailable or not keys:
        keys = [ctx.doc_unit] + keys
    su_by_key = {su.unit.key: su for su in inv.units}
    for u in keys:
        checks: list[CheckResult] = []
        if unavailable and u.type == UnitType.DOCUMENT:
            checks.append(CheckResult(check=CheckName.COMPLETENESS, outcome=CheckOutcome.NOT_VERIFIABLE, engine="completeness",
                                      summary="; ".join(notes) or "no source inventory"))
        for heur in (False, True):
            n = ctx.examined[u.key]["heur" if heur else "det"]
            mine = [i for h, i in by_unit.get(u.key, []) if h == heur]
            if not n and not mine:
                continue
            blocking = [i for i in mine if i.severity in _REVIEW_OR_WORSE]
            matched = sum(1 for m in ctx.matches if m.unit == u.key and m.status == MatchStatus.MATCHED and (m.evidence_kind != DET) == heur)
            checks.append(CheckResult(
                check=CheckName.COMPLETENESS, outcome=CheckOutcome.FAILED if blocking else CheckOutcome.PASSED,
                evidence_kind=HEUR if heur else DET, engine="completeness.heuristic" if heur else "completeness.deterministic",
                summary=(f"{len(blocking)} source object(s) appear to be missing" if blocking else
                         "expected source content is represented (presence only; says nothing about correctness)"),
                counts={"source_objects_examined": n, "matched": matched, "issues": len(mine)}, issue_ids=[i.id for i in mine]))
        su = su_by_key.get(u.key)
        if su is not None:
            if not su.independent_text_available:
                checks.append(CheckResult(check=CheckName.COMPLETENESS, outcome=CheckOutcome.NOT_VERIFIABLE, engine="completeness.text",
                                          summary=f"text completeness cannot be verified independently: {su.independence_note}"))
            elif not checks:
                checks.append(CheckResult(check=CheckName.COMPLETENESS, outcome=CheckOutcome.PASSED, evidence_kind=DET,
                                          engine="completeness.deterministic", counts={"source_objects_examined": 0},
                                          summary="the source inventory found nothing expected in this unit"))
        units.append(UnitChecks(unit=u, checks=checks))

    counted = Counter(m.status.value for m in ctx.matches)
    return CompletenessOutcome(
        units=units, issues=issues, matches=ctx.matches, duration_ms=round((time.perf_counter() - t0) * 1000, 3),
        stats={"issues": len(issues), "source_objects": len(ctx.matches), **{st.value: counted[st.value] for st in MatchStatus}},
        notes=notes)
