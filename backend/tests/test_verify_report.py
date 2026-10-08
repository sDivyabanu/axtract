"""AXTRACT Verify: report assembly, metrics and internal consistency."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from tests.verify_factories import DET, HEUR, MODEL, check, content_pass, issue, page, recovery
from verify.models import (
    CheckName,
    CheckOutcome,
    LayerStatus,
    ProviderInfo,
    Severity,
    UnitRef,
    UnitType,
    ValidationFailure,
    ValidationReport,
    ValidationStatus,
)
from verify.rollup import UnitChecks, build_report

S = ValidationStatus
C = CheckName
O = CheckOutcome


def realistic_report() -> ValidationReport:
    """369 units: 366 verified, 2 recovered through promoted recoveries, 1 needing review."""
    units: list[UnitChecks] = [UnitChecks(page(n), [content_pass(DET), check(C.COMPLETENESS, O.PASSED)]) for n in range(1, 367)]
    issues, recoveries = [], []
    for n, (iid, rid) in zip((367, 368), (("i367", "r367"), ("i368", "r368"))):
        issues.append(issue(iid, Severity.HIGH, page(n), recovery_id=rid))
        recoveries.append(recovery(rid, iid, unit=page(n)))
        units.append(UnitChecks(page(n), [check(C.CONTENT, O.FAILED, DET, issue_ids=[iid])]))
    issues.append(issue("i369", Severity.HIGH, page(369), code="possible_missing_table", layer=C.COMPLETENESS))
    units.append(UnitChecks(page(369), [check(C.COMPLETENESS, O.FAILED, DET, issue_ids=["i369"])]))
    return build_report(
        units, issues, recoveries,
        providers=[ProviderInfo(name="pdf-crosscheck", version="1", units_checked=3)],
        timings_ms={"integrity": 1.2, "inventory": 40.0, "compare": 12.5},
    )


class TestRealisticReport:
    def test_summary_counts_are_exact(self):
        s = realistic_report().summary
        assert (s.units_total, s.verified, s.recovered, s.review_required, s.failed, s.not_verifiable) == (369, 366, 2, 1, 0, 0)

    def test_document_status_is_the_worst_unit(self):
        r = realistic_report()
        assert r.status == S.REVIEW_REQUIRED and "review_required" in r.status_reason

    def test_issue_accounting(self):
        s = realistic_report().summary
        assert (s.issues_total, s.issues_open, s.issues_resolved) == (3, 1, 2)
        assert s.issues_by_severity == {"high": 3}

    def test_measures_are_ratios_of_raw_counts_not_accuracy(self):
        s = realistic_report().summary
        # content-bearing, accepted-evidence checks: 366 passed + 2 failed. The completeness failure
        # on page 369 is a counts-level check, so it is excluded from both measures.
        assert (s.checks_decided, s.checks_agreed) == (368, 366)
        assert s.agreement_rate == round(366 / 368, 4)
        assert (s.units_with_evidence, s.evidence_coverage) == (368, round(368 / 369, 4))
        assert not any("accuracy" in k or "confidence" in k for k in s.model_dump())

    def test_a_recovered_unit_explains_itself(self):
        r = realistic_report()
        u = next(u for u in r.units if u.unit.key == "page:367")
        assert u.status == S.RECOVERED and u.recovery_ids == ["r367"] and "r367" in u.status_reasons[0]

    def test_a_verified_unit_names_the_evidence(self):
        u = realistic_report().units[0]
        assert u.status == S.VERIFIED and u.verified_by == ["content:test-engine(deterministic)"]

    def test_review_unit_points_to_its_issue(self):
        r = realistic_report()
        u = next(u for u in r.units if u.status == S.REVIEW_REQUIRED)
        assert u.issue_ids == ["i369"] and "possible_missing_table" in u.status_reasons[0]

    def test_layers_are_summarised(self):
        layers = realistic_report().checks
        assert layers[C.CONTENT].status == LayerStatus.FAILED
        assert layers[C.COMPLETENESS].status == LayerStatus.FAILED

    def test_policy_providers_and_timings_are_carried(self):
        r = realistic_report()
        assert r.policy["accepted_evidence_kinds"] == ["deterministic", "model_based"]
        assert r.providers[0].name == "pdf-crosscheck" and r.timings_ms["inventory"] == 40.0

    def test_json_roundtrip_is_lossless(self):
        r = realistic_report()
        assert ValidationReport.model_validate_json(r.model_dump_json()) == r

    def test_json_uses_plain_strings_for_enums_and_keys(self):
        d = realistic_report().model_dump(mode="json")
        assert d["status"] == "review_required" and d["units"][0]["status"] == "verified"
        assert "completeness" in d["checks"] and d["issues"][0]["severity"] == "high"


class TestMetricsWithoutEvidence:
    def test_no_units_gives_null_measures_not_zero(self):
        r = build_report([])
        assert r.status == S.NOT_VERIFIABLE
        assert r.summary.evidence_coverage is None and r.summary.agreement_rate is None

    def test_all_unverifiable_has_zero_coverage_and_null_agreement(self):
        r = build_report([UnitChecks(page(n), [check(C.CONTENT, O.NOT_VERIFIABLE)]) for n in (1, 2)])
        assert r.status == S.NOT_VERIFIABLE
        assert r.summary.evidence_coverage == 0.0 and r.summary.agreement_rate is None
        assert r.summary.not_verifiable == 2

    def test_heuristic_evidence_does_not_inflate_the_measures(self):
        r = build_report([UnitChecks(page(1), [content_pass(HEUR)]), UnitChecks(page(2), [content_pass(MODEL)])])
        assert r.summary.checks_decided == 1 and r.summary.evidence_coverage == 0.5

    def test_mixed_verified_and_unverifiable_is_not_reported_as_verified(self):
        r = build_report([UnitChecks(page(1), [content_pass()]), UnitChecks(page(2), [])])
        assert r.status == S.NOT_VERIFIABLE and (r.summary.verified, r.summary.not_verifiable) == (1, 1)


class TestProcessingVsValidation:
    def test_a_fully_processed_document_can_still_require_review(self):
        # The extractor finished (that is DocumentResponse.status) yet validation does not trust it.
        r = build_report([UnitChecks(page(1), [content_pass()])], [issue("i1", Severity.MEDIUM)])
        assert r.status == S.REVIEW_REQUIRED

    def test_validation_failure_is_reported_without_claiming_anything_about_extraction(self):
        r = build_report([], failure=ValidationFailure(stage="inventory", error_type="MemoryError", message="oom"))
        assert r.status == S.FAILED and r.failure.stage == "inventory" and r.summary.verified == 0

    def test_failure_with_partial_results_is_still_failed(self):
        r = build_report([UnitChecks(page(1), [content_pass()])], failure=ValidationFailure(stage="compare", error_type="X"))
        assert r.status == S.FAILED and r.units[0].status == S.VERIFIED


class TestAssembly:
    def test_an_issue_is_never_dropped_even_if_its_unit_was_not_listed(self):
        r = build_report([UnitChecks(page(1), [content_pass()])], [issue("i9", unit=page(7))])
        keys = [u.unit.key for u in r.units]
        assert keys == ["page:1", "page:7"] and r.units[1].issue_ids == ["i9"] and r.status == S.REVIEW_REQUIRED

    def test_unit_order_follows_the_input(self):
        r = build_report([UnitChecks(page(n), [content_pass()]) for n in (3, 1, 2)])
        assert [u.unit.index for u in r.units] == [3, 1, 2]

    def test_non_pdf_units_work_the_same_way(self):
        sheet = UnitRef(type=UnitType.SHEET, index=2, label="Q3")
        r = build_report([UnitChecks(sheet, [check(C.CONTENT, O.PASSED, DET, engine="ooxml:sheet-xml")])])
        assert r.units[0].unit.label == "Q3" and r.status == S.VERIFIED

    def test_listing_a_unit_twice_is_an_error(self):
        with pytest.raises(ValueError, match="listed twice"):
            build_report([UnitChecks(page(1)), UnitChecks(page(1))])

    def test_inputs_are_not_mutated(self):
        checks = [content_pass()]
        before = copy.deepcopy(checks)
        build_report([UnitChecks(page(1), checks)])
        assert checks == before

    def test_building_twice_gives_the_same_report(self):
        assert realistic_report() == realistic_report()

    def test_issue_pointing_at_an_unknown_recovery_is_rejected(self):
        with pytest.raises(ValidationError, match="unknown recovery"):
            build_report([], [issue("i1", recovery_id="nope")])

    def test_check_pointing_at_an_unknown_issue_is_rejected(self):
        with pytest.raises(ValidationError, match="unknown issue"):
            build_report([UnitChecks(page(1), [check(C.CONTENT, O.FAILED, issue_ids=["ghost"])])])

    def test_recovery_pointing_at_an_unknown_issue_is_rejected(self):
        with pytest.raises(ValidationError, match="unknown issue"):
            build_report([UnitChecks(page(1))], [], [recovery("r1", "ghost")])

    def test_duplicate_issue_ids_are_rejected(self):
        with pytest.raises(ValidationError, match="duplicate issue id"):
            build_report([], [issue("i1"), issue("i1")])


class TestConsistencyGuards:
    """A report that was edited by hand into a contradiction must not validate."""

    def _tamper(self, **changes):
        data = realistic_report().model_dump()
        data.update(changes)
        return ValidationReport.model_validate(data)

    def test_summary_total_must_match_units(self):
        data = realistic_report().model_dump()
        data["summary"]["units_total"] = 5
        with pytest.raises(ValidationError, match="units_total"):
            ValidationReport.model_validate(data)

    def test_status_counts_must_match_units(self):
        data = realistic_report().model_dump()
        data["summary"]["verified"] = 369
        with pytest.raises(ValidationError, match="summary.verified"):
            ValidationReport.model_validate(data)

    def test_a_failure_cannot_coexist_with_a_non_failed_status(self):
        with pytest.raises(ValidationError, match="status 'failed'"):
            self._tamper(failure={"stage": "x", "error_type": "E", "message": ""})

    def test_an_issue_must_belong_to_its_unit(self):
        data = realistic_report().model_dump()
        data["units"][0]["issue_ids"] = []
        data["units"][-1]["issue_ids"] = []
        with pytest.raises(ValidationError, match="not attached to its unit"):
            ValidationReport.model_validate(data)
