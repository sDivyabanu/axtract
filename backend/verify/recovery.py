"""Targeted escalation and explicit recovery.

escalate()  For units Verify already flagged, ask secondary providers to re-read them and record each
            answer as a CANDIDATE, compared with both the original extraction and the independent source
            evidence. Nothing is changed: every record starts as `candidate_only`.
promote()   The ONLY way a candidate reaches the output. It is an explicit call, it requires the
            comparison evidence to favour the candidate (or a named reviewer's decision), and it works on
            a COPY: the original blocks stay in place (marked as superseded), the new content is added as
            a separate block tagged with who produced it and why, and the report keeps the whole trail:
            original text, candidate, comparison metrics, decision, decider and reason.

A secondary extraction is never trusted blindly. Agreement between two readers of the same page is
evidence; it is not a guarantee, and a promoted unit is reported RECOVERED, not VERIFIED.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from models.document import BlockType as T, DocumentBlock, DocumentResponse
from services.markdown_service import blocks_to_markdown
from verify.content import is_ocr_block, out_text
from verify.ids import IdSequence
from verify.inventory import SourceInventory, build_inventory
from verify.inventory.models import SourceObjectType as SO
from verify.matching import recall, similarity
from verify.models import (
    CandidateSnapshot, CheckName, ComparisonEvidence, EvidenceKind, Issue, OriginalSnapshot, ProviderInfo, RecoveryDecision,
    RecoveryRecord, Severity, UnitResult, UnitType, ValidationReport, ValidationStatus,
)
from verify.providers import ProviderError, ValidationProvider, default_providers
from verify.rollup import UnitChecks, build_report

_BLOCKING = {Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL}
_SECONDARY_LAYERS = {CheckName.CONTENT, CheckName.COMPLETENESS, CheckName.STRUCTURE, CheckName.CROSS_CHECK}
AGREES = 0.98  # share of the source's words a reading must contain to count as agreeing with it
MAX_UNITS = 10
MAX_RECORDS_PER_UNIT = 5


class PromotionRefused(Exception):
    """A candidate may not be promoted on this evidence."""


@dataclass
class EscalationResult:
    report: ValidationReport
    recoveries: list[RecoveryRecord] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class PromotionResult:
    response: DocumentResponse
    report: ValidationReport


def _report(report) -> ValidationReport:
    return report if isinstance(report, ValidationReport) else ValidationReport.model_validate(report)


def _unit_blocks(response: DocumentResponse, unit_type: UnitType, index: int) -> list[DocumentBlock]:
    return [b for b in response.blocks if unit_type == UnitType.SECTION or b.page == index]


def _source_text(inv: SourceInventory, unit_index: int) -> str | None:
    """Independent source text of a unit, or None when no independent reader exists."""
    su = inv.unit(unit_index) if inv.file_type != "docx" else (inv.units[0] if inv.units else None)
    if su is None or not su.independent_text_available:
        return None
    if inv.file_type == "xlsx":
        cells = next((o for o in su.objects if o.type == SO.CELL_REGION), None)
        return " ".join(cells.metadata["cells"].values()) if cells else ""
    if inv.file_type == "pdf":
        page = next((o for o in su.objects if o.type == SO.TEXT and o.metadata.get("scope") == "page"), None)
        return page.text if page else ""
    return " ".join(o.text for o in su.objects if o.text and o.contract.value == "expected")


def _verdict(r_src_orig: float | None, r_src_cand: float | None) -> str:
    if r_src_orig is None or r_src_cand is None:
        return "inconclusive"  # no independent source text to arbitrate between the two readings
    if r_src_cand >= AGREES and r_src_orig < AGREES:
        return "candidate_supported"
    if r_src_orig >= AGREES and r_src_cand < AGREES:
        return "original_supported"
    return "both_agree_with_source" if r_src_orig >= AGREES else "inconclusive"


def escalate(file_path: Path, response: DocumentResponse, report, *, providers: list[ValidationProvider] | None = None,
             inventory: SourceInventory | None = None, budget_s: float = 30.0) -> EscalationResult:
    """Ask secondary providers to re-read the units Verify flagged. Changes nothing; returns candidate_only records."""
    rep = _report(report)
    providers = providers if providers is not None else default_providers(include_heavy=True)
    inv = inventory or build_inventory(Path(file_path), response.file_type)
    t_all = time.perf_counter()
    notes: list[str] = []
    infos: list[ProviderInfo] = []
    issues_by_unit: dict[str, list[Issue]] = {}
    for i in rep.issues:
        if i.severity in _BLOCKING and i.layer in _SECONDARY_LAYERS and i.recovery_id is None:
            issues_by_unit.setdefault(i.locator.unit.key, []).append(i)
    suspicious = [u for u in rep.units if u.unit.key in issues_by_unit and u.status in (ValidationStatus.REVIEW_REQUIRED, ValidationStatus.FAILED)]
    ids = IdSequence("rec", start=len(rep.recoveries) + 1)
    new: list[RecoveryRecord] = []
    usable = []
    for p in providers:
        ok, why = p.available()
        infos.append(ProviderInfo(name=p.name, tier=p.tier, available=ok, note=why))
        if ok:
            usable.append((p, infos[-1]))
    if not usable:
        notes.append("no secondary provider is available")
    for ur in suspicious[:MAX_UNITS]:
        u = ur.unit
        blocks = _unit_blocks(response, u.type, u.index)
        original = " ".join(out_text(b) for b in blocks if not is_ocr_block(b))
        src = _source_text(inv, u.index)
        for p, info in usable:
            left = budget_s - (time.perf_counter() - t_all)
            if left <= 1:
                notes.append("escalation time budget spent; remaining units were not re-read")
                break
            if not p.supports(inv, u):
                continue
            try:
                cand = p.extract(Path(file_path), u, left)
            except ProviderError as exc:
                notes.append(f"{p.name} could not read {u.key}: {exc}")
                continue
            info.units_checked += 1
            r_orig = recall(src, original)[0] if src is not None else None
            r_cand = recall(src, cand.text)[0] if src is not None else None
            verdict = _verdict(r_orig, r_cand)
            cmp = [
                ComparisonEvidence(metric="candidate_vs_original_similarity", value=round(similarity(cand.text, original), 4), kind=EvidenceKind.DETERMINISTIC),
                ComparisonEvidence(metric="source_recall_in_original", value=None if r_orig is None else round(r_orig, 4), kind=EvidenceKind.DETERMINISTIC,
                                   note="share of the independent source's words found in the original extraction"),
                ComparisonEvidence(metric="source_recall_in_candidate", value=None if r_cand is None else round(r_cand, 4), kind=EvidenceKind.DETERMINISTIC,
                                   note="share of the independent source's words found in the candidate"),
                ComparisonEvidence(metric="verdict", value=verdict, kind=cand.kind,
                                   note="candidate_supported only when the candidate matches the independent source and the original does not"),
            ]
            for iss in issues_by_unit[u.key][:MAX_RECORDS_PER_UNIT]:
                new.append(RecoveryRecord(
                    id=ids.next(), issue_id=iss.id, unit=u, provider=p.name,
                    original=OriginalSnapshot(block_ids=[b.id for b in blocks][:50], content=original),
                    candidate=CandidateSnapshot(provider=p.name, content=cand.text, structure=cand.structure),
                    comparison=cmp, decision=RecoveryDecision.CANDIDATE_ONLY,
                    reason=f"{verdict}; the original output is unchanged until a promotion is explicitly requested"))
    out = _rebuild(rep, recoveries=list(rep.recoveries) + new, extra_providers=infos, extra_timing={"escalation": round((time.perf_counter() - t_all) * 1000, 3)})
    return EscalationResult(report=out, recoveries=new, notes=notes)


def _rebuild(rep: ValidationReport, *, recoveries: list[RecoveryRecord], issues: list[Issue] | None = None,
             extra_providers: list[ProviderInfo] | None = None, extra_timing: dict[str, float] | None = None) -> ValidationReport:
    units = [UnitChecks(unit=u.unit, checks=list(u.checks)) for u in rep.units]
    known = {p.name for p in rep.providers}
    providers = list(rep.providers) + [p for p in (extra_providers or []) if p.name not in known]
    return build_report(units, issues if issues is not None else list(rep.issues), recoveries, providers=providers,
                        timings_ms={**rep.timings_ms, **(extra_timing or {})}, failure=rep.failure)


def _metric(rec: RecoveryRecord, name: str):
    return next((c.value for c in rec.comparison if c.metric == name), None)


def promote(response: DocumentResponse, report, recovery_ids: list[str], *, decided_by: str, reason: str) -> PromotionResult:
    """Explicitly accept candidates as recovered content. Works on a copy; the original blocks are kept."""
    if decided_by not in ("explicit_request", "reviewer"):
        raise PromotionRefused("decided_by must be 'explicit_request' or 'reviewer'")
    if not reason.strip():
        raise PromotionRefused("a reason is required")
    rep = _report(report)
    wanted = {r.id: r for r in rep.recoveries if r.id in set(recovery_ids)}
    missing = set(recovery_ids) - set(wanted)
    if missing:
        raise PromotionRefused(f"unknown recovery id(s): {sorted(missing)}")
    for r in wanted.values():
        if r.decision != RecoveryDecision.CANDIDATE_ONLY:
            raise PromotionRefused(f"{r.id} is already {r.decision.value}")
        if decided_by != "reviewer" and _metric(r, "verdict") != "candidate_supported":
            raise PromotionRefused(
                f"{r.id}: the evidence does not favour the candidate (verdict: {_metric(r, 'verdict')}); "
                "only a named reviewer may promote it")
    new_resp = response.model_copy(deep=True)
    updated: dict[str, RecoveryRecord] = {}
    made: dict[tuple[str, str, str], str] = {}  # one new block per (unit, provider, candidate)
    for rid, rec in wanted.items():
        key = (rec.unit.key, rec.provider, rec.candidate.content_sha256)
        if key not in made:
            originals = [b for b in new_resp.blocks if b.id in set(rec.original.block_ids)]
            anchor = originals[-1] if originals else None
            nb = DocumentBlock(
                id=f"{rec.unit.type.value}{rec.unit.index}-recovered-{len(made) + 1}", type=T.PARAGRAPH, content=rec.candidate.content,
                page=1 if rec.unit.type == UnitType.SECTION else rec.unit.index, bbox=None, confidence=None, extractor=f"verify:{rec.provider}",
                requires_review=True,
                metadata={"recovered_for": [i.issue_id for i in wanted.values() if i.unit.key == rec.unit.key],
                          "recovery_ids": [i.id for i in wanted.values() if (i.unit.key, i.provider, i.candidate.content_sha256) == key],
                          "original_block_ids": rec.original.block_ids, "decided_by": decided_by, "provenance": "secondary extraction promoted explicitly"})
            for ob in originals:
                ob.metadata["superseded_by"] = nb.id
            new_resp.blocks.insert(new_resp.blocks.index(anchor) + 1 if anchor else len(new_resp.blocks), nb)
            made[key] = nb.id
        updated[rid] = rec.model_copy(update={"decision": RecoveryDecision.PROMOTED, "decided_by": decided_by, "reason": reason.strip(),
                                              "promoted_block_ids": [made[key]]})
    for i, b in enumerate(new_resp.blocks):  # keep reading_order a clean 0..n-1 sequence in the new version
        b.reading_order = i
    new_resp.markdown = blocks_to_markdown([b for b in new_resp.blocks if "superseded_by" not in b.metadata])
    issues = [i.model_copy(update={"recovery_id": next((rid for rid, r in updated.items() if r.issue_id == i.id), i.recovery_id)}) for i in rep.issues]
    recs = [updated.get(r.id, r) for r in rep.recoveries]
    new_rep = _rebuild(rep, recoveries=recs, issues=issues)
    new_resp.validation = new_rep.model_dump(mode="json")
    return PromotionResult(response=new_resp, report=new_rep)
