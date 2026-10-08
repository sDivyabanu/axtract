"""Shared plumbing for the check layers (content, structure, reading order).

A layer records what it EXAMINED (per unit and evidence kind), what it FOUND, and the cases where
it could not check at all. LayerBuilder turns that into issues and per-unit CheckResults the same
way for every layer, so statuses and reports are consistent:

  * a unit/kind that was examined and has no medium-or-worse finding  -> PASSED
  * any medium/high/critical finding                                  -> FAILED (info/low never fail a check)
  * a case where no (independent) evidence exists                     -> NOT_VERIFIABLE, with the reason
  * a layer that does not apply (reading order of a spreadsheet)      -> NOT_APPLICABLE

Issues are capped per (unit, code) so one broken document cannot flood a report; the overflow is
counted in the last issue's evidence.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from verify.ids import IdSequence
from verify.models import (
    CheckName, CheckOutcome, CheckResult, EvidenceKind, Issue, IssueEvidence, Locator, RecommendedAction,
    Severity, UnitRef, UnitType, bbox_problem,
)
from verify.rollup import UnitChecks

MAX_ISSUES_PER_UNIT_CODE = 20
_REVIEW_OR_WORSE = {Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL}
_PAGE_LIKE = (UnitType.PAGE, UnitType.SLIDE, UnitType.SHEET, UnitType.SECTION, UnitType.IMAGE)


@dataclass
class LayerOutcome:
    units: list[UnitChecks]
    issues: list[Issue]
    duration_ms: float
    stats: dict[str, int]
    notes: list[str] = field(default_factory=list)


@dataclass
class _Finding:
    unit: UnitRef
    code: str
    severity: Severity
    kind: EvidenceKind
    engine: str
    message: str
    source: str | None
    extracted: str | None
    detail: dict[str, Any]
    block_ids: list[str]
    bbox: tuple | None
    page: int | None
    action: RecommendedAction
    order: int


class LayerBuilder:
    def __init__(self, layer: CheckName, catalog: dict, ids: IdSequence | None = None):
        self.layer, self.catalog = layer, catalog
        self.ids = ids or IdSequence("iss")
        self._t0 = time.perf_counter()
        self._findings: list[_Finding] = []
        self._examined: dict[str, Counter] = defaultdict(Counter)
        self._counts: dict[str, Counter] = defaultdict(Counter)
        self._notes: dict[str, list[tuple[CheckOutcome, str, str, EvidenceKind]]] = defaultdict(list)
        self._units: dict[str, UnitRef] = {}
        self._n = 0
        self.notes: list[str] = []

    # -- recording --------------------------------------------------------------------------------
    def unit(self, u: UnitRef) -> UnitRef:
        self._units.setdefault(u.key, u)
        return u

    def examine(self, u: UnitRef, kind: EvidenceKind, n: int = 1, **counts: int) -> None:
        self.unit(u)
        self._examined[u.key][kind] += n
        for k, v in counts.items():
            self._counts[u.key][k] += v

    def note(self, u: UnitRef, outcome: CheckOutcome, summary: str, engine: str, kind: EvidenceKind = EvidenceKind.UNAVAILABLE) -> None:
        self.unit(u)
        self._notes[u.key].append((outcome, engine, summary, kind))

    def find(self, u: UnitRef, code: str, message: str, *, kind: EvidenceKind, engine: str, severity: Severity | None = None,
             source: str | None = None, extracted: str | None = None, detail: dict | None = None,
             block_ids: list[str] | None = None, bbox=None, action: RecommendedAction = RecommendedAction.REVIEW) -> None:
        self.unit(u)
        self._n += 1
        page = u.index if u.type in _PAGE_LIKE else None
        self._findings.append(_Finding(u, code, severity or self.catalog[code][0], kind, engine, message, source, extracted,
                                       detail or {}, list(block_ids or []), bbox, page, action, self._n))

    # -- output -----------------------------------------------------------------------------------
    def build(self) -> LayerOutcome:
        order = {k: i for i, k in enumerate(self._units)}
        self._findings.sort(key=lambda f: (f.unit.type != UnitType.DOCUMENT, order[f.unit.key], f.code, f.order))
        issues: list[Issue] = []
        by_unit: dict[str, list[tuple[EvidenceKind, Issue]]] = defaultdict(list)
        seen: Counter = Counter()
        last: dict[tuple[str, str], Issue] = {}
        for f in self._findings:
            k = (f.unit.key, f.code)
            seen[k] += 1
            if seen[k] > MAX_ISSUES_PER_UNIT_CODE:
                last[k].evidence[0].detail["additional_not_itemised"] = seen[k] - MAX_ISSUES_PER_UNIT_CODE
                continue
            bbox = f.bbox if f.bbox and bbox_problem(tuple(f.bbox)) is None else None
            issue = Issue(
                id=self.ids.next(), code=f.code, severity=f.severity, layer=self.layer, message=f.message,
                locator=Locator(unit=f.unit, page=f.page, bbox=bbox, block_ids=f.block_ids[:50]),
                evidence=[IssueEvidence(kind=f.kind, engine=f.engine, source=f.source, extracted=f.extracted or "(nothing)",
                                        detail=f.detail)],
                recommended_action=f.action)
            last[k] = issue
            issues.append(issue)
            by_unit[f.unit.key].append((f.kind, issue))

        units: list[UnitChecks] = []
        for key, u in self._units.items():
            checks: list[CheckResult] = []
            for kind in (EvidenceKind.DETERMINISTIC, EvidenceKind.MODEL_BASED, EvidenceKind.HEURISTIC):
                mine = [i for k, i in by_unit.get(key, []) if k == kind]
                n = self._examined[key][kind]
                if not n and not mine:
                    continue
                blocking = [i for i in mine if i.severity in _REVIEW_OR_WORSE]
                checks.append(CheckResult(
                    check=self.layer, outcome=CheckOutcome.FAILED if blocking else CheckOutcome.PASSED, evidence_kind=kind,
                    engine=f"{self.layer.value}.{kind.value}",
                    summary=(f"{len(blocking)} disagreement(s) with the independent source evidence" if blocking else
                             f"independent {kind.value} evidence agrees on everything examined"),
                    counts={"examined": n, "issues": len(mine), **dict(self._counts[key])}, issue_ids=[i.id for i in mine]))
            for outcome, engine, summary, kind in self._notes.get(key, []):
                checks.append(CheckResult(check=self.layer, outcome=outcome, evidence_kind=kind, engine=engine, summary=summary))
            units.append(UnitChecks(unit=u, checks=checks))
        return LayerOutcome(
            units=units, issues=issues, duration_ms=round((time.perf_counter() - self._t0) * 1000, 3),
            stats={"issues": len(issues), "units": len(units)}, notes=self.notes)
