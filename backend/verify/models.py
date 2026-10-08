"""AXTRACT Verify: validation data model.

Schema only. Nothing in this module inspects a document; the checks that produce these
objects arrive in later steps, and the state machine that derives statuses is in `rollup.py`.

Principles encoded here:
  * Processing success and validation success are separate. `DocumentResponse.status`
    says whether extraction ran; `ValidationReport.status` says whether the result is trusted.
  * Evidence is always labelled with how it was obtained (EvidenceKind). A PASSED or FAILED
    check cannot claim `unavailable` evidence.
  * Nothing is silently accepted: no evidence means NOT_VERIFIABLE, never VERIFIED.
  * No composite "accuracy" or "confidence" number exists. Reports carry raw counts plus
    `evidence_coverage` and `agreement_rate`, which are validation measures, not accuracy.
  * Recovery keeps an audit trail: what was originally extracted, the secondary candidate,
    the comparison evidence, and an explicit decision. Original blocks are never overwritten.
"""

from __future__ import annotations

import hashlib
import math
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from models.document import BBox

SCHEMA_VERSION = "1.0"

# Free text carried in a report (evidence, snapshots) is clipped so reports stay small and
# a hostile document cannot bloat the response. `truncated` records that it happened.
MAX_EVIDENCE_CHARS = 2000
MAX_SNAPSHOT_CHARS = 4000


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ValidationStatus(StrEnum):
    """Trust state of a unit or of the whole document."""

    VERIFIED = "verified"  # independent evidence agrees, nothing disagrees
    RECOVERED = "recovered"  # a problem was found and fixed through an explicit recovery
    REVIEW_REQUIRED = "review_required"  # evidence of a problem, or an unresolved disagreement
    FAILED = "failed"  # an extraction or validation stage failed
    NOT_VERIFIABLE = "not_verifiable"  # not enough independent evidence to say either way


class EvidenceKind(StrEnum):
    """How a piece of evidence was obtained. Heuristics are never presented as ground truth."""

    DETERMINISTIC = "deterministic"  # read directly from the file structure
    MODEL_BASED = "model_based"  # produced by a model / second extraction engine
    HEURISTIC = "heuristic"  # inferred; may be wrong
    UNAVAILABLE = "unavailable"  # no usable evidence


class UnitType(StrEnum):
    """What a validation unit is. Generic on purpose: not everything is a PDF page."""

    DOCUMENT = "document"
    PAGE = "page"
    SLIDE = "slide"
    SHEET = "sheet"
    RANGE = "range"
    OBJECT = "object"
    SECTION = "section"
    REGION = "region"
    IMAGE = "image"


class CheckName(StrEnum):
    """The validation layers. Layer 1 (source evidence) feeds these; it is not a check."""

    COMPLETENESS = "completeness"
    CONTENT = "content"
    STRUCTURE = "structure"
    READING_ORDER = "reading_order"
    EXTRACTION_INTEGRITY = "extraction_integrity"
    CROSS_CHECK = "cross_check"  # independent second opinion (layer 7)


class CheckOutcome(StrEnum):
    PASSED = "passed"  # evidence examined and it agrees
    FAILED = "failed"  # evidence examined and it disagrees
    NOT_VERIFIABLE = "not_verifiable"  # applicable, but no usable evidence
    NOT_APPLICABLE = "not_applicable"  # this check does not apply to this unit
    ERROR = "error"  # the check itself crashed or timed out


class LayerStatus(StrEnum):
    """Roll-up of one check layer across every unit."""

    PASSED = "passed"
    FAILED = "failed"
    PARTIAL = "partial"  # some units passed, others could not be verified
    NOT_VERIFIABLE = "not_verifiable"
    ERROR = "error"
    NOT_APPLICABLE = "not_applicable"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"  # the extraction or validation is unusable for this unit


class RecommendedAction(StrEnum):
    NONE = "none"
    REVIEW = "review"
    SECONDARY_EXTRACTION = "secondary_extraction"
    REEXTRACT = "reextract"
    MANUAL = "manual"


