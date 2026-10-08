"""AXTRACT Verify wired into /api/parse and the document history, plus secondary validation and security."""

from __future__ import annotations

import io
import json
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from models.document import DocumentResponse
from tests.test_documents_api import FakeDb, env  # noqa: F401  (env is a fixture: fake DB + storage + real crypto)
from tests.verify_cases import Case, first, mutate
from tests.verify_fixtures import make_docx, make_pdf, make_pdf_numbers, make_pptx, make_xlsx_cached, png_file
from verify.models import RecoveryDecision, ValidationStatus
from verify.providers import DoclingProvider, PdfCrossCheckProvider, ProviderCandidate, ProviderError, default_providers
from verify.recovery import PromotionRefused, escalate, promote
from verify.models import EvidenceKind


@pytest.fixture(scope="module")
def tmp(tmp_path_factory):
    return tmp_path_factory.mktemp("integration")


@pytest.fixture(scope="module")
def files(tmp):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (400, 120), "white")
    ImageDraw.Draw(img).text((20, 40), "Invoice total 1200", fill="black")
    img.save(tmp / "scan.png")
    return {"pdf": make_pdf(tmp / "a.pdf", pages=2), "docx": make_docx(tmp / "a.docx"), "pptx": make_pptx(tmp / "a.pptx"),
            "xlsx": make_xlsx_cached(tmp / "a.xlsx"), "png": tmp / "scan.png"}


def post(client, path: Path):
    with open(path, "rb") as fh:
        return client.post("/api/parse", files={"file": (path.name, fh, "application/octet-stream")})


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


# ================================================================== /api/parse


