"""AXTRACT Verify: the rollup state machine.

Pure functions: given check results, issues and recovery records, derive a status for each
unit, a status for each layer, and a status for the document. No I/O, no document access.

UNIT STATUS (first matching rule wins)

  1. FAILED           any check ended in ERROR, or an open issue has a fatal severity (critical).
  2. REVIEW_REQUIRED  an open issue has a review severity (medium/high), or a check FAILED and
                      is not fully explained by resolved issues. Passing checks do NOT outvote
                      a disagreement.
  3. RECOVERED        an issue was resolved by a PROMOTED recovery and nothing above applies.
                      A candidate that was not promoted resolves nothing.
  4. VERIFIED         at least one content-bearing check PASSED on accepted evidence.
  5. NOT_VERIFIABLE   everything else: no checks, only counts-level passes, only heuristic
                      passes, or every check lacked evidence.

  An issue is "resolved" only when its recovery_id points at a PROMOTED recovery record for
  that issue. Low/info issues are always recorded in the report but never change a status.
  "Counts alone are not proof": completeness, reading-order and integrity passes cannot make a
  unit VERIFIED; only content-bearing layers can (see RollupPolicy.substantive_layers).

DOCUMENT STATUS

  Worst unit wins, using   VERIFIED < RECOVERED < NOT_VERIFIABLE < REVIEW_REQUIRED < FAILED.
  Unknown is worse than recovered-with-evidence, and a known problem is worse than unknown.
  A validation failure (the engine itself could not finish) is FAILED. No units at all is
  NOT_VERIFIABLE. The document is never reported better than its worst unit.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from verify.models import (
    CheckName,
    CheckOutcome,
    CheckResult,
    EvidenceKind,
    Issue,
    LayerStatus,
    LayerSummary,
    ProviderInfo,
    RecoveryDecision,
    RecoveryRecord,
    Severity,
    UnitRef,
    UnitResult,
    ValidationFailure,
    ValidationReport,
    ValidationStatus,
    ValidationSummary,
)

S = ValidationStatus

# Lower rank = more trusted. Used only for the document-level "worst unit wins" rule.
STATUS_RANK: dict[ValidationStatus, int] = {
    S.VERIFIED: 0,
    S.RECOVERED: 1,
    S.NOT_VERIFIABLE: 2,
    S.REVIEW_REQUIRED: 3,
    S.FAILED: 4,
}


@dataclass(frozen=True)
class RollupPolicy:
    """The rules, as data, so a report can state exactly which rules produced its statuses."""

    # Evidence kinds strong enough to support VERIFIED. Heuristics are recorded but do not count.
    accepted_kinds: frozenset[EvidenceKind] = frozenset({EvidenceKind.DETERMINISTIC, EvidenceKind.MODEL_BASED})
    # Layers that look at content itself. Count-level layers cannot verify a unit on their own.
    substantive_layers: frozenset[CheckName] = frozenset(
        {CheckName.CONTENT, CheckName.STRUCTURE, CheckName.CROSS_CHECK}
    )
    # Open issues at these severities force REVIEW_REQUIRED / FAILED respectively.
    review_severities: frozenset[Severity] = frozenset({Severity.MEDIUM, Severity.HIGH})
    fatal_severities: frozenset[Severity] = frozenset({Severity.CRITICAL})

    def describe(self) -> dict:
        return {
            "accepted_evidence_kinds": sorted(k.value for k in self.accepted_kinds),
            "content_bearing_layers": sorted(c.value for c in self.substantive_layers),
            "review_severities": sorted(s.value for s in self.review_severities),
            "fatal_severities": sorted(s.value for s in self.fatal_severities),
            "document_rank": [s.value for s in sorted(STATUS_RANK, key=STATUS_RANK.get)],
        }


DEFAULT_POLICY = RollupPolicy()


@dataclass
class UnitChecks:
    """What the engine hands to the rollup for one unit."""

    unit: UnitRef
    checks: list[CheckResult] = field(default_factory=list)


@dataclass(frozen=True)
class UnitDecision:
    status: ValidationStatus
    reasons: list[str]
    verified_by: list[str]
    gaps: list[str]


# ---------------------------------------------------------------------------
# Unit level
# ---------------------------------------------------------------------------


def _resolved_issue_ids(issues: Iterable[Issue], recoveries: dict[str, RecoveryRecord]) -> set[str]:
    resolved = set()
    for issue in issues:
        rec = recoveries.get(issue.id)
        if (
            rec is not None
            and issue.recovery_id == rec.id
            and rec.issue_id == issue.id
            and rec.decision == RecoveryDecision.PROMOTED
        ):
            resolved.add(issue.id)
    return resolved


def _label(check: CheckResult) -> str:
    return f"{check.check.value}:{check.engine or 'n/a'}({check.evidence_kind.value})"


def decide_unit(
    checks: Sequence[CheckResult],
    issues: Sequence[Issue],
    recoveries: dict[str, RecoveryRecord] | None = None,
    policy: RollupPolicy = DEFAULT_POLICY,
) -> UnitDecision:
    """Apply the unit rules documented at the top of this module."""
    recoveries = recoveries or {}
    resolved = _resolved_issue_ids(issues, recoveries)
    open_issues = [i for i in issues if i.id not in resolved]

    gaps = sorted(
        {
            c.check.value
            for c in checks
            if c.outcome == CheckOutcome.NOT_VERIFIABLE
            and not any(o.check == c.check and o.outcome == CheckOutcome.PASSED for o in checks)
        }
    )
    minor = [i for i in open_issues if i.severity not in policy.review_severities | policy.fatal_severities]
    minor_note = [f"{len(minor)} informational/low issue(s) recorded; they do not change the status"] if minor else []

    # 1. FAILED
    errored = [c for c in checks if c.outcome == CheckOutcome.ERROR]
    fatal = [i for i in open_issues if i.severity in policy.fatal_severities]
    if errored or fatal:
        reasons = [f"check {c.check.value} errored: {c.summary or 'no detail'}" for c in errored]
        reasons += [f"open {i.severity.value} issue {i.code} ({i.id})" for i in fatal]
        return UnitDecision(S.FAILED, reasons + minor_note, [], gaps)

    # 2. REVIEW_REQUIRED
    unexplained = [
        c
        for c in checks
        if c.outcome == CheckOutcome.FAILED and (not c.issue_ids or not all(i in resolved for i in c.issue_ids))
    ]
    review = [i for i in open_issues if i.severity in policy.review_severities]
    if review or unexplained:
        reasons = [f"open {i.severity.value} issue {i.code} ({i.id})" for i in review]
        reasons += [
            f"check {c.check.value} disagreed with the source"
            + ("" if c.issue_ids else " and recorded no issue")
            for c in unexplained
            if not any(i.layer == c.check for i in review)
        ]
        return UnitDecision(S.REVIEW_REQUIRED, reasons + minor_note, [], gaps)

    # 3. RECOVERED
    if resolved:
        by_id = {i.id: i for i in issues}
        reasons = [
            f"issue {by_id[iid].code} ({iid}) resolved by promoted recovery {by_id[iid].recovery_id}"
            for iid in sorted(resolved)
        ]
        return UnitDecision(S.RECOVERED, reasons + minor_note, [], gaps)

    # 4. VERIFIED
    passes = [
        c
        for c in checks
        if c.outcome == CheckOutcome.PASSED
        and c.check in policy.substantive_layers
        and c.evidence_kind in policy.accepted_kinds
    ]
    if passes:
        return UnitDecision(
            S.VERIFIED,
            ["independent evidence agrees with the extraction and nothing disagrees"] + minor_note,
            [_label(c) for c in passes],
            gaps,
        )

    # 5. NOT_VERIFIABLE (say precisely why, so it is never mistaken for a pass)
    if not checks:
        why = "no checks ran for this unit"
    elif any(c.outcome == CheckOutcome.PASSED for c in checks):
        weak = [c for c in checks if c.outcome == CheckOutcome.PASSED]
        if any(c.check in policy.substantive_layers for c in weak):
            why = "content checks passed only on heuristic evidence, which is not accepted as proof"
        else:
            why = "only completeness/order/integrity checks passed; counts alone are not proof of correct content"
    else:
        why = "no usable independent evidence was available for this unit"
    return UnitDecision(S.NOT_VERIFIABLE, [why] + minor_note, [], gaps)


# ---------------------------------------------------------------------------
# Layer + document level
# ---------------------------------------------------------------------------


def summarize_layers(unit_checks: Iterable[Sequence[CheckResult]]) -> dict[CheckName, LayerSummary]:
    tally: dict[CheckName, Counter] = {}
    kinds: dict[CheckName, set[EvidenceKind]] = {}
    for checks in unit_checks:
        for c in checks:
            tally.setdefault(c.check, Counter())[c.outcome] += 1
            if c.outcome in (CheckOutcome.PASSED, CheckOutcome.FAILED):
                kinds.setdefault(c.check, set()).add(c.evidence_kind)

    layers: dict[CheckName, LayerSummary] = {}
    for name, n in tally.items():
        p, f, nv = n[CheckOutcome.PASSED], n[CheckOutcome.FAILED], n[CheckOutcome.NOT_VERIFIABLE]
        na, er = n[CheckOutcome.NOT_APPLICABLE], n[CheckOutcome.ERROR]
        if f:
            status = LayerStatus.FAILED
        elif er:
            status = LayerStatus.ERROR
        elif p and nv:
            status = LayerStatus.PARTIAL
        elif p:
            status = LayerStatus.PASSED
        elif nv:
            status = LayerStatus.NOT_VERIFIABLE
        else:
            status = LayerStatus.NOT_APPLICABLE
        layers[name] = LayerSummary(
            status=status,
            passed=p,
            failed=f,
            not_verifiable=nv,
            not_applicable=na,
            error=er,
            evidence_kinds=sorted(kinds.get(name, ()), key=lambda k: k.value),
        )
    return layers


def decide_document(
    units: Sequence[UnitResult], failure: ValidationFailure | None = None
) -> tuple[ValidationStatus, str]:
    if failure is not None:
        return S.FAILED, f"validation could not complete (stage '{failure.stage}'); extraction output is unaffected"
    if not units:
        return S.NOT_VERIFIABLE, "no validation units were produced"
    worst = max((u.status for u in units), key=STATUS_RANK.get)
    n = sum(1 for u in units if u.status == worst)
    if worst == S.VERIFIED:
        return worst, f"all {len(units)} unit(s) verified"
    return worst, f"worst unit status is {worst.value} ({n} of {len(units)} unit(s))"


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def summarize(
    units: Sequence[UnitResult], issues: Sequence[Issue], recoveries: Sequence[RecoveryRecord],
    policy: RollupPolicy = DEFAULT_POLICY,
) -> ValidationSummary:
    by_status = Counter(u.status for u in units)

    def substantive(c: CheckResult) -> bool:
        return c.check in policy.substantive_layers and c.evidence_kind in policy.accepted_kinds

    decided = [c for u in units for c in u.checks if substantive(c) and c.outcome in (CheckOutcome.PASSED, CheckOutcome.FAILED)]
    agreed = sum(1 for c in decided if c.outcome == CheckOutcome.PASSED)
    with_evidence = sum(
        1 for u in units if any(substantive(c) and c.outcome in (CheckOutcome.PASSED, CheckOutcome.FAILED) for c in u.checks)
    )
    resolved = _resolved_issue_ids(issues, {r.issue_id: r for r in recoveries})
    outcomes = Counter(c.outcome.value for u in units for c in u.checks)

    return ValidationSummary(
        units_total=len(units),
        verified=by_status[S.VERIFIED],
        recovered=by_status[S.RECOVERED],
        review_required=by_status[S.REVIEW_REQUIRED],
        failed=by_status[S.FAILED],
        not_verifiable=by_status[S.NOT_VERIFIABLE],
        units_with_evidence=with_evidence,
        evidence_coverage=_ratio(with_evidence, len(units)),
        checks_decided=len(decided),
        checks_agreed=agreed,
        agreement_rate=_ratio(agreed, len(decided)),
        check_outcomes=dict(sorted(outcomes.items())),
        issues_total=len(issues),
        issues_open=len(issues) - len(resolved),
        issues_resolved=len(resolved),
        issues_by_severity=dict(sorted(Counter(i.severity.value for i in issues).items())),
    )


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def merge_units(*groups: Iterable[UnitChecks]) -> list[UnitChecks]:
    """Combine the per-unit checks produced by different layers (integrity, completeness, ...).

    Units are matched by key; their checks are concatenated in the order the layers are given.
    """
    merged: dict[str, UnitChecks] = {}
    for group in groups:
        for uc in group:
            cur = merged.get(uc.unit.key)
            if cur is None:
                merged[uc.unit.key] = UnitChecks(unit=uc.unit, checks=list(uc.checks))
            else:
                cur.checks.extend(uc.checks)
                if cur.unit.label is None and uc.unit.label is not None:
                    cur.unit = uc.unit
    return list(merged.values())



def build_report(
    units: Sequence[UnitChecks],
    issues: Sequence[Issue] = (),
    recoveries: Sequence[RecoveryRecord] = (),
    providers: Sequence[ProviderInfo] = (),
    timings_ms: dict[str, float] | None = None,
    failure: ValidationFailure | None = None,
    policy: RollupPolicy = DEFAULT_POLICY,
) -> ValidationReport:
    """Derive every status and metric from the raw checks, issues and recoveries.

    Callers never set a status by hand; they supply evidence and this function decides.
    An issue whose unit was not listed still gets a unit, so no issue can be dropped.
    """
    ordered: dict[str, UnitChecks] = {}
    for uc in units:
        if uc.unit.key in ordered:
            raise ValueError(f"unit {uc.unit.key} listed twice")
        ordered[uc.unit.key] = uc
    for issue in issues:
        ordered.setdefault(issue.locator.unit.key, UnitChecks(unit=issue.locator.unit))

    issues_by_unit: dict[str, list[Issue]] = {k: [] for k in ordered}
    for issue in issues:
        issues_by_unit[issue.locator.unit.key].append(issue)
    recovery_by_issue = {r.issue_id: r for r in recoveries}

    results: list[UnitResult] = []
    for key, uc in ordered.items():
        unit_issues = issues_by_unit[key]
        d = decide_unit(uc.checks, unit_issues, recovery_by_issue, policy)
        results.append(
            UnitResult(
                unit=uc.unit,
                status=d.status,
                checks=list(uc.checks),
                issue_ids=[i.id for i in unit_issues],
                recovery_ids=sorted({r.id for i in unit_issues if (r := recovery_by_issue.get(i.id))}),
                status_reasons=d.reasons,
                verified_by=d.verified_by,
                gaps=d.gaps,
            )
        )

    status, reason = decide_document(results, failure)
    return ValidationReport(
        status=status,
        status_reason=reason,
        summary=summarize(results, issues, recoveries, policy),
        checks=summarize_layers(r.checks for r in results),
        units=results,
        issues=list(issues),
        recoveries=list(recoveries),
        providers=list(providers),
        timings_ms=dict(timings_ms or {}),
        policy=policy.describe(),
        failure=failure,
    )