class RecoveryDecision(StrEnum):
    CANDIDATE_ONLY = "candidate_only"  # a candidate exists; the original is still what is served
    PROMOTED = "promoted"  # candidate explicitly accepted as the recovered content
    REJECTED = "rejected"  # candidate examined and not accepted


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def clip(text: str | None, limit: int) -> tuple[str | None, bool]:
    """Return (text clipped to `limit` characters, whether anything was cut)."""
    if text is None or len(text) <= limit:
        return text, False
    return text[:limit], True


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bbox_problem(bbox: BBox | tuple[float, ...] | None) -> str | None:
    """Why a normalised [x1, y1, x2, y2] box is invalid, or None if it is fine (or absent).

    Shared by the Locator validator and the extraction-integrity checks so both agree on
    what "invalid bbox" means.
    """
    if bbox is None:
        return None
    if len(bbox) != 4:
        return "bbox must have exactly 4 values"
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in bbox):
        return "bbox contains a non-finite or non-numeric value"
    x1, y1, x2, y2 = bbox
    if min(bbox) < 0.0 or max(bbox) > 1.0:
        return "bbox is outside the normalised 0..1 range"
    if x1 > x2 or y1 > y2:
        return "bbox corners are inverted (x1>x2 or y1>y2)"
    return None


# ---------------------------------------------------------------------------
# Units, locators, evidence
# ---------------------------------------------------------------------------


class UnitRef(BaseModel):
    """A reference to one validated unit, 1-based like `DocumentBlock.page`.

    PDF page / PPTX slide / XLSX sheet / DOCX section / image / region. A region (or a range
    or object) can name the unit that contains it through `parent`.
    """

    model_config = ConfigDict(frozen=True)

    type: UnitType
    index: int = Field(ge=1)
    label: str | None = None  # e.g. a sheet name; display only
    parent: UnitRef | None = None

    @property
    def key(self) -> str:
        own = f"{self.type.value}:{self.index}"
        return f"{self.parent.key}/{own}" if self.parent else own


class PreviewLocator(BaseModel):
    """Where to draw the source in the existing viewer (mirrors `metadata.preview`)."""

    page: int = Field(ge=1)
    bbox: BBox | None = None
    approximate: bool = False

    @field_validator("bbox")
    @classmethod
    def _check_bbox(cls, v: BBox | None) -> BBox | None:
        problem = bbox_problem(v)
        if problem:
            raise ValueError(problem)
        return v


class Locator(BaseModel):
    """Everything the UI needs to open an issue at its source location."""

    unit: UnitRef
    page: int | None = Field(default=None, ge=1)
    bbox: BBox | None = None
    block_ids: list[str] = Field(default_factory=list)
    preview: PreviewLocator | None = None

    @field_validator("bbox")
    @classmethod
    def _check_bbox(cls, v: BBox | None) -> BBox | None:
        problem = bbox_problem(v)
        if problem:
            raise ValueError(problem)
        return v


class IssueEvidence(BaseModel):
    """One observation backing an issue: what the source shows vs what AXTRACT extracted."""

    kind: EvidenceKind
    engine: str  # who produced the source-side value, e.g. "pypdfium2" or "ooxml:sheet-xml"
    source: str | None = None  # value observed in the original document
    extracted: str | None = None  # value found in AXTRACT output
    detail: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False

    @model_validator(mode="after")
    def _clip(self) -> IssueEvidence:
        self.source, cut_a = clip(self.source, MAX_EVIDENCE_CHARS)
        self.extracted, cut_b = clip(self.extracted, MAX_EVIDENCE_CHARS)
        self.truncated = self.truncated or cut_a or cut_b
        return self


class CheckResult(BaseModel):
    """The outcome of one validation layer on one unit."""

    check: CheckName
    outcome: CheckOutcome
    evidence_kind: EvidenceKind = EvidenceKind.UNAVAILABLE
    engine: str | None = None
    summary: str = ""
    counts: dict[str, int] = Field(default_factory=dict)  # e.g. {"source_tables": 3, "output_tables": 2}
    issue_ids: list[str] = Field(default_factory=list)
    duration_ms: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def _decided_checks_need_evidence(self) -> CheckResult:
        if self.outcome in (CheckOutcome.PASSED, CheckOutcome.FAILED) and self.evidence_kind == EvidenceKind.UNAVAILABLE:
            raise ValueError(f"a {self.outcome.value} check cannot rest on unavailable evidence")
        return self