class TestParseIntegration:
    @pytest.mark.parametrize("fmt", ["pdf", "docx", "pptx", "xlsx", "png"])
    def test_every_format_returns_a_validation_report(self, client, files, fmt):
        r = post(client, files[fmt])
        assert r.status_code == 200
        v = r.json()["validation"]
        assert v["status"] in {s.value for s in ValidationStatus} and v["schema_version"] == "1.0" and v["failure"] is None
        assert v["summary"]["units_total"] >= 1 and v["timings_ms"]["total"] > 0

    def test_expected_statuses(self, client, files):
        got = {f: post(client, files[f]).json()["validation"]["status"] for f in files}
        assert got["docx"] == got["pptx"] == got["xlsx"] == "verified" and got["pdf"] == "verified"
        assert got["png"] == "not_verifiable"  # OCR is not independent evidence

    def test_the_extraction_itself_is_unchanged_by_validation(self, client, files, monkeypatch):
        for fmt in ("pdf", "docx", "pptx", "xlsx"):
            with_v = post(client, files[fmt]).json()
            monkeypatch.setenv("AXTRACT_VERIFY", "0")
            without = post(client, files[fmt]).json()
            monkeypatch.delenv("AXTRACT_VERIFY")
            assert without["validation"] is None
            strip = lambda d: {k: v for k, v in d.items() if k not in ("validation", "document_id", "processing_time_ms")}
            a, b = strip(with_v), strip(without)
            for blocks in (a["blocks"], b["blocks"]):
                for blk in blocks:
                    blk["metadata"].pop("preview", None)
            assert a == b, fmt

    def test_processing_status_and_validation_status_are_independent(self, client, files):
        d = post(client, files["docx"]).json()
        assert d["status"] == "success" and "status" in d["validation"] and d["validation"]["status"] != d["status"]

    def test_a_crash_in_verify_never_costs_the_extraction(self, client, files, monkeypatch):
        import verify.engine as eng

        monkeypatch.setattr(eng, "verify_extraction", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        r = post(client, files["docx"])
        assert r.status_code == 200 and r.json()["blocks"] and r.json()["validation"] is None

    def test_a_failing_stage_is_reported_inside_the_report(self, client, files, monkeypatch):
        import verify.engine as eng

        monkeypatch.setattr(eng, "check_content", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
        d = post(client, files["docx"]).json()
        assert d["blocks"] and d["validation"]["status"] == "failed" and d["validation"]["failure"]["stage"] == "content"

    def test_validation_respects_the_request_time_budget(self, client, files, monkeypatch):
        from utils import deadline

        real = deadline.remaining
        monkeypatch.setattr(deadline, "remaining", lambda: 2.0)
        d = post(client, files["docx"]).json()
        monkeypatch.setattr(deadline, "remaining", real)
        assert d["blocks"] and d["validation"]["failure"]["stage"] == "budget"

    def test_an_exhausted_engine_budget_skips_later_stages_and_says_so(self, files):
        from tests.verify_fixtures import extract
        from verify.engine import VerifyOptions, run_verification

        rep = run_verification(files["docx"], extract(files["docx"]), VerifyOptions(time_budget_s=0.0)).report
        assert rep.status == ValidationStatus.FAILED and rep.failure.error_type == "TimeBudget"

    def test_huge_reports_are_bounded(self, client, files, monkeypatch):
        import services.parse_service as ps

        monkeypatch.setattr(ps, "_MAX_REPORT_BYTES", 10)
        v = post(client, files["pdf"]).json()["validation"]
        assert "size limit" in v["truncated"] and all(u["checks"] == [] for u in v["units"]) and v["status"]

    def test_the_uploaded_original_is_still_removed(self, client, files):
        from utils.files import UPLOAD_DIR

        before = set(UPLOAD_DIR.glob("*")) if UPLOAD_DIR.exists() else set()
        post(client, files["pdf"])
        post(client, files["xlsx"])
        after = set(UPLOAD_DIR.glob("*")) if UPLOAD_DIR.exists() else set()
        assert {p for p in after - before if p.is_file()} == set()

    def test_unsupported_and_corrupt_uploads_behave_as_before(self, client, tmp):
        (tmp / "x.txt").write_text("hi")
        assert post(client, tmp / "x.txt").status_code == 415
        (tmp / "bad.pdf").write_bytes(b"%PDF-1.4 truncated")
        assert post(client, tmp / "bad.pdf").status_code == 422

    def test_response_stays_parseable_by_the_documented_model(self, client, files):
        DocumentResponse.model_validate(post(client, files["pptx"]).json())


# ================================================================== history persistence


class TestHistory:
    def test_validation_is_saved_with_the_result_and_returned_without_reparsing(self, env, files):
        client, fake, storage, who = env
        with open(files["docx"], "rb") as fh:
            body = client.post("/api/documents", files={"file": ("a.docx", fh, "application/octet-stream")}).json()
        assert body["result"]["validation"]["status"] == "verified"
        saved = client.get(f"/api/documents/{body['document_id']}/result").json()["result"]
        assert saved["validation"] == body["result"]["validation"]

    def test_verification_failure_does_not_stop_saving(self, env, files, monkeypatch):
        import verify.engine as eng

        client, *_ = env
        monkeypatch.setattr(eng, "verify_extraction", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        with open(files["docx"], "rb") as fh:
            r = client.post("/api/documents", files={"file": ("a.docx", fh, "application/octet-stream")})
        assert r.status_code == 201 and r.json()["result"]["validation"] is None and r.json()["result"]["blocks"]


# ================================================================== secondary providers


class FakeReader:
    """A scripted secondary reader."""
    tier, kind = "builtin", EvidenceKind.MODEL_BASED

    def __init__(self, name, text, ok=True):
        self.name, self._text, self._ok = name, text, ok

    def available(self):
        return (self._ok, "" if self._ok else "scripted unavailable")

    def supports(self, inventory, unit):
        return True

    def extract(self, file_path, unit, budget_s):
        return ProviderCandidate(provider=self.name, unit=unit, text=self._text, kind=self.kind)


@pytest.fixture(scope="module")
def flagged(tmp):
    case = Case.of(make_pdf_numbers(tmp / "flag.pdf"))
    bad = mutate(case.resp, lambda r: setattr(first(r, lambda b: "Revenue was" in b.content), "content",
                                              first(r, lambda b: "Revenue was" in b.content).content.replace("18.2", "13.2")))
    return case, bad, case.verify(bad).report


class TestProviders:
    def test_docling_is_optional_and_reports_why_it_is_unavailable(self):
        ok, why = DoclingProvider().available()
        assert (ok is False and "not installed" in why) or ok is True

    def test_docling_is_not_in_the_default_cheap_set(self):
        assert [p.name for p in default_providers()] == ["pdf-crosscheck"]
        assert [p.name for p in default_providers(include_heavy=True)] == ["pdf-crosscheck", "docling"]

    def test_docling_adapter_with_a_stand_in_module(self, tmp, monkeypatch):
        class Doc:
            def export_to_markdown(self, page_no=None):
                return f"markdown of page {page_no}"

        class Conv:
            def convert(self, path):
                return types.SimpleNamespace(document=Doc())

        mod = types.ModuleType("docling")
        sub = types.ModuleType("docling.document_converter")
        sub.DocumentConverter = Conv
        mod.document_converter = sub
        monkeypatch.setitem(sys.modules, "docling", mod)
        monkeypatch.setitem(sys.modules, "docling.document_converter", sub)
        mod.__spec__ = types.SimpleNamespace(name="docling")
        p = DoclingProvider()
        assert p.available()[0] is True
        from verify.models import UnitRef, UnitType

        cand = p.extract(tmp / "a.pdf", UnitRef(type=UnitType.PAGE, index=2), 5)
        assert cand.text == "markdown of page 2" and cand.kind == EvidenceKind.MODEL_BASED

    def test_the_crosscheck_reads_digital_pages_and_declines_scanned_ones(self, tmp):
        scan = Case.of(make_pdf(tmp / "sc.pdf", pages=2, scanned_page=2))
        p = PdfCrossCheckProvider()
        from verify.models import UnitRef, UnitType

        assert p.supports(scan.inv, UnitRef(type=UnitType.PAGE, index=1)) and not p.supports(scan.inv, UnitRef(type=UnitType.PAGE, index=2))
        assert "Section 1 Overview" in p.extract(scan.path, UnitRef(type=UnitType.PAGE, index=1), 5).text

    def test_escalation_records_candidates_with_evidence_and_changes_nothing(self, flagged):
        case, bad, rep = flagged
        before_resp, before_rep = bad.model_dump(), rep.model_dump()
        esc = escalate(case.path, bad, rep, inventory=case.inv, providers=[PdfCrossCheckProvider()])
        assert bad.model_dump() == before_resp and rep.model_dump() == before_rep  # inputs untouched
        assert esc.recoveries and all(r.decision == RecoveryDecision.CANDIDATE_ONLY for r in esc.recoveries)
        assert esc.report.status == rep.status == ValidationStatus.REVIEW_REQUIRED  # nothing is trusted yet
        r = esc.recoveries[0]
        assert r.original.content and r.candidate.content and {c.metric for c in r.comparison} >= {
            "source_recall_in_original", "source_recall_in_candidate", "candidate_vs_original_similarity", "verdict"}
        assert "13.2" in r.original.content and "18.2" in r.candidate.content

    def test_a_secondary_reader_that_agrees_with_the_source_is_supported(self, flagged):
        case, bad, rep = flagged
        good = case.inv.unit(1).objects[0].text
        esc = escalate(case.path, bad, rep, inventory=case.inv, providers=[FakeReader("agreeing", good)])
        assert [c.value for r in esc.recoveries for c in r.comparison if c.metric == "verdict"] == ["candidate_supported"]

    def test_a_secondary_reader_that_disagrees_is_never_promoted_on_its_say_so(self, flagged):
        case, bad, rep = flagged
        esc = escalate(case.path, bad, rep, inventory=case.inv, providers=[FakeReader("wrong", "completely unrelated text")])
        ids = [r.id for r in esc.recoveries]
        with pytest.raises(PromotionRefused, match="does not favour the candidate"):
            promote(bad, esc.report, ids, decided_by="explicit_request", reason="trying anyway")

    def test_an_unavailable_or_failing_provider_is_reported_not_fatal(self, flagged):
        case, bad, rep = flagged

        class Boom(FakeReader):
            def extract(self, *a, **k):
                raise ProviderError("model crashed")

        esc = escalate(case.path, bad, rep, inventory=case.inv, providers=[FakeReader("off", "", ok=False), Boom("boom", "")])
        assert esc.recoveries == [] and any("model crashed" in n for n in esc.notes)
        assert {p.name: p.available for p in esc.report.providers}["off"] is False

    def test_the_escalation_time_budget_is_respected(self, flagged):
        case, bad, rep = flagged
        esc = escalate(case.path, bad, rep, inventory=case.inv, providers=[FakeReader("slow", "x")], budget_s=0.0)
        assert esc.recoveries == [] and any("budget" in n for n in esc.notes)

    def test_promotion_keeps_the_whole_audit_trail_and_the_original_blocks(self, flagged):
        case, bad, rep = flagged
        good = case.inv.unit(1).objects[0].text
        esc = escalate(case.path, bad, rep, inventory=case.inv, providers=[FakeReader("agreeing", good)])
        out = promote(bad, esc.report, [esc.recoveries[0].id], decided_by="explicit_request", reason="matches the PDF text layer")
        assert out.report.status == ValidationStatus.RECOVERED
        assert [b.id for b in bad.blocks if "superseded_by" not in b.metadata] == [b.id for b in bad.blocks]  # input untouched
        kept = {b.id for b in out.response.blocks}
        assert {b.id for b in bad.blocks} <= kept  # every original block is still there
        new = [b for b in out.response.blocks if b.extractor.startswith("verify:")]
        assert len(new) == 1 and new[0].requires_review and new[0].metadata["original_block_ids"]
        rec = next(r for r in out.report.recoveries if r.decision == RecoveryDecision.PROMOTED)
        assert rec.decided_by == "explicit_request" and rec.reason and rec.original.content and rec.candidate.content and rec.comparison
        assert rec.promoted_block_ids == [new[0].id]
        assert out.response.validation["status"] == "recovered"

    def test_a_promotion_without_a_reason_or_with_a_bad_decider_is_refused(self, flagged):
        case, bad, rep = flagged
        good = case.inv.unit(1).objects[0].text
        esc = escalate(case.path, bad, rep, inventory=case.inv, providers=[FakeReader("agreeing", good)])
        ids = [esc.recoveries[0].id]
        with pytest.raises(PromotionRefused):
            promote(bad, esc.report, ids, decided_by="explicit_request", reason="  ")
        with pytest.raises(PromotionRefused):
            promote(bad, esc.report, ids, decided_by="auto", reason="x")
        with pytest.raises(PromotionRefused, match="unknown"):
            promote(bad, esc.report, ["nope"], decided_by="explicit_request", reason="x")

    def test_a_named_reviewer_may_promote_without_a_supporting_verdict(self, flagged):
        case, bad, rep = flagged
        esc = escalate(case.path, bad, rep, inventory=case.inv, providers=[FakeReader("meh", "reviewer typed this")])
        out = promote(bad, esc.report, [esc.recoveries[0].id], decided_by="reviewer", reason="checked by hand against the paper copy")
        assert out.report.status == ValidationStatus.RECOVERED
        assert next(r for r in out.report.recoveries if r.decision == RecoveryDecision.PROMOTED).decided_by == "reviewer"

    def test_a_promoted_recovery_cannot_be_promoted_twice(self, flagged):
        case, bad, rep = flagged
        good = case.inv.unit(1).objects[0].text
        esc = escalate(case.path, bad, rep, inventory=case.inv, providers=[FakeReader("agreeing", good)])
        out = promote(bad, esc.report, [esc.recoveries[0].id], decided_by="explicit_request", reason="ok")
        with pytest.raises(PromotionRefused, match="already"):
            promote(out.response, out.report, [esc.recoveries[0].id], decided_by="explicit_request", reason="again")

    def test_clean_documents_have_nothing_to_escalate(self, tmp):
        case = Case.of(make_pdf_numbers(tmp / "clean.pdf"))
        esc = escalate(case.path, case.resp, case.verify().report, inventory=case.inv, providers=[FakeReader("x", "y")])
        assert esc.recoveries == []


# ================================================================== escalation / promotion API


@pytest.fixture
def saved_flagged(env, tmp):
    """A saved PDF whose stored extraction has a changed digit and a report that flags it."""
    client, fake, storage, who = env
    pdf = make_pdf_numbers(tmp / "api.pdf")
    with open(pdf, "rb") as fh:
        body = client.post("/api/documents", files={"file": ("api.pdf", fh, "application/pdf")}).json()
    resp = DocumentResponse.model_validate(body["result"])
    bad = mutate(resp, lambda r: setattr(first(r, lambda b: "Revenue was" in b.content), "content",
                                         first(r, lambda b: "Revenue was" in b.content).content.replace("18.2", "13.2")))
    from verify.engine import run_verification

    bad.validation = run_verification(pdf, bad).report.model_dump(mode="json")
    run_id = next(iter(fake.outputs))
    fake.outputs[run_id]["response_json"] = bad.model_dump_json()

    async def update(document_id, user_id, response_json):
        fake.outputs[next(iter(fake.outputs))]["response_json"] = response_json
        return True

    FakeDb.update_latest_output_json = lambda self, d, u, j: update(d, u, j)
    import routers.documents as docs

    docs.db.update_latest_output_json = update
    return client, fake, who, body["document_id"]


class TestEscalationApi:
    def test_escalate_stores_candidates_without_changing_the_output(self, saved_flagged):
        client, fake, who, doc_id = saved_flagged
        before = json.loads(next(iter(fake.outputs.values()))["response_json"])
        r = client.post(f"/api/documents/{doc_id}/verify/escalate")
        assert r.status_code == 200 and r.json()["recoveries"]
        after = json.loads(next(iter(fake.outputs.values()))["response_json"])
        assert after["blocks"] == before["blocks"] and after["markdown"] == before["markdown"]
        assert after["validation"]["recoveries"] and after["validation"]["status"] == "review_required"

    def test_promote_saves_a_new_result_and_keeps_the_earlier_one(self, saved_flagged):
        client, fake, who, doc_id = saved_flagged
        recs = client.post(f"/api/documents/{doc_id}/verify/escalate").json()["recoveries"]
        supported = [r["id"] for r in recs if any(c["metric"] == "verdict" and c["value"] == "candidate_supported" for c in r["comparison"])]
        assert supported
        n_before = len(fake.outputs)
        r = client.post(f"/api/documents/{doc_id}/verify/promote", json={"recovery_ids": supported, "reason": "matches the text layer"})
        assert r.status_code == 200 and r.json()["result"]["validation"]["status"] == "recovered"
        assert len(fake.outputs) == n_before + 1  # the earlier output is preserved for audit

    def test_promoting_an_unsupported_candidate_is_refused(self, saved_flagged):
        client, fake, who, doc_id = saved_flagged
        client.post(f"/api/documents/{doc_id}/verify/escalate")
        r = client.post(f"/api/documents/{doc_id}/verify/promote", json={"recovery_ids": ["rec-9999"], "reason": "nope nope"})
        assert r.status_code == 422

    def test_request_bodies_are_validated(self, saved_flagged):
        client, fake, who, doc_id = saved_flagged
        assert client.post(f"/api/documents/{doc_id}/verify/promote", json={"recovery_ids": [], "reason": "abc"}).status_code == 422
        assert client.post(f"/api/documents/{doc_id}/verify/promote", json={"recovery_ids": ["x"], "reason": ""}).status_code == 422

    def test_other_users_cannot_escalate_or_promote(self, saved_flagged):
        client, fake, who, doc_id = saved_flagged
        who["user"] = "user-b"
        assert client.post(f"/api/documents/{doc_id}/verify/escalate").status_code == 404
        assert client.post(f"/api/documents/{doc_id}/verify/promote", json={"recovery_ids": ["x"], "reason": "abc"}).status_code == 404

    def test_endpoints_require_authentication(self, files):
        import main

        main.app.dependency_overrides.clear()
        with TestClient(main.app) as c:
            assert c.post("/api/documents/anything/verify/escalate").status_code == 401
            assert c.post("/api/documents/anything/verify/promote", json={"recovery_ids": ["x"], "reason": "abc"}).status_code == 401


# ================================================================== security


class TestSecurity:
    def test_reports_contain_no_server_paths_or_secrets(self, client, files, monkeypatch):
        monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb-secret-DO-NOT-LEAK-12345")
        monkeypatch.setenv("AXTRACT_MASTER_KEY_BASE64", "bWFzdGVyLWtleS1ETy1OT1QtTEVBSw==")
        for fmt in ("pdf", "docx", "pptx", "xlsx", "png"):
            text = json.dumps(post(client, files[fmt]).json()["validation"])
            for needle in ("DO-NOT-LEAK", "bWFzdGVy", "AppData", "uploads", str(Path.home().name), "Temp\\\\", "/tmp/"):
                assert needle not in text, (fmt, needle)

    def test_evidence_text_is_bounded(self, tmp):
        from tests.verify_fixtures import extract
        import docx

        d = docx.Document()
        d.add_paragraph("word " * 20000)
        d.save(tmp / "big.docx")
        case = Case.of(tmp / "big.docx")
        rep = case.verify(mutate(case.resp, lambda r: setattr(r.blocks[0], "content", "other " * 20000))).report
        assert all(len(e.source or "") <= 2000 and len(e.extracted or "") <= 2000 for i in rep.issues for e in i.evidence)
        assert len(rep.model_dump_json()) < 200_000

    def test_a_hostile_xml_bomb_is_refused_not_expanded(self, tmp):
        import zipfile

        from verify.inventory import build_inventory

        bomb = ('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
                '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>&c;</w:t></w:r></w:p></w:body></w:document>')
        with zipfile.ZipFile(tmp / "bomb.docx", "w") as z:
            z.writestr("word/document.xml", bomb)
        inv = build_inventory(tmp / "bomb.docx", "docx")
        assert inv.error and "Entities" in inv.error or "entit" in (inv.error or "").lower()

    def test_oversized_packages_are_refused_before_decompression(self, tmp, monkeypatch):
        import verify.inventory.ooxml as ox
        from verify.inventory import build_inventory

        monkeypatch.setattr(ox, "MAX_TOTAL_UNCOMPRESSED", 10)
        assert "too large" in build_inventory(make_xlsx_cached(tmp / "big.xlsx"), "xlsx").error

    def test_a_malicious_filename_cannot_reach_the_report(self, client, tmp):
        from tests.verify_fixtures import make_pdf

        p = make_pdf(tmp / "x.pdf", pages=1)
        with open(p, "rb") as fh:
            r = client.post("/api/parse", files={"file": ("..\\..\\evil<script>.pdf", fh, "application/pdf")})
        assert r.status_code == 200 and "<script>" not in json.dumps(r.json()["validation"])

    def test_secondary_validation_is_never_part_of_the_normal_parse_path(self, client, files, monkeypatch):
        calls = []
        monkeypatch.setattr(DoclingProvider, "extract", lambda *a, **k: calls.append(1))
        for fmt in ("pdf", "docx"):
            post(client, files[fmt])
        assert calls == [] and "docling" not in json.dumps(post(client, files["pdf"]).json()["validation"]["providers"])
