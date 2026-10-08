"""Content, structure, reading order and the assembled report, on real files."""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest

from models.document import BlockType as T, DocumentBlock
from tests.verify_cases import Case, first, mutate, sync_table_content, tables
from tests.verify_fixtures import (
    extract, make_docx_campaign, make_docx_merged, make_pdf, make_pdf_numbers, make_pptx_campaign, make_pptx_stacked,
    make_xlsx_cached, make_pptx, make_docx, png_file,
)
from verify.engine import VerifyOptions, run_verification, verify_extraction
from verify.models import CheckName, CheckOutcome, EvidenceKind, Severity, ValidationReport, ValidationStatus
from verify.order import _displaced
from verify.textdiff import content_tokens, diff_multiset, diff_sequence

SAMPLES = Path(__file__).resolve().parents[2] / "sample_files"


@pytest.fixture(scope="module")
def tmp(tmp_path_factory):
    return tmp_path_factory.mktemp("layers")


@pytest.fixture(scope="module")
def docx(tmp):
    return Case.of(make_docx_campaign(tmp / "d.docx"))


@pytest.fixture(scope="module")
def xlsx(tmp):
    return Case.of(make_xlsx_cached(tmp / "x.xlsx"))


@pytest.fixture(scope="module")
def pdf(tmp):
    return Case.of(make_pdf_numbers(tmp / "p.pdf"))


@pytest.fixture(scope="module")
def pdf2(tmp):
    return Case.of(make_pdf_numbers(tmp / "p2.pdf", columns=2))


@pytest.fixture(scope="module")
def pptx_stacked(tmp):
    return Case.of(make_pptx_stacked(tmp / "s.pptx"))


def codes(outcome, blocking=False):
    return {i.code for i in outcome.issues if not blocking or i.severity in (Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL)}


# ============================================================================ token diff


class TestTextDiff:
    def test_a_number_is_one_token_so_a_single_digit_is_a_visible_change(self):
        d = diff_sequence(content_tokens("Revenue was $18.2 million"), content_tokens("Revenue was $13.2 million"))
        assert [(c.kind, c.src, c.out, c.numeric) for c in d.changes] == [("changed", ("$18200000",), ("$13200000",), True)]

    @pytest.mark.parametrize("a,b", [("$18.2 million", "$18,200,000"), ("Café ﬁnance", "café finance"), ("inter-\nnational", "international"),
                                     ("1,234.50 units", "1234.50 units"), ("12.5 %", "12.5%")])
    def test_formatting_differences_are_not_changes(self, a, b):
        assert diff_sequence(content_tokens(a), content_tokens(b)).equal

    def test_multiset_ignores_order_but_not_content(self):
        assert diff_multiset(content_tokens("a b c"), content_tokens("c a b")).equal
        d = diff_multiset(content_tokens("total 18.2 now"), content_tokens("total 13.2 now"))
        assert [(c.kind, c.numeric) for c in d.changes] == [("changed", True)]

    def test_extra_and_missing_are_distinguished(self):
        d = diff_multiset(["a", "b"], ["a", "zzzz"] + ["b"], ignore_extra=None)
        assert [c.kind for c in d.changes] == ["extra"]
        assert [c.kind for c in diff_multiset(["a", "b"], ["a"]).changes] == ["missing"]


# ============================================================================ content