class Issue(BaseModel):
    id: str = Field(min_length=1)
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    severity: Severity
    layer: CheckName
    message: str
    locator: Locator
    evidence: list[IssueEvidence] = Field(default_factory=list)
    recommended_action: RecommendedAction = RecommendedAction.REVIEW
    recovery_id: str | None = None  # set when a recovery record addresses this issue


# ---------------------------------------------------------------------------
# Recovery audit trail (schema only; the mechanism arrives in a later step)
# ---------------------------------------------------------------------------


class OriginalSnapshot(BaseModel):
    """What AXTRACT originally extracted. Kept verbatim; never replaced by a candidate."""

    block_ids: list[str] = Field(default_factory=list)
    content: str = ""
    content_sha256: str = ""
    truncated: bool = False

    @model_validator(mode="after")
    def _seal(self) -> OriginalSnapshot:
        if not self.content_sha256:
            self.content_sha256 = sha256_text(self.content)
        self.content, cut = clip(self.content, MAX_SNAPSHOT_CHARS)
        self.content = self.content or ""
        self.truncated = self.truncated or cut
        return self


class CandidateSnapshot(BaseModel):
    """What a secondary extractor produced for the same unit."""

    provider: str
    content: str = ""
    content_sha256: str = ""
    structure: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False

    @model_validator(mode="after")
    def _seal(self) -> CandidateSnapshot:
        if not self.content_sha256:
            self.content_sha256 = sha256_text(self.content)
        self.content, cut = clip(self.content, MAX_SNAPSHOT_CHARS)
        self.content = self.content or ""
        self.truncated = self.truncated or cut
        return self


class ComparisonEvidence(BaseModel):
    metric: str  # e.g. "normalized_text_similarity"
    value: float | str | None = None
    kind: EvidenceKind
    note: str = ""


class RecoveryRecord(BaseModel):
    """Audit record linking an issue, the original, a secondary candidate and a decision."""

    id: str = Field(min_length=1)
    issue_id: str = Field(min_length=1)
    unit: UnitRef
    provider: str
    original: OriginalSnapshot
    candidate: CandidateSnapshot
    comparison: list[ComparisonEvidence] = Field(default_factory=list)
    decision: RecoveryDecision = RecoveryDecision.CANDIDATE_ONLY
    decided_by: Literal["policy", "explicit_request", "reviewer"] | None = None
    reason: str = ""
    promoted_block_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _promotion_must_be_explicit(self) -> RecoveryRecord:
        if self.decision == RecoveryDecision.PROMOTED:
            missing = [
                name
                for name, ok in (
                    ("comparison evidence", bool(self.comparison)),
                    ("decided_by", self.decided_by is not None),
                    ("reason", bool(self.reason.strip())),
                    ("promoted_block_ids", bool(self.promoted_block_ids)),
                )
                if not ok
            ]
            if missing:
                raise ValueError("a promoted recovery must record: " + ", ".join(missing))
        return self


# ---------------------------------------------------------------------------
# Results and report
# ---------------------------------------------------------------------------


class UnitResult(BaseModel):
    unit: UnitRef
    status: ValidationStatus
    checks: list[CheckResult] = Field(default_factory=list)
    issue_ids: list[str] = Field(default_factory=list)
    recovery_ids: list[str] = Field(default_factory=list)
    status_reasons: list[str] = Field(default_factory=list)  # why this status
    verified_by: list[str] = Field(default_factory=list)  # evidence that supports trust
    gaps: list[str] = Field(default_factory=list)  # layers that could not be verified


class LayerSummary(BaseModel):
    status: LayerStatus
    passed: int = 0
    failed: int = 0
    not_verifiable: int = 0
    not_applicable: int = 0
    error: int = 0
    evidence_kinds: list[EvidenceKind] = Field(default_factory=list)


