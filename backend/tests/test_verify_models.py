"""AXTRACT Verify: schema validation rules."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.verify_factories import DET, NONE_, check, issue, page, recovery
from verify.models import (
    MAX_EVIDENCE_CHARS,
    MAX_SNAPSHOT_CHARS,
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
    PreviewLocator,
    RecoveryDecision,
    RecoveryRecord,
    Severity,
    UnitRef,
    UnitType,
    ValidationStatus,
    bbox_problem,
    sha256_text,
)


class TestEnums:
    def test_the_five_validation_states_exist_with_stable_values(self):
        assert {s.value for s in ValidationStatus} == {
            "verified", "recovered", "review_required", "failed", "not_verifiable",
        }

    def test_evidence_kinds_cover_the_four_provenance_classes(self):
        assert {k.value for k in EvidenceKind} == {"deterministic", "model_based", "heuristic", "unavailable"}

    def test_units_are_generic_not_pdf_only(self):
        assert {"page", "slide", "sheet", "section", "region", "image", "range", "object", "document"} == {
            u.value for u in UnitType
        }

    def test_all_seven_layers_have_a_check_name(self):
        assert {c.value for c in CheckName} == {
            "completeness", "content", "structure", "reading_order", "extraction_integrity", "cross_check",
        }


class TestUnitRef:
    def test_key_is_type_and_index(self):
        assert page(3).key == "page:3"

    def test_nested_key_includes_parent(self):
        region = UnitRef(type=UnitType.REGION, index=2, parent=page(3))
        assert region.key == "page:3/region:2"

    def test_index_is_one_based(self):
        with pytest.raises(ValidationError):
            UnitRef(type=UnitType.PAGE, index=0)

    def test_is_hashable_and_equal_by_value(self):
        assert page(2) == page(2) and len({page(2), page(2), page(3)}) == 2

    def test_frozen(self):
        with pytest.raises(ValidationError):
            page(1).index = 5  # type: ignore[misc]


class TestBBox:
    @pytest.mark.parametrize("box", [None, (0, 0, 1, 1), (0.1, 0.2, 0.3, 0.4), (0.5, 0.5, 0.5, 0.5)])
    def test_valid(self, box):
        assert bbox_problem(box) is None

    @pytest.mark.parametrize(
        "box,fragment",
        [
            ((0.1, 0.2, 0.3), "exactly 4"),
            ((-0.1, 0, 0.5, 0.5), "outside"),
            ((0, 0, 1.2, 0.5), "outside"),
            ((0.6, 0.1, 0.4, 0.5), "inverted"),
            ((0.1, 0.6, 0.4, 0.5), "inverted"),
            ((float("nan"), 0, 0.5, 0.5), "non-finite"),
            ((0, 0, float("inf"), 0.5), "non-finite"),
        ],
    )
    def test_invalid(self, box, fragment):
        assert fragment in bbox_problem(box)

    def test_locator_rejects_an_invalid_bbox(self):
        with pytest.raises(ValidationError):
            Locator(unit=page(1), bbox=(0.9, 0.9, 0.1, 0.1))

    def test_preview_locator_rejects_an_invalid_bbox(self):
        with pytest.raises(ValidationError):
            PreviewLocator(page=1, bbox=(0, 0, 2, 2))

    def test_locator_allows_no_bbox_for_formats_without_geometry(self):
        loc = Locator(unit=UnitRef(type=UnitType.SHEET, index=2, label="Q3"), page=2)
        assert loc.bbox is None and loc.unit.label == "Q3"


class TestEvidence:
    def test_long_text_is_clipped_and_flagged(self):
        ev = IssueEvidence(kind=DET, engine="x", source="a" * (MAX_EVIDENCE_CHARS + 50), extracted="b")
        assert len(ev.source) == MAX_EVIDENCE_CHARS and ev.truncated is True

    def test_short_text_is_untouched(self):
        ev = IssueEvidence(kind=DET, engine="x", source="abc", extracted="abd")
        assert (ev.source, ev.extracted, ev.truncated) == ("abc", "abd", False)


class TestCheckResult:
    @pytest.mark.parametrize("outcome", [CheckOutcome.PASSED, CheckOutcome.FAILED])
    def test_a_decided_check_cannot_rest_on_unavailable_evidence(self, outcome):
        with pytest.raises(ValidationError, match="unavailable evidence"):
            CheckResult(check=CheckName.CONTENT, outcome=outcome, evidence_kind=EvidenceKind.UNAVAILABLE)

    @pytest.mark.parametrize(
        "outcome", [CheckOutcome.NOT_VERIFIABLE, CheckOutcome.NOT_APPLICABLE, CheckOutcome.ERROR]
    )
    def test_undecided_checks_may_have_no_evidence(self, outcome):
        assert CheckResult(check=CheckName.CONTENT, outcome=outcome).evidence_kind == NONE_

    def test_negative_duration_rejected(self):
        with pytest.raises(ValidationError):
            CheckResult(check=CheckName.CONTENT, outcome=CheckOutcome.NOT_VERIFIABLE, duration_ms=-1)


class TestIssue:
    def test_roundtrips_through_json(self):
        i = issue()
        assert Issue.model_validate_json(i.model_dump_json()) == i

    @pytest.mark.parametrize("code", ["Possible-Missing", "1bad", "has space", ""])
    def test_code_must_be_snake_case(self, code):
        with pytest.raises(ValidationError):
            issue(code=code)

    def test_id_required(self):
        with pytest.raises(ValidationError):
            issue(id_="")

    def test_locator_carries_what_the_viewer_needs(self):
        loc = issue().locator
        assert loc.unit.key == "page:1" and loc.page == 1 and loc.bbox and loc.block_ids == ["p1-b0"]


class TestSnapshots:
    def test_original_snapshot_hashes_the_full_text_even_when_clipped(self):
        big = "x" * (MAX_SNAPSHOT_CHARS + 10)
        snap = OriginalSnapshot(content=big)
        assert snap.truncated is True and len(snap.content) == MAX_SNAPSHOT_CHARS
        assert snap.content_sha256 == sha256_text(big)

    def test_candidate_snapshot_hash(self):
        assert CandidateSnapshot(provider="p", content="abc").content_sha256 == sha256_text("abc")


class TestRecoveryRecord:
    def test_candidate_only_needs_no_justification(self):
        rec = recovery(decision=RecoveryDecision.CANDIDATE_ONLY)
        assert rec.decision == RecoveryDecision.CANDIDATE_ONLY and not rec.promoted_block_ids

    def test_promoted_recovery_is_valid_when_fully_documented(self):
        rec = recovery()
        assert rec.decision == RecoveryDecision.PROMOTED and rec.original.content != rec.candidate.content

    @pytest.mark.parametrize(
        "kwargs,missing",
        [
            ({"comparison": []}, "comparison evidence"),
            ({"decided_by": None}, "decided_by"),
            ({"reason": "  "}, "reason"),
            ({"promoted_block_ids": []}, "promoted_block_ids"),
        ],
    )
    def test_promotion_without_an_audit_trail_is_rejected(self, kwargs, missing):
        base = recovery().model_dump()
        base.update(kwargs)
        with pytest.raises(ValidationError, match=missing):
            RecoveryRecord(**base)

    def test_original_and_candidate_are_both_kept(self):
        rec = recovery()
        assert rec.original.content == "Revenue was $13.2 million"
        assert rec.candidate.content == "Revenue was $18.2 million"

    def test_comparison_evidence_is_typed(self):
        assert ComparisonEvidence(metric="m", value="x", kind=DET).kind == DET