class TestContent:
    def test_clean_office_and_xlsx_content_agrees(self, docx, xlsx):
        for case in (docx, xlsx):
            _, content, _, _ = case.layers()
            assert not codes(content, blocking=True)
            assert all(c.outcome == CheckOutcome.PASSED for u in content.units for c in u.checks if c.evidence_kind == EvidenceKind.DETERMINISTIC)

    def test_a_changed_digit_names_both_values_and_is_high(self, docx):
        r = mutate(docx.resp, lambda r: setattr(first(r, lambda b: "Revenue was" in b.content), "content",
                                                first(r, lambda b: "Revenue was" in b.content).content.replace("18.2", "13.2")))
        i = next(i for i in docx.layers(r)[1].issues if i.code == "numeric_value_mismatch")
        assert i.severity == Severity.HIGH and "$18200000" in i.evidence[0].source and "$13200000" in i.evidence[0].extracted
        assert i.evidence[0].kind == EvidenceKind.DETERMINISTIC and i.evidence[0].engine.startswith("ooxml:")

    def test_formatting_only_changes_are_not_flagged(self, docx):
        def f(r):
            b = first(r, lambda b: "Revenue was" in b.content)
            b.content = "Revenue was  $18,200,000 in 2024, up 12.5 % on 2023."
        assert not codes(docx.layers(mutate(docx.resp, f))[1], blocking=True)

    def test_inline_math_in_output_is_not_a_hallucination(self, docx):
        def f(r):
            b = first(r, lambda b: "Management expects" in b.content)
            b.content += " $x^2 + y^2$"
        assert not codes(docx.layers(mutate(docx.resp, f))[1], blocking=True)

    def test_xlsx_cells_are_compared_by_coordinate_and_meaning(self, xlsx):
        def edit(row, col, v):
            return mutate(xlsx.resp, lambda r: first(r, lambda b: b.metadata.get("sheet_name") == "Sales").metadata["rows"][row].__setitem__(col, v))
        _, content, _, _ = xlsx.layers(edit(1, 2, "21"))
        i = next(i for i in content.issues if i.code == "cell_value_mismatch")
        assert i.severity == Severity.HIGH and i.evidence[0].detail["cells"][0] == {"cell": "C2", "source": "20", "extracted": "21", "is_formula": False}
        assert not codes(xlsx.layers(edit(1, 2, "20.0"))[1], blocking=True)  # same number, different spelling
        assert "cell_value_mismatch" in codes(xlsx.layers(edit(2, 5, "7"))[1])  # a text cell '007' is not the number 7
        assert "formula_result_mismatch" in codes(xlsx.layers(edit(1, 3, "31"))[1])

    def test_pdf_compares_the_native_text_layer_and_localises_the_difference(self, pdf):
        r = mutate(pdf.resp, lambda r: setattr(first(r, lambda b: "Revenue was" in b.content), "content",
                                               first(r, lambda b: "Revenue was" in b.content).content.replace("18.2", "13.2")))
        i = next(i for i in pdf.layers(r)[1].issues if i.code == "numeric_value_mismatch")
        assert i.evidence[0].engine == "pypdfium2" and i.evidence[0].kind == EvidenceKind.DETERMINISTIC and i.locator.bbox

    def test_ocr_is_never_independent_evidence(self, tmp):
        case = Case.of(make_pdf(tmp / "scan.pdf", pages=2, scanned_page=2))
        r = mutate(case.resp, lambda r: [setattr(b, "content", b.content.replace("a", "x")) for b in r.blocks if b.page == 2])
        _, content, _, _ = case.layers(r)
        u2 = next(u for u in content.units if u.unit.key == "page:2")
        assert [c.outcome for c in u2.checks] == [CheckOutcome.NOT_VERIFIABLE] and "OCR" in u2.checks[0].summary
        assert not [i for i in content.issues if i.locator.unit.key == "page:2"]  # wrong OCR text cannot be caught: and is not claimed verified

    def test_images_are_not_verifiable(self, tmp):
        case = Case.of(png_file(tmp / "i.png"))
        assert [c.outcome for c in case.layers()[1].units[0].checks] == [CheckOutcome.NOT_VERIFIABLE]

    def test_ocr_text_on_a_digital_page_does_not_count_as_invented(self, pdf):
        def f(r):
            r.blocks.append(DocumentBlock(id="ocr1", type=T.PARAGRAPH, content="entirely unrelated recognised words", page=1,
                                          extractor="rapidocr", reading_order=99, bbox=(0.1, 0.9, 0.5, 0.95), metadata={"ocr_engine": "rapidocr"}))
        _, content, _, _ = pdf.layers(mutate(pdf.resp, f))
        assert not codes(content, blocking=True) and "content_not_compared" in codes(content)

    def test_what_was_not_compared_is_recorded_not_hidden(self, tmp):
        case = Case.of(make_pptx_campaign(tmp / "cp.pptx"))
        info = [i for i in case.layers()[1].issues if i.code == "content_not_compared"]
        assert info and info[0].severity == Severity.INFO and info[0].evidence[0].detail.get("chart") == 1


# ============================================================================ structure