METRIC_DEFINITIONS: dict[str, str] = {
    "evidence_coverage": (
        "units_with_evidence / units_total, where a unit has evidence when at least one content-bearing "
        "check (content, structure, cross_check) reached PASSED or FAILED using deterministic or model-based "
        "evidence. null when there are no units. A validation measure, not accuracy."
    ),
    "agreement_rate": (
        "checks_agreed / checks_decided over the same content-bearing, deterministic or model-based checks. "
        "null when no check was decided. Says how often independent evidence agreed with the extraction, "
        "not how accurate the extraction is."
    ),
}


class ValidationSummary(BaseModel):
    units_total: int = 0
    verified: int = 0
    recovered: int = 0
    review_required: int = 0
    failed: int = 0
    not_verifiable: int = 0
    units_with_evidence: int = 0
    evidence_coverage: float | None = None
    checks_decided: int = 0
    checks_agreed: int = 0
    agreement_rate: float | None = None
    check_outcomes: dict[str, int] = Field(default_factory=dict)
    issues_total: int = 0
    issues_open: int = 0
    issues_resolved: int = 0
    issues_by_severity: dict[str, int] = Field(default_factory=dict)
    definitions: dict[str, str] = Field(default_factory=lambda: dict(METRIC_DEFINITIONS))


class ProviderInfo(BaseModel):
    name: str
    version: str | None = None
    tier: Literal["builtin", "heavy"] = "builtin"
    available: bool = True
    units_checked: int = Field(default=0, ge=0)
    note: str = ""


class ValidationFailure(BaseModel):
    """Validation itself could not complete. Extraction output is unaffected."""

    stage: str
    error_type: str
    message: str = ""


class ValidationReport(BaseModel):
    schema_version: str = SCHEMA_VERSION
    status: ValidationStatus
    status_reason: str = ""
    summary: ValidationSummary
    checks: dict[CheckName, LayerSummary] = Field(default_factory=dict)
    units: list[UnitResult] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    recoveries: list[RecoveryRecord] = Field(default_factory=list)
    providers: list[ProviderInfo] = Field(default_factory=list)
    timings_ms: dict[str, float] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)  # the rollup rules that produced the statuses
    failure: ValidationFailure | None = None

    @model_validator(mode="after")
    def _internally_consistent(self) -> ValidationReport:
        def unique(items: list[str], what: str) -> set[str]:
            seen = set(items)
            if len(seen) != len(items):
                raise ValueError(f"duplicate {what}")
            return seen

        issue_ids = unique([i.id for i in self.issues], "issue id")
        recovery_ids = unique([r.id for r in self.recoveries], "recovery id")
        unit_keys = unique([u.unit.key for u in self.units], "unit")
        by_unit = {u.unit.key: u for u in self.units}

        for issue in self.issues:
            owner = by_unit.get(issue.locator.unit.key)
            if owner is None or issue.id not in owner.issue_ids:
                raise ValueError(f"issue {issue.id} is not attached to its unit")
            if issue.recovery_id is not None and issue.recovery_id not in recovery_ids:
                raise ValueError(f"issue {issue.id} references unknown recovery {issue.recovery_id}")
        for rec in self.recoveries:
            if rec.issue_id not in issue_ids:
                raise ValueError(f"recovery {rec.id} references unknown issue {rec.issue_id}")
        for u in self.units:
            for iid in u.issue_ids:
                if iid not in issue_ids:
                    raise ValueError(f"unit {u.unit.key} references unknown issue {iid}")
            for iid in (i for c in u.checks for i in c.issue_ids):
                if iid not in issue_ids:
                    raise ValueError(f"a check on {u.unit.key} references unknown issue {iid}")
            for rid in u.recovery_ids:
                if rid not in recovery_ids:
                    raise ValueError(f"unit {u.unit.key} references unknown recovery {rid}")

        if self.summary.units_total != len(unit_keys):
            raise ValueError("summary.units_total does not match the units list")
        for status in ValidationStatus:
            counted = sum(1 for u in self.units if u.status == status)
            if getattr(self.summary, status.value) != counted:
                raise ValueError(f"summary.{status.value} does not match the units list")
        if self.failure is not None and self.status != ValidationStatus.FAILED:
            raise ValueError("a report with a failure must have status 'failed'")
        return self


UnitRef.model_rebuild()
