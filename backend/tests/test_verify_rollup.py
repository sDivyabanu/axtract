"""AXTRACT Verify: unit, layer and document rollup rules."""

from __future__ import annotations

import pytest

from tests.verify_factories import DET, HEUR, MODEL, check, content_pass, issue, page, recovery
from verify.models import (
    CheckName,
    CheckOutcome,
    LayerStatus,
    RecoveryDecision,
    Severity,
    UnitResult,
    ValidationFailure,
    ValidationStatus,
)
from verify.rollup import DEFAULT_POLICY, STATUS_RANK, RollupPolicy, decide_document, decide_unit, summarize_layers

S = ValidationStatus
C = CheckName
O = CheckOutcome


def status(checks=(), issues=(), recoveries=()):
    rec = {r.issue_id: r for r in recoveries}
    return decide_unit(list(checks), list(issues), rec)


class TestVerified:
    def test_deterministic_content_pass(self):
        d = status([content_pass(DET)])
        assert d.status == S.VERIFIED
        assert d.verified_by == ["content:test-engine(deterministic)"]

    def test_model_based_content_pass(self):
        assert status([content_pass(MODEL)]).status == S.VERIFIED

    @pytest.mark.parametrize("layer", [C.STRUCTURE, C.CROSS_CHECK])
    def test_other_content_bearing_layers_can_verify(self, layer):
        assert status([check(layer, O.PASSED, DET)]).status == S.VERIFIED

    def test_every_supporting_check_is_listed_as_evidence(self):
        d = status([content_pass(DET), check(C.STRUCTURE, O.PASSED, MODEL, engine="docling")])
        assert d.verified_by == ["content:test-engine(deterministic)", "structure:docling(model_based)"]

    def test_unverifiable_layer_next_to_a_pass_is_reported_as_a_gap_not_hidden(self):
        d = status([content_pass(), check(C.READING_ORDER, O.NOT_VERIFIABLE)])
        assert d.status == S.VERIFIED and d.gaps == ["reading_order"]

    def test_low_and_info_issues_are_recorded_but_do_not_block(self):
        d = status([content_pass()], [issue("a", Severity.LOW), issue("b", Severity.INFO)])
        assert d.status == S.VERIFIED and any("2 informational/low" in r for r in d.reasons)


class TestNotVerifiable:
    def test_no_checks_at_all(self):
        d = status()
        assert d.status == S.NOT_VERIFIABLE and "no checks ran" in d.reasons[0]

    def test_counts_alone_are_not_proof(self):
        d = status([check(C.COMPLETENESS, O.PASSED), check(C.READING_ORDER, O.PASSED), check(C.EXTRACTION_INTEGRITY, O.PASSED)])
        assert d.status == S.NOT_VERIFIABLE and "counts alone" in d.reasons[0]

    def test_heuristic_content_pass_is_not_accepted_as_proof(self):
        d = status([content_pass(HEUR)])
        assert d.status == S.NOT_VERIFIABLE and "heuristic" in d.reasons[0]

    def test_every_check_lacked_evidence(self):
        d = status([check(C.CONTENT, O.NOT_VERIFIABLE), check(C.STRUCTURE, O.NOT_VERIFIABLE)])
        assert d.status == S.NOT_VERIFIABLE and d.gaps == ["content", "structure"]

    def test_not_applicable_checks_prove_nothing(self):
        assert status([check(C.CONTENT, O.NOT_APPLICABLE)]).status == S.NOT_VERIFIABLE


class TestReviewRequired:
    def test_a_failed_check_beats_a_passing_check(self):
        i = issue("i1", Severity.HIGH)
        d = status([content_pass(), check(C.STRUCTURE, O.FAILED, issue_ids=["i1"])], [i])
        assert d.status == S.REVIEW_REQUIRED

    @pytest.mark.parametrize("sev", [Severity.MEDIUM, Severity.HIGH])
    def test_open_medium_or_high_issue(self, sev):
        assert status([content_pass()], [issue(severity=sev)]).status == S.REVIEW_REQUIRED

    def test_failed_check_without_an_issue_still_requires_review(self):
        d = status([check(C.CONTENT, O.FAILED)])
        assert d.status == S.REVIEW_REQUIRED and "recorded no issue" in " ".join(d.reasons)

    def test_unpromoted_candidate_does_not_resolve_anything(self):
        for decision in (RecoveryDecision.CANDIDATE_ONLY, RecoveryDecision.REJECTED):
            i = issue("i1", recovery_id="r1")
            d = status([content_pass()], [i], [recovery("r1", "i1", decision)])
            assert d.status == S.REVIEW_REQUIRED, decision

    def test_a_recovery_id_with_no_matching_record_resolves_nothing(self):
        assert status([content_pass()], [issue("i1", recovery_id="ghost")]).status == S.REVIEW_REQUIRED

    def test_recovery_for_a_different_issue_does_not_resolve_this_one(self):
        d = status([content_pass()], [issue("i1", recovery_id="r1")], [recovery("r1", "other")])
        assert d.status == S.REVIEW_REQUIRED


class TestRecovered:
    def test_promoted_recovery_resolves_the_issue(self):
        i = issue("i1", recovery_id="r1")
        d = status([check(C.CONTENT, O.FAILED, issue_ids=["i1"])], [i], [recovery("r1", "i1")])
        assert d.status == S.RECOVERED and "r1" in d.reasons[0]

    def test_recovery_does_not_hide_a_second_open_problem(self):
        fixed, other = issue("i1", recovery_id="r1"), issue("i2", Severity.HIGH, code="possible_missing_table")
        assert status([], [fixed, other], [recovery("r1", "i1")]).status == S.REVIEW_REQUIRED

    def test_recovery_does_not_hide_a_critical_problem(self):
        fixed, other = issue("i1", recovery_id="r1"), issue("i2", Severity.CRITICAL, code="extractor_exception")
        assert status([], [fixed, other], [recovery("r1", "i1")]).status == S.FAILED

    def test_recovered_ranks_above_a_plain_pass(self):
        i = issue("i1", recovery_id="r1")
        d = status([content_pass()], [i], [recovery("r1", "i1")])
        assert d.status == S.RECOVERED