class TestStructure:
    def test_clean_structure_passes_for_every_format(self, docx, xlsx, tmp):
        for case in (docx, xlsx, Case.of(make_pptx_campaign(tmp / "st.pptx")), Case.of(make_docx_merged(tmp / "st.docx"))):
            assert not codes(case.layers()[2], blocking=True)

    def test_merged_cells_are_compared_with_the_source_grid(self, tmp):
        case = Case.of(make_docx_merged(tmp / "m.docx"))
        r = mutate(case.resp, lambda r: tables(r)[0].metadata.update(merged_cells={"has_merged_cells": False, "merged_regions": []}))
        i = next(i for i in case.layers(r)[2].issues if i.code == "merged_cells_mismatch")
        assert i.evidence[0].detail["missing_in_output"] == [(0, 0, 1, 2), (1, 2, 2, 1)] or i.evidence[0].detail["missing_in_output"] == [[0, 0, 1, 2], [1, 2, 2, 1]]

    def test_row_and_column_order_are_structure_not_content(self, docx):
        def swap(r):
            t = tables(r)[0]
            t.metadata["rows"][1], t.metadata["rows"][3] = t.metadata["rows"][3], t.metadata["rows"][1]
            sync_table_content(t)
        comp, content, structure, _ = docx.layers(mutate(docx.resp, swap))
        assert "table_row_order_changed" in codes(structure) and "cell_value_mismatch" not in codes(content)

    def test_a_table_flattened_into_text_is_a_structure_finding_and_only_low_for_completeness(self, docx):
        def flat(r):
            t = tables(r)[0]
            t.type, t.metadata = T.PARAGRAPH, {}
        comp, _, structure, _ = docx.layers(mutate(docx.resp, flat))
        assert {i.severity for i in comp.issues if i.code == "possible_missing_table"} == {Severity.LOW}
        assert "table_flattened_into_text" in codes(structure, blocking=True)

    def test_heading_level_and_type_are_checked_against_the_style(self, docx):
        def lvl(r):
            first(r, lambda b: b.type == T.HEADING).metadata["heading_level"] = 3
        assert "heading_level_mismatch" in codes(docx.layers(mutate(docx.resp, lvl))[2])

    def test_pdf_headings_and_lists_have_no_independent_structure_evidence(self, pdf):
        pdf_headed = Case.of(make_pdf(pdf.path.with_name("h.pdf"), pages=1))
        notes = [c for u in pdf_headed.layers()[2].units for c in u.checks if c.outcome == CheckOutcome.NOT_VERIFIABLE]
        assert notes and "no independent evidence" in notes[0].summary

    def test_pdf_table_findings_are_heuristic_and_dimension_disagreement_is_only_low(self, tmp):
        case = Case.of(make_pdf(tmp / "tb.pdf", pages=1))
        def dims(r):
            t = tables(r)[0]
            t.metadata["row_count"], t.metadata["rows"] = 9, t.metadata["rows"] + [["x", "y", "z"]] * 5
        issues = case.layers(mutate(case.resp, dims))[2].issues
        i = next(i for i in issues if i.code == "table_dimensions_mismatch")
        assert i.severity == Severity.LOW and i.evidence[0].kind == EvidenceKind.HEURISTIC


# ============================================================================ reading order


class TestReadingOrder:
    def test_displaced_finds_the_items_out_of_place(self):
        assert _displaced([1, 2, 3, 4]) == []
        assert _displaced([2, 1, 3, 4], strict=True) in ([0], [1])
        assert _displaced([5, 1, 2, 3, 4], strict=True) == [0]
        assert _displaced([1, 1, 2], strict=False) == [] and _displaced([1, 1, 2], strict=True) != []

    def test_docx_order_follows_the_document_body(self, docx):
        assert not codes(docx.layers()[3])
        def swap(r):
            a, b = first(r, lambda x: "Revenue was" in x.content), first(r, lambda x: "Management expects" in x.content)
            ia, ib = r.blocks.index(a), r.blocks.index(b)
            r.blocks[ia], r.blocks[ib] = b, a
            a.reading_order, b.reading_order = b.reading_order, a.reading_order
        i = next(i for i in docx.layers(mutate(docx.resp, swap))[3].issues if i.code == "reading_order_mismatch")
        assert i.evidence[0].kind == EvidenceKind.DETERMINISTIC and i.evidence[0].engine == "ooxml:word/document.xml"

    def test_pptx_only_checks_unambiguous_stacked_shapes(self, pptx_stacked):
        assert not codes(pptx_stacked.layers()[3])
        def swap(r):
            a, b = first(r, lambda x: "First box" in x.content), first(r, lambda x: "Third box" in x.content)
            a.reading_order, b.reading_order = b.reading_order, a.reading_order
            r.blocks[r.blocks.index(a)], r.blocks[r.blocks.index(b)] = b, a
        i = next(i for i in pptx_stacked.layers(mutate(pptx_stacked.resp, swap))[3].issues if i.code == "reading_order_mismatch")
        assert i.evidence[0].kind == EvidenceKind.HEURISTIC

    def test_pdf_single_column_is_checked_multi_column_is_not_verifiable(self, pdf, pdf2):
        assert not codes(pdf.layers()[3])
        def swap(r):
            a, b = first(r, lambda x: "Revenue was" in x.content), first(r, lambda x: "Capital spending" in x.content)
            a.reading_order, b.reading_order = b.reading_order, a.reading_order
            r.blocks[r.blocks.index(a)], r.blocks[r.blocks.index(b)] = b, a
        assert "reading_order_mismatch" in codes(pdf.layers(mutate(pdf.resp, swap))[3])
        order = pdf2.layers()[3]
        assert not order.issues
        assert any(c.outcome == CheckOutcome.NOT_VERIFIABLE and "multi-column" in c.summary for u in order.units for c in u.checks)

    def test_xlsx_has_no_reading_order_and_none_is_invented(self, xlsx):
        order = xlsx.layers()[3]
        assert order.issues == [] and all(c.outcome == CheckOutcome.NOT_APPLICABLE for u in order.units for c in u.checks)


