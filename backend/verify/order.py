"""AXTRACT Verify, layer 5: reading order.

Does the output present the content in the order the source does? This layer is deliberately
conservative: where the source has no reliable notion of order, it says so instead of inventing one.

  DOCX   The body of a document IS its reading order. The order of matched objects in the output is
         compared with their order in the file (deterministic).
  PPTX   Z-order is not reading order. Only unambiguous relations are checked: one shape entirely
         ABOVE another in the same column should be read first (heuristic, from exact geometry).
  PDF    Single-column pages: top-to-bottom. Pages with side-by-side regions (multi-column) are
         reported NOT_VERIFIABLE because their order is an interpretation (heuristic).
  XLSX   NOT_APPLICABLE. A spreadsheet has no reading order and none is invented.
  IMAGE  NOT_VERIFIABLE (no independent layout evidence).

Missing text is the completeness layer's finding; this layer only compares the order of what is there.
"""

from __future__ import annotations

from bisect import bisect_left
from typing import Sequence

from models.document import BlockType as T, DocumentBlock, DocumentResponse
from verify.completeness import MatchRecord, MatchStatus, _bbox
from verify.content import is_ocr_block, out_text
from verify.ids import IdSequence
from verify.inventory.models import Contract, SourceInventory, SourceObjectType as SO
from verify.layer import LayerBuilder, LayerOutcome
from verify.matching import containment, iou, recall
from verify.models import CheckName, CheckOutcome, EvidenceKind, Severity, UnitRef, UnitType

DET, HEUR = EvidenceKind.DETERMINISTIC, EvidenceKind.HEURISTIC
MIN_REGION_CHARS = 15
MAX_LISTED = 6

CODE_CATALOG: dict[str, tuple[Severity, str]] = {
    "reading_order_mismatch": (Severity.MEDIUM, "content appears in the output in a different order than in the source"),
}


def _out_rank(blocks: list[DocumentBlock]) -> dict[str, int]:
    """Position of each block in the output's reading order (reading_order when complete, else list order)."""
    ro = [b.reading_order for b in blocks]
    if all(isinstance(v, int) and not isinstance(v, bool) for v in ro) and len(set(ro)) == len(ro):
        return {b.id: v for b, v in zip(blocks, ro)}
    return {b.id: i for i, b in enumerate(blocks)}


def _displaced(seq: Sequence[int], strict: bool = False) -> list[int]:
    """Indices NOT in a longest increasing (or non-decreasing) subsequence: the items that are out of place."""
    n = len(seq)
    tails: list[int] = []  # smallest tail value of an increasing run of each length
    tail_idx: list[int] = []
    prev = [-1] * n
    for i, v in enumerate(seq):
        pos = bisect_left(tails, v) if strict else _bisect_right(tails, v)
        if pos == len(tails):
            tails.append(v)
            tail_idx.append(i)
        else:
            tails[pos], tail_idx[pos] = v, i
        prev[i] = tail_idx[pos - 1] if pos else -1
    keep: set[int] = set()
    k = tail_idx[-1] if tail_idx else -1
    while k != -1:
        keep.add(k)
        k = prev[k]
    return [i for i in range(n) if i not in keep]


def _bisect_right(a: list[int], x: int) -> int:
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if x < a[mid]:
            hi = mid
        else:
            lo = mid + 1
    return lo


def _docx(lb: LayerBuilder, inv: SourceInventory, resp: DocumentResponse, matches: list[MatchRecord]) -> None:
    blocks = {b.id: b for b in resp.blocks}
    rank = _out_rank(list(resp.blocks))
    objs = {o.id: o for o in inv.objects()}
    u = lb.unit(inv.units[0].unit)
    pairs = []
    for m in matches:
        o = objs.get(m.object_id)
        if o and m.status == MatchStatus.MATCHED and m.block_ids and o.contract == Contract.EXPECTED and o.locator.position:
            pairs.append((o.locator.position, rank[m.block_ids[0]], o, blocks[m.block_ids[0]]))
    pairs.sort(key=lambda p: p[0])
    lb.examine(u, DET, objects=len(pairs))
    bad = _displaced([p[1] for p in pairs], strict=True)
    if bad:
        shown = [pairs[i] for i in bad[:MAX_LISTED]]
        lb.find(u, "reading_order_mismatch", f"{len(bad)} object(s) are out of order relative to the document body.", kind=DET, engine="ooxml:word/document.xml",
                source="document body order: " + ", ".join(f"{(o.text or o.type.value)[:30]!r}@{o.locator.path}" for _, _, o, _ in shown),
                extracted="output positions: " + ", ".join(f"{b.id}={r}" for _, r, _, b in shown),
                block_ids=[b.id for _, _, _, b in shown],
                detail={"displaced": [{"source_path": o.locator.path, "block": b.id, "output_rank": r} for _, r, o, b in shown], "count": len(bad)})