class TestFailed:
    def test_a_crashed_check_fails_the_unit(self):
        d = status([content_pass(), check(C.STRUCTURE, O.ERROR, summary="boom")])
        assert d.status == S.FAILED and "boom" in d.reasons[0]

    def test_open_critical_issue(self):
        assert status([content_pass()], [issue(severity=Severity.CRITICAL)]).status == S.FAILED

    def test_failed_outranks_review_required(self):
        d = status([], [issue("a", Severity.HIGH), issue("b", Severity.CRITICAL)])
        assert d.status == S.FAILED

    def test_a_promoted_recovery_clears_a_critical_issue(self):
        i = issue("i1", Severity.CRITICAL, recovery_id="r1")
        assert status([], [i], [recovery("r1", "i1")]).status == S.RECOVERED


class TestPolicy:
    def test_default_policy_is_echoable_and_stable(self):
        d = DEFAULT_POLICY.describe()
        assert d["accepted_evidence_kinds"] == ["deterministic", "model_based"]
        assert d["content_bearing_layers"] == ["content", "cross_check", "structure"]
        assert d["document_rank"] == ["verified", "recovered", "not_verifiable", "review_required", "failed"]

    def test_a_stricter_policy_changes_outcomes_explicitly(self):
        strict = RollupPolicy(review_severities=frozenset({Severity.LOW, Severity.MEDIUM, Severity.HIGH}))
        d = decide_unit([content_pass()], [issue(severity=Severity.LOW)], {}, strict)
        assert d.status == S.REVIEW_REQUIRED


def _unit(i: int, st: ValidationStatus) -> UnitResult:
    return UnitResult(unit=page(i), status=st)


class TestDocumentRollup:
    def test_rank_order(self):
        order = sorted(STATUS_RANK, key=STATUS_RANK.get)
        assert order == [S.VERIFIED, S.RECOVERED, S.NOT_VERIFIABLE, S.REVIEW_REQUIRED, S.FAILED]

    @pytest.mark.parametrize(
        "unit_statuses,expected",
        [
            ([S.VERIFIED, S.VERIFIED], S.VERIFIED),
            ([S.VERIFIED, S.RECOVERED], S.RECOVERED),
            ([S.VERIFIED, S.RECOVERED, S.NOT_VERIFIABLE], S.NOT_VERIFIABLE),
            ([S.VERIFIED, S.NOT_VERIFIABLE, S.REVIEW_REQUIRED], S.REVIEW_REQUIRED),
            ([S.VERIFIED] * 366 + [S.RECOVERED] * 2 + [S.REVIEW_REQUIRED], S.REVIEW_REQUIRED),
            ([S.REVIEW_REQUIRED, S.FAILED, S.VERIFIED], S.FAILED),
        ],
    )
    def test_worst_unit_wins(self, unit_statuses, expected):
        units = [_unit(i + 1, s) for i, s in enumerate(unit_statuses)]
        assert decide_document(units)[0] == expected

    def test_no_units_is_not_verifiable_never_verified(self):
        assert decide_document([])[0] == S.NOT_VERIFIABLE

    def test_validation_failure_is_failed_even_with_clean_units(self):
        st, reason = decide_document([_unit(1, S.VERIFIED)], ValidationFailure(stage="inventory", error_type="OSError"))
        assert st == S.FAILED and "inventory" in reason and "extraction output is unaffected" in reason


class TestLayerSummary:
    def test_layer_statuses(self):
        layers = summarize_layers(
            [
                [check(C.CONTENT, O.PASSED), check(C.COMPLETENESS, O.PASSED), check(C.STRUCTURE, O.NOT_VERIFIABLE)],
                [check(C.CONTENT, O.NOT_VERIFIABLE), check(C.COMPLETENESS, O.PASSED), check(C.STRUCTURE, O.NOT_VERIFIABLE)],
                [check(C.READING_ORDER, O.NOT_APPLICABLE), check(C.EXTRACTION_INTEGRITY, O.FAILED), check(C.CROSS_CHECK, O.ERROR)],
            ]
        )
        assert layers[C.CONTENT].status == LayerStatus.PARTIAL
        assert layers[C.COMPLETENESS].status == LayerStatus.PASSED
        assert layers[C.STRUCTURE].status == LayerStatus.NOT_VERIFIABLE
        assert layers[C.READING_ORDER].status == LayerStatus.NOT_APPLICABLE
        assert layers[C.EXTRACTION_INTEGRITY].status == LayerStatus.FAILED
        assert layers[C.CROSS_CHECK].status == LayerStatus.ERROR

    def test_failed_beats_everything_in_a_layer(self):
        layers = summarize_layers([[check(C.CONTENT, O.PASSED)], [check(C.CONTENT, O.FAILED)], [check(C.CONTENT, O.ERROR)]])
        assert layers[C.CONTENT].status == LayerStatus.FAILED and layers[C.CONTENT].error == 1

    def test_evidence_kinds_listed_only_for_decided_checks(self):
        layers = summarize_layers([[check(C.CONTENT, O.PASSED, DET), check(C.CONTENT, O.PASSED, MODEL), check(C.CONTENT, O.NOT_VERIFIABLE)]])
        assert [k.value for k in layers[C.CONTENT].evidence_kinds] == ["deterministic", "model_based"]
