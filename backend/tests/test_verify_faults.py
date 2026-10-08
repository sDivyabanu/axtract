"""Fault-injection campaign as tests: every injected corruption must be noticed, by the right layer."""

from __future__ import annotations

import pytest

from tests.fault_campaign import FAULTS, run_campaign
from verify.models import ValidationStatus


@pytest.fixture(scope="module")
def campaign(tmp_path_factory):
    results, cases = run_campaign(tmp_path_factory.mktemp("campaign"))
    return {r.fault.name + "|" + r.fault.case: r for r in results}, cases


def test_every_clean_baseline_is_verified(campaign):
    _, cases = campaign
    for name, case in cases.items():
        rep = case.verify().report
        assert rep.status == ValidationStatus.VERIFIED, (name, rep.status, [(i.code, i.locator.unit.key) for i in rep.issues])
        assert rep.summary.evidence_coverage == 1.0 and rep.summary.agreement_rate == 1.0


@pytest.mark.parametrize("fault", FAULTS, ids=lambda f: f"{f.case}:{f.name}")
def test_each_injected_fault_is_detected_and_attributed(fault, campaign):
    r = campaign[0][fault.name + "|" + fault.case]
    assert r.detected, f"SILENT LOSS: {fault.name} went unnoticed (status {r.status})"
    assert r.matched_expectation, f"{fault.name}: flagged as {sorted(r.codes)}, expected one of {sorted(fault.expect)}"


def test_silent_loss_rate_on_injected_faults_is_zero(campaign):
    results = list(campaign[0].values())
    missed = [r.fault.name for r in results if not r.detected]
    assert len(results) >= 50 and missed == []


def test_campaign_covers_every_format_and_fault_family(campaign):
    results = list(campaign[0].values())
    assert {r.fault.case for r in results} == {"docx", "pptx", "xlsx", "pdf_text", "pdf_table"}
    families = " ".join(r.fault.name for r in results)
    for needle in ("digit", "swapped", "merged", "flattened", "formula", "removed", "dropped", "extracted as"):
        assert needle in families, needle