def _pptx(lb: LayerBuilder, inv: SourceInventory, resp: DocumentResponse, matches: list[MatchRecord]) -> None:
    blocks = {b.id: b for b in resp.blocks}
    rank = _out_rank(list(resp.blocks))
    objs = {o.id: o for o in inv.objects()}
    by_unit: dict[str, list] = {}
    for m in matches:
        o = objs.get(m.object_id)
        if o and m.status == MatchStatus.MATCHED and m.block_ids and o.contract == Contract.EXPECTED and o.locator.bbox \
                and o.type in (SO.TEXT, SO.HEADING, SO.TABLE, SO.PICTURE, SO.CHART):
            by_unit.setdefault(o.unit.key, []).append((o, min(rank[i] for i in m.block_ids if i in rank)))
    for su in inv.units:
        u = lb.unit(su.unit)
        items = by_unit.get(u.key, [])
        pairs = bad = 0
        examples = []
        for i, (a, ra) in enumerate(items):
            for b2, rb in items[i + 1:]:
                for first, second, r1, r2 in ((a, b2, ra, rb), (b2, a, rb, ra)):
                    f, s = first.locator.bbox, second.locator.bbox
                    overlap = min(f[2], s[2]) - max(f[0], s[0])
                    if f[3] <= s[1] and overlap >= 0.5 * min(f[2] - f[0], s[2] - s[0]):  # f is entirely above s, same column
                        pairs += 1
                        if r1 > r2:
                            bad += 1
                            examples.append((first, second))
        if not pairs:
            lb.note(u, CheckOutcome.NOT_VERIFIABLE, "no pair of shapes has an unambiguous reading order (z-order is not reading order)", "order.pptx", HEUR)
            continue
        lb.examine(u, HEUR, pairs=pairs)
        if bad:
            lb.find(u, "reading_order_mismatch", f"{bad} of {pairs} clearly stacked shape pair(s) are read in the wrong order.", kind=HEUR,
                    engine="ooxml:xfrm geometry", source="; ".join(f"{(f.text or f.type.value)[:25]!r} above {(s.text or s.type.value)[:25]!r}" for f, s in examples[:MAX_LISTED]),
                    extracted="the lower shape is emitted first",
                    detail={"violations": bad, "pairs_checked": pairs, "rule": "a shape entirely above another in the same column is read first"})