# ============================================================================ the assembled report


class TestReport:
    def test_clean_documents_are_verified_with_the_evidence_that_proves_it(self, docx, xlsx):
        for case in (docx, xlsx):
            rep = case.verify().report
            assert rep.status == ValidationStatus.VERIFIED
            for u in rep.units:
                assert any(v.startswith(("content:", "structure:")) for v in u.verified_by), u.verified_by
            assert {CheckName.CONTENT, CheckName.STRUCTURE, CheckName.COMPLETENESS, CheckName.EXTRACTION_INTEGRITY} <= set(rep.checks)

    def test_a_passing_check_never_overrides_an_unresolved_disagreement(self, docx):
        rep = docx.verify(mutate(docx.resp, lambda r: setattr(first(r, lambda b: "Revenue was" in b.content), "content",
                                                               first(r, lambda b: "Revenue was" in b.content).content.replace("18.2", "13.2")))).report
        unit = rep.units[0]
        assert any(c.check == CheckName.STRUCTURE and c.outcome == CheckOutcome.PASSED for c in unit.checks)  # other layers passed
        assert any(c.check == CheckName.CONTENT and c.outcome == CheckOutcome.FAILED for c in unit.checks)
        assert unit.status == rep.status == ValidationStatus.REVIEW_REQUIRED and unit.verified_by == []

    def test_insufficient_evidence_stays_not_verifiable(self, tmp):
        scan = Case.of(make_pdf(tmp / "sc.pdf", pages=2, scanned_page=2)).verify().report
        assert {u.unit.key: u.status for u in scan.units} == {"page:1": ValidationStatus.VERIFIED, "page:2": ValidationStatus.NOT_VERIFIABLE}
        assert scan.status == ValidationStatus.NOT_VERIFIABLE and 0 < scan.summary.evidence_coverage < 1
        from PIL import Image, ImageDraw

        pic = Image.new("RGB", (400, 120), "white")
        ImageDraw.Draw(pic).text((20, 40), "Invoice total 1200", fill="black")
        pic.save(tmp / "im.png")
        img = Case.of(tmp / "im.png").verify().report
        assert img.status == ValidationStatus.NOT_VERIFIABLE and img.summary.evidence_coverage == 0.0 and img.summary.agreement_rate is None

    def test_reading_order_gaps_are_listed_but_do_not_block_verified(self, pdf2):
        rep = pdf2.verify().report
        assert rep.status == ValidationStatus.VERIFIED and rep.units[0].gaps == ["reading_order"]

    def test_issues_keep_exact_locations_and_independent_evidence(self, docx, tmp):
        rep = docx.verify(mutate(docx.resp, lambda r: r.blocks.remove(first(r, lambda b: "Operating costs" in b.content)))).report
        i = next(i for i in rep.issues if i.code == "possible_missing_text")
        assert i.locator.unit.key == "section:1" and i.evidence[0].detail["path"].startswith("body[") and i.evidence[0].source
        case = Case.of(make_pdf_numbers(tmp / "loc.pdf"))
        rep = case.verify(mutate(case.resp, lambda r: r.blocks.remove(first(r, lambda b: "Operating costs" in b.content)))).report
        assert next(i for i in rep.issues if i.code == "possible_missing_text").locator.bbox

    def test_stage_timings_and_providers_are_reported(self, docx, pdf):
        rep = docx.verify().report
        assert {"integrity", "inventory", "completeness", "content", "structure", "reading_order", "total"} <= set(rep.timings_ms)
        assert [p.name for p in pdf.verify().report.providers] == ["pypdfium2", "pdfplumber"]

    def test_the_report_is_valid_json_deterministic_and_leaves_the_extraction_untouched(self, docx):
        before = docx.resp.model_dump()
        a, b = docx.verify().report, docx.verify().report
        assert docx.resp.model_dump() == before
        assert ValidationReport.model_validate_json(a.model_dump_json()) == a
        strip = lambda r: r.model_dump(exclude={"timings_ms"})
        assert strip(a) == strip(b)

    def test_a_stage_failure_is_contained_and_reported(self, docx, monkeypatch):
        import verify.engine as eng

        monkeypatch.setattr(eng, "check_content", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        art = run_verification(docx.path, docx.resp)
        rep = art.report
        assert rep.status == ValidationStatus.FAILED and rep.failure.stage == "content" and rep.failure.error_type == "RuntimeError"
        assert any(c.check == CheckName.STRUCTURE for u in rep.units for c in u.checks)  # the other stages still ran
        assert docx.resp.blocks  # the extraction is untouched

    def test_verify_extraction_never_raises(self, docx, monkeypatch, tmp):
        import verify.engine as eng

        monkeypatch.setattr(eng, "run_verification", lambda *a, **k: (_ for _ in ()).throw(MemoryError("x")))
        rep = verify_extraction(docx.path, docx.resp)
        assert rep.status == ValidationStatus.FAILED and rep.failure.stage == "engine"
        monkeypatch.undo()
        assert verify_extraction(tmp / "does_not_exist.docx", docx.resp).status != ValidationStatus.VERIFIED  # unreadable source

    def test_an_unreadable_source_never_looks_verified(self, docx, tmp):
        (tmp / "broken.docx").write_bytes(b"not a zip")
        rep = verify_extraction(tmp / "broken.docx", docx.resp)
        assert rep.status in (ValidationStatus.NOT_VERIFIABLE, ValidationStatus.REVIEW_REQUIRED) and rep.summary.verified == 0


# ============================================================================ real documents


REAL = ["01_cross_page_table.pdf", "02_merged_cells_multirow_header.pdf", "03_financial_number_formats.pdf", "04_charts.pdf",
        "05_equations.pdf", "06_mixed_digital_scanned.pdf", "07_report.docx", "08_deck.pptx", "09_financials.xlsx",
        "10_invoice_ocr.png", "10b_scanned_memo.png", "11_chart_image.jpg"]
EXPECTED_STATUS = {
    "01_cross_page_table.pdf": ValidationStatus.VERIFIED, "02_merged_cells_multirow_header.pdf": ValidationStatus.VERIFIED,
    "03_financial_number_formats.pdf": ValidationStatus.VERIFIED, "04_charts.pdf": ValidationStatus.VERIFIED,
    "07_report.docx": ValidationStatus.VERIFIED, "08_deck.pptx": ValidationStatus.VERIFIED, "09_financials.xlsx": ValidationStatus.VERIFIED,
    # honest limits: an equation page and scans cannot be independently verified
    "05_equations.pdf": ValidationStatus.NOT_VERIFIABLE, "06_mixed_digital_scanned.pdf": ValidationStatus.NOT_VERIFIABLE,
    "10_invoice_ocr.png": ValidationStatus.NOT_VERIFIABLE, "10b_scanned_memo.png": ValidationStatus.NOT_VERIFIABLE,
    # the extractor itself flagged two low-confidence OCR blocks for review
    "11_chart_image.jpg": ValidationStatus.REVIEW_REQUIRED,
}


@pytest.mark.parametrize("name", REAL)
def test_real_documents_raise_no_false_blocking_issues_and_get_the_honest_status(name):
    path = SAMPLES / name
    if not path.exists():
        pytest.skip("sample missing")
    t0 = time.perf_counter()
    resp = extract(path)
    extract_ms = (time.perf_counter() - t0) * 1000
    rep = run_verification(path, resp).report
    blocking = {(i.code, i.layer.value) for i in rep.issues if i.severity in (Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL)}
    assert rep.status == EXPECTED_STATUS[name], (rep.status, blocking)
    assert rep.failure is None
    if name == "11_chart_image.jpg":
        assert blocking == {("extractor_flagged_blocks", "extraction_integrity")}  # the extractor's own flag, nothing from Verify
    else:
        assert blocking == set(), blocking
    assert rep.timings_ms["total"] < 1500 and rep.timings_ms["total"] < extract_ms * 1.0 + 500
