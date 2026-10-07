"""AXTRACT Verify, layer 6: extraction integrity.

Detects STRUCTURAL and OPERATIONAL problems in an extraction: duplicate ids, impossible
coordinates, bad page references, missing provenance, extractor errors, inconsistent reading
order, malformed table metadata, and so on.

What this layer does NOT do: it never says the extracted CONTENT is correct. A clean integrity
result only means the output is well-formed and usable by downstream consumers (viewer,
markdown, history). Its check results are therefore count-level in the rollup policy and cannot
make a unit VERIFIED on their own.

Evidence classes
  deterministic  rules that read facts straight off the output (an id repeats, a bbox is outside
                 0..1, a table's row_count disagrees with its rows, an extractor reported an error)
  heuristic      rules that infer a likely problem (overlapping identical blocks, a page with no
                 blocks). Their issues are labelled heuristic and are never presented as proof.

Issues are aggregated per (unit, code) so a badly broken extraction cannot flood a report;
each issue lists up to MAX_BLOCK_IDS blocks and a few examples.
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from models.document import BlockType, DocumentBlock, DocumentResponse
from verify.ids import IdSequence
from verify.models import (
    CheckName,
    CheckOutcome,
    CheckResult,
    EvidenceKind,
    Issue,
    IssueEvidence,
    Locator,
    PreviewLocator,
    RecommendedAction,
    Severity,
    UnitRef,
    UnitType,
    bbox_problem,
)
from verify.normalize import normalize_text
from verify.rollup import UnitChecks

GEOMETRY_FORMATS = frozenset({"pdf", "pptx", "jpg", "jpeg", "png"})  # formats whose blocks carry a bbox
UNIT_TYPE_BY_FORMAT = {
    "pdf": UnitType.PAGE,
    "pptx": UnitType.SLIDE,
    "xlsx": UnitType.SHEET,
    "docx": UnitType.SECTION,
    "jpg": UnitType.IMAGE,
    "jpeg": UnitType.IMAGE,
    "png": UnitType.IMAGE,
}
TEXT_BLOCKS = frozenset({BlockType.HEADING, BlockType.PARAGRAPH, BlockType.LIST, BlockType.HEADER, BlockType.FOOTER})
MAX_BLOCK_IDS = 50
MAX_EXAMPLES = 5
MAX_ERROR_ISSUES = 50
MAX_ENUMERATED_UNITS = 2000
MIN_DUPLICATE_CHARS = 12
DUPLICATE_IOU = 0.8
_REVIEW_OR_WORSE = {Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL}
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Extractor error codes that mean part of the document was lost (the rest are "could not read one object").
_UNIT_LOSS = re.compile(r"(ROUTING_FAILED|EXTRACTION_FAILED|RENDER_FAILED|OCR_.*FAILED)$")

# code -> (default severity, evidence class, what it means). The catalogue doubles as documentation.
CODE_CATALOG: dict[str, tuple[Severity, EvidenceKind, str]] = {
    "empty_extraction": (Severity.HIGH, EvidenceKind.DETERMINISTIC, "the extractor returned no blocks (critical if it also reported errors)"),
    "invalid_page_count": (Severity.HIGH, EvidenceKind.DETERMINISTIC, "page_count is not a positive integer"),
    "extractor_error": (Severity.HIGH, EvidenceKind.DETERMINISTIC, "the extractor recorded an error (medium when only one object was affected)"),
    "partial_status_without_errors": (Severity.MEDIUM, EvidenceKind.DETERMINISTIC, "status says partial but no error explains it"),
    "errors_with_success_status": (Severity.MEDIUM, EvidenceKind.DETERMINISTIC, "status says success but errors were recorded"),
    "extractor_flagged_blocks": (Severity.MEDIUM, EvidenceKind.DETERMINISTIC, "the extractor itself marked blocks as incomplete or needing review"),
    "duplicate_block_id": (Severity.HIGH, EvidenceKind.DETERMINISTIC, "two blocks share an id, so selection and provenance are ambiguous"),
    "missing_required_field": (Severity.HIGH, EvidenceKind.DETERMINISTIC, "a block has no id (high) or no extractor name (medium)"),
    "invalid_page_reference": (Severity.HIGH, EvidenceKind.DETERMINISTIC, "block.page is below 1 or beyond page_count"),
    "unit_reference_mismatch": (Severity.HIGH, EvidenceKind.DETERMINISTIC, "a PPTX block's slide_number disagrees with its page"),
    "invalid_bbox": (Severity.HIGH, EvidenceKind.DETERMINISTIC, "bbox is malformed, non-finite, outside 0..1 or inverted"),
    "degenerate_bbox": (Severity.LOW, EvidenceKind.DETERMINISTIC, "bbox has zero width or height"),
    "missing_required_provenance": (Severity.MEDIUM, EvidenceKind.DETERMINISTIC, "a block in a format with coordinates has no bbox"),
    "invalid_confidence": (Severity.MEDIUM, EvidenceKind.DETERMINISTIC, "confidence is outside 0..1 or not finite"),
    "empty_block_content": (Severity.MEDIUM, EvidenceKind.DETERMINISTIC, "a block type that must carry content is empty"),
    "invalid_block_content": (Severity.MEDIUM, EvidenceKind.DETERMINISTIC, "content has control characters, replacement characters or is not text"),
    "malformed_metadata": (Severity.MEDIUM, EvidenceKind.DETERMINISTIC, "metadata required by the block type is missing or inconsistent"),
    "malformed_preview_locator": (Severity.MEDIUM, EvidenceKind.DETERMINISTIC, "metadata.preview cannot be drawn by the viewer"),
    "reading_order_missing": (Severity.MEDIUM, EvidenceKind.DETERMINISTIC, "some or all blocks have no reading_order"),
    "invalid_reading_order": (Severity.MEDIUM, EvidenceKind.DETERMINISTIC, "reading_order is not a non-negative integer"),
    "duplicate_reading_order": (Severity.MEDIUM, EvidenceKind.DETERMINISTIC, "two blocks share a reading_order value"),
    "reading_order_not_monotonic": (Severity.MEDIUM, EvidenceKind.DETERMINISTIC, "the block list is not in reading_order sequence"),
    "duplicate_block": (Severity.MEDIUM, EvidenceKind.HEURISTIC, "identical content at (nearly) the same place: likely extracted twice"),
    "repeated_adjacent_block": (Severity.LOW, EvidenceKind.HEURISTIC, "identical neighbouring blocks in a format without coordinates (may be genuine repetition)"),
    "unit_without_blocks": (Severity.LOW, EvidenceKind.HEURISTIC, "a page/slide/sheet produced no blocks (may be genuinely blank)"),
}


@dataclass
class _Finding:
    unit: UnitRef
    code: str
    severity: Severity
    heuristic: bool
    message: str
    count: int = 0
    block_ids: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    page: int | None = None
    bbox: tuple[float, ...] | None = None
    preview: PreviewLocator | None = None
    action: RecommendedAction = RecommendedAction.REVIEW


@dataclass
class IntegrityOutcome:
    units: list[UnitChecks]
    issues: list[Issue]
    duration_ms: float
    stats: dict[str, int]


class _Run:
    def __init__(self, resp: DocumentResponse, ids: IdSequence):
        self.resp = resp
        self.ids = ids
        self.file_type = str(resp.file_type or "").lower()
        self.page_count = resp.page_count if isinstance(resp.page_count, int) and resp.page_count >= 1 else 1
        self.unit_type = UNIT_TYPE_BY_FORMAT.get(self.file_type, UnitType.PAGE)
        self.doc_unit = UnitRef(type=UnitType.DOCUMENT, index=1)
        self.findings: dict[tuple[str, str], _Finding] = {}
        self.sheet_labels: dict[int, str] = {}

    # -- units -------------------------------------------------------------
    def unit_for(self, page: Any) -> UnitRef:
        if isinstance(page, int) and not isinstance(page, bool) and 1 <= page <= self.page_count:
            return UnitRef(type=self.unit_type, index=page, label=self.sheet_labels.get(page))
        return self.doc_unit

    # -- recording ---------------------------------------------------------
    def add(
        self,
        unit: UnitRef,
        code: str,
        message: str,
        *,
        severity: Severity | None = None,
        block: DocumentBlock | None = None,
        example: str | None = None,
        detail: dict[str, Any] | None = None,
        action: RecommendedAction = RecommendedAction.REVIEW,
    ) -> None:
        default_sev, kind, _ = CODE_CATALOG[code]
        key = (unit.key, code)
        f = self.findings.get(key)
        if f is None:
            f = self.findings[key] = _Finding(
                unit=unit, code=code, severity=severity or default_sev,
                heuristic=kind == EvidenceKind.HEURISTIC, message=message, action=action,
            )
        else:
            f.severity = max(f.severity, severity or default_sev, key=list(Severity).index)
        f.count += 1
        if block is not None:
            bid = getattr(block, "id", None)
            if isinstance(bid, str) and bid and bid not in f.block_ids and len(f.block_ids) < MAX_BLOCK_IDS:
                f.block_ids.append(bid)
            if f.page is None and unit.type != UnitType.DOCUMENT:
                f.page = unit.index
            bb = _safe_bbox(block)
            if f.bbox is None and bb is not None and bbox_problem(bb) is None:
                f.bbox = bb
                f.preview = f.preview or _preview_of(block)
        if example and len(f.examples) < MAX_EXAMPLES:
            f.examples.append(example)
        if detail:
            f.detail.update(detail)


def _safe_bbox(block: DocumentBlock) -> tuple[float, ...] | None:
    bb = getattr(block, "bbox", None)
    try:
        return tuple(bb) if bb is not None else None
    except TypeError:
        return None


def _meta(block: DocumentBlock) -> dict:
    m = getattr(block, "metadata", None)
    return m if isinstance(m, dict) else {}


def _preview_of(block: DocumentBlock) -> PreviewLocator | None:
    p = _meta(block).get("preview")
    if not isinstance(p, dict):
        return None
    page, bb = p.get("page"), p.get("bbox")
    try:
        return PreviewLocator(page=page, bbox=tuple(bb) if bb else None, approximate=bool(p.get("bbox_approximate")))
    except (ValueError, TypeError):
        return None


def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _document_level(run: _Run) -> None:
    r = run.resp
    if not _is_int(r.page_count) or r.page_count < 1:
        run.add(run.doc_unit, "invalid_page_count", "page_count must be a positive integer.",
                example=f"page_count={r.page_count!r}")
    if not r.blocks:
        sev = Severity.CRITICAL if r.errors else Severity.HIGH
        run.add(run.doc_unit, "empty_extraction",
                "The extractor returned no blocks" + (" and reported errors." if r.errors else "."),
                severity=sev, example=f"file_type={r.file_type}, page_count={r.page_count}",
                action=RecommendedAction.SECONDARY_EXTRACTION)
    if r.status == "partial" and not r.errors:
        run.add(run.doc_unit, "partial_status_without_errors", "Status is 'partial' but no error is recorded.",
                example="status='partial', errors=[]")
    if r.status == "success" and r.errors:
        run.add(run.doc_unit, "errors_with_success_status",
                f"Status is 'success' but {len(r.errors)} error(s) were recorded.",
                example=", ".join(e.code for e in r.errors[:MAX_EXAMPLES]))
    for err in list(r.errors)[:MAX_ERROR_ISSUES]:
        unit = run.unit_for(err.page) if err.page is not None else run.doc_unit
        sev = Severity.HIGH if _UNIT_LOSS.search(err.code or "") else Severity.MEDIUM
        run.add(unit, "extractor_error", f"The extractor reported an error ({err.code}).", severity=sev,
                example=f"{err.code}: {err.message}", detail={"codes": sorted({e.code for e in r.errors})},
                action=RecommendedAction.SECONDARY_EXTRACTION)
    if len(r.errors) > MAX_ERROR_ISSUES:
        run.add(run.doc_unit, "extractor_error", "More extractor errors were recorded than are itemised.",
                example=f"{len(r.errors)} errors recorded, first {MAX_ERROR_ISSUES} itemised",
                detail={"errors_total": len(r.errors), "errors_itemised": MAX_ERROR_ISSUES})


def _ids(run: _Run, blocks: list[DocumentBlock]) -> None:
    counts = Counter(b.id for b in blocks if isinstance(b.id, str) and b.id)
    reported = set()
    for b in blocks:
        if not isinstance(b.id, str) or not b.id.strip():
            run.add(run.unit_for(b.page), "missing_required_field", "A block has no id.",
                    block=b, example="id is empty", severity=Severity.HIGH)
        elif counts[b.id] > 1 and b.id not in reported:
            reported.add(b.id)
            run.add(run.doc_unit, "duplicate_block_id", "Block ids must be unique.", block=b,
                    example=f"{b.id!r} appears {counts[b.id]} times")
        if not isinstance(b.extractor, str) or not b.extractor.strip():
            run.add(run.unit_for(b.page), "missing_required_field", "A block does not name its extractor.",
                    block=b, example="extractor is empty", severity=Severity.MEDIUM)


def _provenance(run: _Run, b: DocumentBlock) -> None:
    unit = run.unit_for(b.page)
    if not _is_int(b.page) or b.page < 1 or b.page > run.page_count:
        run.add(run.doc_unit, "invalid_page_reference",
                f"Block page must be between 1 and {run.page_count}.", block=b,
                example=f"{b.id}: page={b.page!r} (page_count={run.page_count})")
    bb = _safe_bbox(b)
    if b.bbox is not None and bb is None:
        run.add(unit, "invalid_bbox", "bbox is not a sequence of numbers.", block=b,
                example=f"{b.id}: bbox={b.bbox!r}", detail={"problem": "bbox is not a sequence of numbers"})
    elif bb is not None:
        problem = bbox_problem(bb)
        if problem:
            run.add(unit, "invalid_bbox", "bbox cannot be used to highlight the source.", block=b,
                    example=f"{b.id}: {problem}: {bb}", detail={"problem": problem})
        elif bb[0] == bb[2] or bb[1] == bb[3]:
            run.add(unit, "degenerate_bbox", "bbox has no area.", block=b, example=f"{b.id}: {bb}")
    elif run.file_type in GEOMETRY_FORMATS:
        run.add(unit, "missing_required_provenance",
                f"Blocks from .{run.file_type} files must carry a bbox so the source can be located.",
                block=b, example=f"{b.id} ({b.type}) has no bbox", detail={"field": "bbox"})

    conf = b.confidence
    if conf is not None and (not isinstance(conf, (int, float)) or isinstance(conf, bool) or not math.isfinite(conf) or not 0 <= conf <= 1):
        run.add(unit, "invalid_confidence", "confidence must be null or within 0..1.", block=b,
                example=f"{b.id}: confidence={conf!r}")

    meta = _meta(b)
    slide = meta.get("slide_number")
    if run.file_type == "pptx" and slide is not None and slide != b.page:
        run.add(unit, "unit_reference_mismatch", "A block's slide_number disagrees with its page.", block=b,
                example=f"{b.id}: page={b.page}, slide_number={slide!r}")
    if "preview" in meta:
        p = meta["preview"]
        problem = None
        if not isinstance(p, dict) or not _is_int(p.get("page")) or p["page"] < 1:
            problem = "preview.page must be a positive integer"
        elif run.resp.preview_pages and p["page"] > run.resp.preview_pages:
            problem = f"preview.page {p['page']} is beyond preview_pages={run.resp.preview_pages}"
        elif p.get("bbox") is not None and (_pb := bbox_problem(_try_tuple(p["bbox"]))):
            problem = f"preview.bbox: {_pb}"
        if problem:
            run.add(unit, "malformed_preview_locator", "The viewer cannot draw this block's preview location.",
                    block=b, example=f"{b.id}: {problem}")


def _try_tuple(v: Any) -> tuple | None:
    try:
        return tuple(v)
    except TypeError:
        return (None,)  # forces bbox_problem to report it


def _content(run: _Run, b: DocumentBlock) -> None:
    unit = run.unit_for(b.page)
    meta = _meta(b)
    text = b.content
    if not isinstance(text, str):
        run.add(unit, "invalid_block_content", "content must be text.", block=b,
                example=f"{b.id}: content is {type(text).__name__}", severity=Severity.HIGH)
        return
    flagged = bool(meta.get("flags") or meta.get("needs_review") or b.requires_review)
    blank = not text.strip()
    if blank:
        if b.type in TEXT_BLOCKS:
            run.add(unit, "empty_block_content", f"A {b.type.value} block has no text.", block=b, example=f"{b.id} ({b.type.value})")
        elif b.type == BlockType.TABLE and not _table_has_cells(meta):
            run.add(unit, "empty_block_content", "A table block has no content and no cells.", block=b, example=b.id)
        elif b.type in (BlockType.CHART, BlockType.EQUATION) and not flagged:
            run.add(unit, "empty_block_content", f"A {b.type.value} block is empty and the extractor did not say why.",
                    block=b, example=b.id)
    n_ctrl = len(_CONTROL.findall(text))
    n_repl = text.count("�")
    if n_ctrl:
        run.add(unit, "invalid_block_content", "Content contains control characters.", block=b,
                example=f"{b.id}: {n_ctrl} control character(s)")
    if n_repl:
        sev = Severity.LOW if n_repl <= 2 and n_repl / max(len(text), 1) < 0.02 else Severity.MEDIUM
        run.add(unit, "invalid_block_content", "Content contains Unicode replacement characters (decoding damage).",
                block=b, example=f"{b.id}: {n_repl} replacement character(s) in {len(text)}", severity=sev)


def _table_has_cells(meta: dict) -> bool:
    rows = meta.get("rows")
    return isinstance(rows, list) and any(isinstance(r, list) and any(c not in (None, "") for c in r) for r in rows)


def _metadata(run: _Run, b: DocumentBlock) -> None:
    unit = run.unit_for(b.page)
    if not isinstance(getattr(b, "metadata", None), dict):
        run.add(unit, "malformed_metadata", "metadata must be an object.", block=b, example=f"{b.id}: metadata is {type(b.metadata).__name__}")
        return
    meta = b.metadata
    if b.type == BlockType.TABLE:
        rows = meta.get("rows")
        if not isinstance(rows, list) or not all(isinstance(r, list) for r in rows):
            run.add(unit, "malformed_metadata", "A table block needs metadata.rows as a list of rows.", block=b,
                    example=f"{b.id}: rows is {type(rows).__name__}")
        else:
            rc, cc = meta.get("row_count"), meta.get("col_count")
            if rc is not None and rc != len(rows):
                run.add(unit, "malformed_metadata", "Table row_count disagrees with rows.", block=b,
                        example=f"{b.id}: row_count={rc}, len(rows)={len(rows)}")
            width = max((len(r) for r in rows), default=0)
            if cc is not None and cc != width:
                run.add(unit, "malformed_metadata", "Table col_count disagrees with the widest row.", block=b,
                        example=f"{b.id}: col_count={cc}, widest row={width}", severity=Severity.LOW)
        for part in meta.get("part_bboxes") or []:
            pg = part.get("page") if isinstance(part, dict) else None
            pb = bbox_problem(_try_tuple(part.get("bbox"))) if isinstance(part, dict) and part.get("bbox") is not None else None
            if not isinstance(part, dict) or not _is_int(pg) or not 1 <= pg <= run.page_count or pb:
                run.add(unit, "malformed_metadata", "A merged table's part_bboxes entry is invalid.", block=b,
                        example=f"{b.id}: {part!r}")
        for pg in meta.get("merged_from_pages") or []:
            if not _is_int(pg) or not 1 <= pg <= run.page_count:
                run.add(unit, "malformed_metadata", "merged_from_pages names a page that does not exist.", block=b,
                        example=f"{b.id}: page {pg!r}")
        if run.file_type == "xlsx" and not (isinstance(meta.get("sheet_name"), str) and meta["sheet_name"].strip()):
            run.add(unit, "malformed_metadata", "A spreadsheet table block needs metadata.sheet_name.", block=b, example=b.id)
    if b.type == BlockType.HEADING and "heading_level" in meta:
        lvl = meta["heading_level"]
        if not _is_int(lvl) or not 1 <= lvl <= 6:
            run.add(unit, "malformed_metadata", "heading_level must be an integer from 1 to 6.", block=b,
                    example=f"{b.id}: heading_level={lvl!r}")
    if meta.get("flags") or meta.get("needs_review") or b.requires_review:
        flags = meta.get("flags") if isinstance(meta.get("flags"), list) else []
        run.add(unit, "extractor_flagged_blocks", "The extractor marked these blocks as incomplete or needing review.",
                block=b, example=f"{b.id}: " + (", ".join(map(str, flags)) or "requires_review"),
                detail={"flags": sorted({str(f) for f in flags})})


def _reading_order(run: _Run, blocks: list[DocumentBlock]) -> None:
    if not blocks:
        return
    missing = [b for b in blocks if b.reading_order is None]
    if missing:
        scope = "No block" if len(missing) == len(blocks) else f"{len(missing)} of {len(blocks)} blocks"
        run.add(run.doc_unit, "reading_order_missing", f"{scope} has a reading_order.", block=missing[0],
                example=f"first: {missing[0].id}")
        for b in missing[1:MAX_BLOCK_IDS]:
            run.findings[(run.doc_unit.key, "reading_order_missing")].block_ids.append(b.id)
    values = [(b, b.reading_order) for b in blocks if b.reading_order is not None]
    bad = [(b, v) for b, v in values if not _is_int(v) or v < 0]
    for b, v in bad:
        run.add(run.doc_unit, "invalid_reading_order", "reading_order must be a non-negative integer.", block=b,
                example=f"{b.id}: reading_order={v!r}")
    good = [(b, v) for b, v in values if _is_int(v) and v >= 0]
    counts = Counter(v for _, v in good)
    for v in sorted(k for k, c in counts.items() if c > 1)[:MAX_EXAMPLES]:
        first = next(b for b, x in good if x == v)
        run.add(run.doc_unit, "duplicate_reading_order", "Two blocks share a reading_order value.", block=first,
                example=f"reading_order={v} used {counts[v]} times")
    for (b1, v1), (b2, v2) in zip(good, good[1:]):
        if v2 < v1:
            run.add(run.doc_unit, "reading_order_not_monotonic", "The block list is not in reading_order sequence.",
                    block=b2, example=f"{b1.id}={v1} is followed by {b2.id}={v2}")


def _duplicates(run: _Run, blocks: list[DocumentBlock]) -> None:
    groups: dict[tuple, list[DocumentBlock]] = defaultdict(list)
    for b in blocks:
        if not isinstance(b.content, str):
            continue
        norm = normalize_text(b.content, case_sensitive=False)
        if len(norm) >= MIN_DUPLICATE_CHARS and _is_int(b.page):
            groups[(b.page, str(b.type), norm)].append(b)
    for (page, _t, norm), members in groups.items():
        if len(members) < 2:
            continue
        unit = run.unit_for(page)
        if run.file_type in GEOMETRY_FORMATS:
            for i, a in enumerate(members):
                ba = _safe_bbox(a)
                for c in members[i + 1:]:
                    bc = _safe_bbox(c)
                    if ba and bc and not bbox_problem(ba) and not bbox_problem(bc) and _iou(ba, bc) >= DUPLICATE_IOU:
                        run.add(unit, "duplicate_block", "Identical content extracted twice at the same place.",
                                block=c, example=f"{a.id} and {c.id}: {norm[:60]!r}")
        else:
            idx = {id(b): i for i, b in enumerate(blocks)}
            ordered = sorted(members, key=lambda b: idx[id(b)])
            for a, c in zip(ordered, ordered[1:]):
                if idx[id(c)] == idx[id(a)] + 1:
                    run.add(unit, "repeated_adjacent_block", "Identical neighbouring blocks.", block=c,
                            example=f"{a.id} and {c.id}: {norm[:60]!r}")


def _units_without_blocks(run: _Run, blocks: list[DocumentBlock]) -> None:
    if not blocks or run.page_count > MAX_ENUMERATED_UNITS:
        return
    covered = {b.page for b in blocks if _is_int(b.page)}
    for b in blocks:
        for pg in _meta(b).get("merged_from_pages") or []:  # a cross-page table lives on its first page only
            if _is_int(pg):
                covered.add(pg)
    for p in range(1, run.page_count + 1):
        if p not in covered:
            run.add(run.unit_for(p), "unit_without_blocks",
                    f"This {run.unit_type.value} produced no blocks (it may be blank, or its content was not read).",
                    example=f"{run.unit_type.value} {p}: 0 blocks of {len(blocks)} in the document")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_integrity(resp: DocumentResponse, ids: IdSequence | None = None) -> IntegrityOutcome:
    """Run every integrity rule over a finished extraction. Pure: reads `resp`, changes nothing."""
    t0 = time.perf_counter()
    ids = ids or IdSequence("int")
    run = _Run(resp, ids)
    blocks = list(resp.blocks)
    for b in blocks:
        if run.file_type == "xlsx" and isinstance(_meta(b).get("sheet_name"), str) and _is_int(b.page):
            run.sheet_labels.setdefault(b.page, _meta(b)["sheet_name"])

    _document_level(run)
    _ids(run, blocks)
    for b in blocks:
        _provenance(run, b)
        _content(run, b)
        _metadata(run, b)
    _reading_order(run, blocks)
    _duplicates(run, blocks)
    _units_without_blocks(run, blocks)

    # -- turn findings into issues and per-unit check results ----------------
    unit_order: list[UnitRef] = []
    seen: set[str] = set()

    def note(u: UnitRef) -> None:
        if u.key not in seen:
            seen.add(u.key)
            unit_order.append(u)

    if run.page_count <= MAX_ENUMERATED_UNITS:
        for p in range(1, run.page_count + 1):
            note(run.unit_for(p))
    else:
        for p in sorted({b.page for b in blocks if _is_int(b.page) and 1 <= b.page <= run.page_count}):
            note(run.unit_for(p))
    doc_findings = [f for f in run.findings.values() if f.unit.type == UnitType.DOCUMENT]
    for f in run.findings.values():
        note(f.unit)
    unit_order.sort(key=lambda u: (u.type != UnitType.DOCUMENT, u.index))

    issues: list[Issue] = []
    by_unit: dict[str, list[tuple[bool, Issue]]] = defaultdict(list)
    for u in unit_order:
        for f in sorted((x for x in run.findings.values() if x.unit.key == u.key), key=lambda x: x.code):
            kind = EvidenceKind.HEURISTIC if f.heuristic else EvidenceKind.DETERMINISTIC
            engine = "integrity.heuristic" if f.heuristic else "integrity.structural"
            detail = {"count": f.count, **f.detail}
            observed = "; ".join(f.examples) if f.examples else None
            issue = Issue(
                id=ids.next(),
                code=f.code,
                severity=f.severity,
                layer=CheckName.EXTRACTION_INTEGRITY,
                message=f.message,
                locator=Locator(unit=u, page=f.page, bbox=f.bbox, block_ids=f.block_ids, preview=f.preview),
                evidence=[IssueEvidence(kind=kind, engine=engine, source=f"rule: {CODE_CATALOG[f.code][2]}",
                                        extracted=observed, detail=detail)],
                recommended_action=f.action,
            )
            issues.append(issue)
            by_unit[u.key].append((f.heuristic, issue))

    page_blocks = Counter(b.page for b in blocks if _is_int(b.page))
    units: list[UnitChecks] = []
    for u in unit_order:
        if u.type == UnitType.DOCUMENT and not doc_findings:
            continue
        checks: list[CheckResult] = []
        for heuristic in (False, True):
            mine = [i for h, i in by_unit.get(u.key, []) if h == heuristic]
            if heuristic and u.type == UnitType.DOCUMENT and not mine:
                continue
            blocking = [i for i in mine if i.severity in _REVIEW_OR_WORSE]
            checks.append(
                CheckResult(
                    check=CheckName.EXTRACTION_INTEGRITY,
                    outcome=CheckOutcome.FAILED if blocking else CheckOutcome.PASSED,
                    evidence_kind=EvidenceKind.HEURISTIC if heuristic else EvidenceKind.DETERMINISTIC,
                    engine="integrity.heuristic" if heuristic else "integrity.structural",
                    summary=("structure and operation only; says nothing about whether content is correct"
                             if not blocking else f"{len(blocking)} structural/operational problem(s)"),
                    counts={"blocks": page_blocks.get(u.index, 0) if u.type != UnitType.DOCUMENT else len(blocks),
                            "issues": len(mine)},
                    issue_ids=[i.id for i in mine],
                )
            )
        units.append(UnitChecks(unit=u, checks=checks))

    return IntegrityOutcome(
        units=units,
        issues=issues,
        duration_ms=round((time.perf_counter() - t0) * 1000, 3),
        stats={"blocks": len(blocks), "units": len(units), "issues": len(issues),
               "errors_reported": len(resp.errors)},
    )