def _pdf(lb: LayerBuilder, inv: SourceInventory, resp: DocumentResponse, matches: list[MatchRecord]) -> None:
    rank = _out_rank(list(resp.blocks))
    for su in inv.units:
        u = lb.unit(su.unit)
        if not su.independent_text_available:
            lb.note(u, CheckOutcome.NOT_VERIFIABLE, "scanned page: no independent text or layout evidence", "order.pdf")
            continue
        regions = [o for o in su.objects if o.type == SO.TEXT and o.metadata.get("scope") == "region"
                   and o.metadata.get("chars", 0) >= MIN_REGION_CHARS and o.locator.bbox]
        if su.properties.get("rotation", 0) != 0 or len(regions) < 2:
            lb.note(u, CheckOutcome.NOT_VERIFIABLE, "too little layout evidence to compare reading order", "order.pdf", HEUR)
            continue
        side_by_side = any(
            min(a.locator.bbox[3], b.locator.bbox[3]) - max(a.locator.bbox[1], b.locator.bbox[1]) >= 0.3 * min(a.locator.bbox[3] - a.locator.bbox[1], b.locator.bbox[3] - b.locator.bbox[1])
            and (a.locator.bbox[2] <= b.locator.bbox[0] or b.locator.bbox[2] <= a.locator.bbox[0])
            for i, a in enumerate(regions) for b in regions[i + 1:])
        if side_by_side:
            lb.note(u, CheckOutcome.NOT_VERIFIABLE, "multi-column or side-by-side layout: the reading order depends on interpretation", "order.pdf", HEUR)
            continue
        cands = [b for b in resp.blocks if b.page == u.index and b.type not in (T.FIGURE, T.CHART, T.EQUATION) and not is_ocr_block(b) and _bbox(b)]
        seq, who = [], []
        for reg in sorted(regions, key=lambda r: (r.locator.bbox[1], r.locator.bbox[0])):
            best = None
            for b in cands:
                ov = max(containment(reg.locator.bbox, _bbox(b)), iou(reg.locator.bbox, _bbox(b)))
                if ov >= 0.5 and recall(reg.text, out_text(b))[0] >= 0.5 and (best is None or ov > best[0]):
                    best = (ov, b)
            if best:
                seq.append(rank[best[1].id])
                who.append((reg, best[1]))
        if len(seq) < 2:
            lb.note(u, CheckOutcome.NOT_VERIFIABLE, "too few text regions could be matched to blocks to compare order", "order.pdf", HEUR)
            continue
        lb.examine(u, HEUR, regions=len(seq))
        bad = _displaced(seq, strict=False)
        if bad:
            lb.find(u, "reading_order_mismatch", f"{len(bad)} text region(s) are out of top-to-bottom order on this single-column page.", kind=HEUR,
                    engine="pdfplumber regions", bbox=who[bad[0]][0].locator.bbox,
                    source="top-to-bottom: " + "; ".join(f"{who[i][0].text[:28]!r}" for i in bad[:MAX_LISTED]),
                    extracted="emitted out of sequence: " + ", ".join(who[i][1].id for i in bad[:MAX_LISTED]),
                    block_ids=[who[i][1].id for i in bad[:MAX_LISTED]],
                    detail={"displaced": len(bad), "regions_compared": len(seq), "layout": "single column (heuristic)"})


def _xlsx(lb: LayerBuilder, inv: SourceInventory, resp: DocumentResponse, matches: list[MatchRecord]) -> None:
    for su in inv.units:
        lb.note(su.unit, CheckOutcome.NOT_APPLICABLE, "a spreadsheet has no reading order; none is invented", "order.xlsx")


def _image(lb: LayerBuilder, inv: SourceInventory, resp: DocumentResponse, matches: list[MatchRecord]) -> None:
    for su in inv.units:
        lb.note(su.unit, CheckOutcome.NOT_VERIFIABLE, "reading order of an image cannot be verified without an independent reader", "order.image")


_COMPARATORS = {"xlsx": _xlsx, "pptx": _pptx, "docx": _docx, "pdf": _pdf, "png": _image, "jpg": _image, "jpeg": _image}


def check_order(inv: SourceInventory, resp: DocumentResponse, matches: list[MatchRecord], ids: IdSequence | None = None) -> LayerOutcome:
    """Compare the order of the output with the order the source supports. Pure."""
    lb = LayerBuilder(CheckName.READING_ORDER, CODE_CATALOG, ids)
    if inv.error:
        lb.notes.append(f"source inventory unavailable ({inv.error}); reading order could not be compared")
        lb.note(UnitRef(type=UnitType.DOCUMENT, index=1), CheckOutcome.NOT_VERIFIABLE, lb.notes[-1], "order")
    elif (cmp_ := _COMPARATORS.get(inv.file_type)) is None:
        lb.notes.append(f"no reading-order comparator for .{inv.file_type}")
    else:
        try:
            cmp_(lb, inv, resp, matches)
        except Exception as exc:  # noqa: BLE001
            lb.notes.append(f"reading-order comparison failed ({type(exc).__name__}: {exc})")
            lb._findings.clear()
            lb._examined.clear()
    return lb.build()
