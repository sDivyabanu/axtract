"""Small builders shared by the AXTRACT Verify tests."""

from __future__ import annotations

from verify.models import (
    CandidateSnapshot,
    CheckName,
    CheckOutcome,
    CheckResult,
    ComparisonEvidence,
    EvidenceKind,
    Issue,
    IssueEvidence,
    Locator,
    OriginalSnapshot,
    RecommendedAction,
    RecoveryDecision,
    RecoveryRecord,
    Severity,
    UnitRef,
    UnitType,
)

DET = EvidenceKind.DETERMINISTIC
MODEL = EvidenceKind.MODEL_BASED
HEUR = EvidenceKind.HEURISTIC
NONE_ = EvidenceKind.UNAVAILABLE


def page(n: int = 1) -> UnitRef:
    return UnitRef(type=UnitType.PAGE, index=n)


def check(
    name: CheckName,
    outcome: CheckOutcome,
    kind: EvidenceKind | None = None,
    engine: str | None = "test-engine",
    issue_ids: list[str] | None = None,
    summary: str = "",
) -> CheckResult:
    if kind is None:
        kind = DET if outcome in (CheckOutcome.PASSED, CheckOutcome.FAILED) else NONE_
    return CheckResult(
        check=name, outcome=outcome, evidence_kind=kind, engine=engine, issue_ids=issue_ids or [], summary=summary
    )


def content_pass(kind: EvidenceKind = DET) -> CheckResult:
    return check(CheckName.CONTENT, CheckOutcome.PASSED, kind)


def issue(
    id_: str = "i1",
    severity: Severity = Severity.HIGH,
    unit: UnitRef | None = None,
    layer: CheckName = CheckName.CONTENT,
    code: str = "content_mismatch",
    recovery_id: str | None = None,
) -> Issue:
    unit = unit or page(1)
    return Issue(
        id=id_,
        code=code,
        severity=severity,
        layer=layer,
        message=f"{code} on {unit.key}",
        locator=Locator(unit=unit, page=unit.index, bbox=(0.1, 0.1, 0.5, 0.2), block_ids=["p1-b0"]),
        evidence=[IssueEvidence(kind=DET, engine="pypdfium2", source="Revenue was $18.2 million",
                                extracted="Revenue was $13.2 million")],
        recommended_action=RecommendedAction.SECONDARY_EXTRACTION,
        recovery_id=recovery_id,
    )


def recovery(
    id_: str = "r1",
    issue_id: str = "i1",
    decision: RecoveryDecision = RecoveryDecision.PROMOTED,
    unit: UnitRef | None = None,
) -> RecoveryRecord:
    promoted = decision == RecoveryDecision.PROMOTED
    return RecoveryRecord(
        id=id_,
        issue_id=issue_id,
        unit=unit or page(1),
        provider="docling",
        original=OriginalSnapshot(block_ids=["p1-b0"], content="Revenue was $13.2 million"),
        candidate=CandidateSnapshot(provider="docling", content="Revenue was $18.2 million"),
        comparison=[ComparisonEvidence(metric="matches_native_text", value=1.0, kind=DET)],
        decision=decision,
        decided_by="explicit_request" if promoted else None,
        reason="candidate equals native PDF text" if promoted else "",
        promoted_block_ids=["p1-b0-recovered"] if promoted else [],
    )
