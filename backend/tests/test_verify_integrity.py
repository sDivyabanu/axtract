"""AXTRACT Verify layer 6: extraction integrity.

Integrity says whether an extraction is well-formed and usable. It never says the content is right.
"""

from __future__ import annotations

import copy
import io
from pathlib import Path

import pytest

from models.document import BlockType, DocumentBlock, DocumentResponse
from models.errors import DocumentError
from verify.ids import IdSequence
from verify.integrity import CODE_CATALOG, MAX_BLOCK_IDS, UNIT_TYPE_BY_FORMAT, run_integrity
from verify.models import (
    CheckName,
    CheckOutcome,
    EvidenceKind,
    Severity,
    UnitType,
    ValidationReport,
    ValidationStatus,
)
from verify.rollup import build_report

T = BlockType
LONG = "Revenue grew strongly across every region this year."


def blk(id_="b1", page=1, type_=T.PARAGRAPH, content=LONG, bbox=(0.1, 0.1, 0.9, 0.2), ro=None,
        extractor="pymupdf", meta=None, review=False, conf=None) -> DocumentBlock:
    return DocumentBlock.model_construct(
        id=id_, type=type_, content=content, page=page, bbox=bbox, confidence=conf, extractor=extractor,
        reading_order=ro, requires_review=review, metadata=meta if meta is not None else {},
    )


def resp(blocks, file_type="pdf", page_count=2, errors=None, status=None, preview_pages=0) -> DocumentResponse:
    errors = errors or []
    return DocumentResponse.model_construct(
        document_id="d" * 32, filename=f"f.{file_type}", file_type=file_type, page_count=page_count,
        processing_time_ms=1, status=status or ("partial" if errors else "success"), blocks=blocks,
        markdown="", errors=errors, preview_available=bool(preview_pages), preview_pages=preview_pages,
        preview_error=None,
    )


def clean() -> DocumentResponse:
    return resp([
        blk("p1-b0", 1, T.HEADING, "Annual Report", (0.1, 0.05, 0.9, 0.1), 0, meta={"heading_level": 1}),
        blk("p1-b1", 1, T.PARAGRAPH, LONG, (0.1, 0.2, 0.9, 0.3), 1),
        blk("p2-b0", 2, T.PARAGRAPH, "Costs were flat quarter over quarter.", (0.1, 0.1, 0.9, 0.2), 2),
        blk("p2-b1", 2, T.TABLE, "A | B\n1 | 2", (0.1, 0.3, 0.9, 0.6), 3,
            meta={"rows": [["A", "B"], ["1", "2"]], "row_count": 2, "col_count": 2}),
    ])


def codes(outcome) -> dict[str, Severity]:
    return {i.code: i.severity for i in outcome.issues}


def only(outcome, code):
    found = [i for i in outcome.issues if i.code == code]
    assert found, f"{code} not raised; got {sorted(codes(outcome))}"
    return found[0]


class TestCleanExtraction:
    def test_a_well_formed_extraction_raises_nothing(self):
        out = run_integrity(clean())
        assert out.issues == []

    def test_every_page_gets_a_passing_structural_and_heuristic_check(self):
        out = run_integrity(clean())
        assert [u.unit.key for u in out.units] == ["page:1", "page:2"]
        for u in out.units:
            assert [(c.engine, c.outcome, c.evidence_kind) for c in u.checks] == [
                ("integrity.structural", CheckOutcome.PASSED, EvidenceKind.DETERMINISTIC),
                ("integrity.heuristic", CheckOutcome.PASSED, EvidenceKind.HEURISTIC),
            ]
            assert all(c.check == CheckName.EXTRACTION_INTEGRITY for c in u.checks)

    def test_the_check_says_it_proves_nothing_about_content(self):
        assert "says nothing about whether content is correct" in run_integrity(clean()).units[0].checks[0].summary

    def test_integrity_alone_can_never_verify_anything(self):
        report = build_report(run_integrity(clean()).units, run_integrity(clean()).issues)
        assert report.status == ValidationStatus.NOT_VERIFIABLE
        assert {u.status for u in report.units} == {ValidationStatus.NOT_VERIFIABLE}
        assert all("counts alone" in u.status_reasons[0] for u in report.units)

    def test_no_document_unit_appears_when_nothing_is_wrong_at_document_level(self):
        assert all(u.unit.type != UnitType.DOCUMENT for u in run_integrity(clean()).units)

    def test_it_is_pure(self):
        r = clean()
        before = copy.deepcopy(r.model_dump())
        run_integrity(r)
        assert r.model_dump() == before


class TestUnitTypesPerFormat:
    @pytest.mark.parametrize("fmt,unit", [("pdf", "page"), ("pptx", "slide"), ("xlsx", "sheet"), ("docx", "section"),
                                          ("png", "image"), ("jpg", "image"), ("jpeg", "image")])
    def test_units_follow_the_format(self, fmt, unit):
        meta = {"rows": [["a"]], "row_count": 1, "col_count": 1, "sheet_name": "S1"} if fmt == "xlsx" else {}
        typ = T.TABLE if fmt == "xlsx" else T.PARAGRAPH
        bbox = (0.1, 0.1, 0.5, 0.2) if fmt in ("pdf", "pptx", "png", "jpg", "jpeg") else None
        out = run_integrity(resp([blk("b", 1, typ, "cell value here", bbox, 0, meta=meta)], file_type=fmt, page_count=1))
        assert out.units[0].unit.type.value == unit and UNIT_TYPE_BY_FORMAT[fmt].value == unit

    def test_spreadsheet_units_are_labelled_with_the_sheet_name(self):
        b = blk("sh1-b0", 1, T.TABLE, "x | y", None, 0, meta={"rows": [["x", "y"]], "row_count": 1, "col_count": 2, "sheet_name": "Q3 Revenue"})
        assert run_integrity(resp([b], "xlsx", 1)).units[0].unit.label == "Q3 Revenue"


# --------------------------------------------------------------------------------------------
# One scenario per issue code. `GALLERY` also proves the catalogue is fully covered.
# --------------------------------------------------------------------------------------------


def _mut(fn):
    r = clean()
    fn(r)
    return r


def _bbox(v):
    return lambda r: setattr(r.blocks[1], "bbox", v)


GALLERY = {
    "empty_extraction": (lambda: resp([], page_count=2), Severity.HIGH),
    "invalid_page_count": (lambda: _mut(lambda r: setattr(r, "page_count", 0)), Severity.HIGH),
    "extractor_error": (lambda: _mut(lambda r: (r.errors.append(DocumentError(code="PAGE_ROUTING_FAILED", message="boom", page=2)), setattr(r, "status", "partial"))), Severity.HIGH),
    "partial_status_without_errors": (lambda: _mut(lambda r: setattr(r, "status", "partial")), Severity.MEDIUM),
    "errors_with_success_status": (lambda: _mut(lambda r: r.errors.append(DocumentError(code="X", message="m", page=1))), Severity.MEDIUM),
    "extractor_flagged_blocks": (lambda: _mut(lambda r: setattr(r.blocks[1], "requires_review", True)), Severity.MEDIUM),
    "duplicate_block_id": (lambda: _mut(lambda r: setattr(r.blocks[2], "id", "p1-b0")), Severity.HIGH),
    "missing_required_field": (lambda: _mut(lambda r: setattr(r.blocks[1], "id", "")), Severity.HIGH),
    "invalid_page_reference": (lambda: _mut(lambda r: setattr(r.blocks[2], "page", 9)), Severity.HIGH),
    "unit_reference_mismatch": (lambda: _mut(lambda r: (setattr(r, "file_type", "pptx"), r.blocks[1].metadata.update(slide_number=2))), Severity.HIGH),
    "invalid_bbox": (lambda: _mut(_bbox((0.1, 0.1, 1.4, 0.3))), Severity.HIGH),
    "degenerate_bbox": (lambda: _mut(_bbox((0.1, 0.2, 0.1, 0.4))), Severity.LOW),
    "missing_required_provenance": (lambda: _mut(_bbox(None)), Severity.MEDIUM),
    "invalid_confidence": (lambda: _mut(lambda r: setattr(r.blocks[1], "confidence", 1.7)), Severity.MEDIUM),
    "empty_block_content": (lambda: _mut(lambda r: setattr(r.blocks[1], "content", "   ")), Severity.MEDIUM),
    "invalid_block_content": (lambda: _mut(lambda r: setattr(r.blocks[1], "content", "bad\x00byte")), Severity.MEDIUM),
    "malformed_metadata": (lambda: _mut(lambda r: r.blocks[3].metadata.update(row_count=7)), Severity.MEDIUM),
    "malformed_preview_locator": (lambda: _mut(lambda r: r.blocks[1].metadata.update(preview={"page": 0, "bbox": None})), Severity.MEDIUM),
    "reading_order_missing": (lambda: _mut(lambda r: setattr(r.blocks[1], "reading_order", None)), Severity.MEDIUM),
    "invalid_reading_order": (lambda: _mut(lambda r: setattr(r.blocks[1], "reading_order", -4)), Severity.MEDIUM),
    "duplicate_reading_order": (lambda: _mut(lambda r: setattr(r.blocks[2], "reading_order", 1)), Severity.MEDIUM),
    "reading_order_not_monotonic": (lambda: _mut(lambda r: r.blocks.reverse()), Severity.MEDIUM),
    "duplicate_block": (lambda: _mut(lambda r: r.blocks.append(blk("dup", 1, T.PARAGRAPH, LONG.upper(), (0.1, 0.205, 0.9, 0.305), 4))), Severity.MEDIUM),
    "repeated_adjacent_block": (lambda: resp([blk("a", 1, T.PARAGRAPH, LONG, None, 0), blk("b", 1, T.PARAGRAPH, LONG, None, 1)], "docx", 1), Severity.LOW),
    "unit_without_blocks": (lambda: _mut(lambda r: r.blocks.__delitem__(slice(2, 4))), Severity.LOW),
}


class TestEveryIssueCode:
    def test_the_gallery_covers_exactly_the_catalogue(self):
        assert set(GALLERY) == set(CODE_CATALOG)

    @pytest.mark.parametrize("code", sorted(GALLERY))
    def test_each_code_is_detected_with_its_severity(self, code):
        build, severity = GALLERY[code]
        issue = only(run_integrity(build()), code)
        assert issue.severity == severity
        assert issue.layer == CheckName.EXTRACTION_INTEGRITY
        assert issue.evidence and issue.evidence[0].extracted, "every issue must say what was observed"
        assert issue.evidence[0].source.startswith("rule: ")

    @pytest.mark.parametrize("code", sorted(GALLERY))
    def test_each_scenario_trips_only_what_it_should(self, code):
        build, _ = GALLERY[code]
        raised = set(codes(run_integrity(build())))
        assert code in raised
        # a single mutation must not drag in unrelated structural findings
        allowed_companions = {"errors_with_success_status", "partial_status_without_errors", "unit_without_blocks",
                              "reading_order_missing", "reading_order_not_monotonic", "duplicate_reading_order",
                              "missing_required_provenance", "duplicate_block", "extractor_flagged_blocks",
                              "malformed_metadata", "empty_block_content", "invalid_page_reference",
                              "extractor_error"}
        assert raised - {code} <= allowed_companions, raised

    @pytest.mark.parametrize("code,expected_kind", [(c, v[1]) for c, v in CODE_CATALOG.items()])
    def test_catalogue_evidence_class_matches_what_is_emitted(self, code, expected_kind):
        issue = only(run_integrity(GALLERY[code][0]()), code)
        assert issue.evidence[0].kind == expected_kind
        assert issue.evidence[0].engine == ("integrity.heuristic" if expected_kind == EvidenceKind.HEURISTIC else "integrity.structural")


class TestDocumentLevel:
    def test_empty_extraction_becomes_critical_when_the_extractor_also_reported_errors(self):
        r = resp([], errors=[DocumentError(code="DOCUMENT_EXTRACTION_FAILED", message="x", page=1)])
        out = run_integrity(r)
        assert codes(out)["empty_extraction"] == Severity.CRITICAL

    def test_an_empty_extraction_does_not_flood_with_per_page_issues(self):
        assert "unit_without_blocks" not in codes(run_integrity(resp([], page_count=40)))

    def test_losing_a_whole_unit_is_high_but_one_unreadable_object_is_medium(self):
        lost = run_integrity(resp(clean().blocks, errors=[DocumentError(code="SHEET_EXTRACTION_FAILED", message="m", page=1)]))
        small = run_integrity(resp(clean().blocks, errors=[DocumentError(code="CHART_PARSE_FAILED", message="m", page=1)]))
        assert codes(lost)["extractor_error"] == Severity.HIGH and codes(small)["extractor_error"] == Severity.MEDIUM

    def test_extractor_errors_are_attached_to_their_page(self):
        out = run_integrity(resp(clean().blocks, errors=[DocumentError(code="PAGE_ROUTING_FAILED", message="m", page=2)]))
        assert only(out, "extractor_error").locator.unit.key == "page:2"

    def test_an_error_with_no_page_lands_on_the_document_unit(self):
        out = run_integrity(resp(clean().blocks, errors=[DocumentError(code="PAGE_ROUTING_FAILED", message="m")]))
        assert only(out, "extractor_error").locator.unit.key == "document:1"
        assert out.units[0].unit.type == UnitType.DOCUMENT

    def test_the_error_message_is_kept_as_evidence(self):
        out = run_integrity(resp(clean().blocks, errors=[DocumentError(code="PAGE_ROUTING_FAILED", message="bad xref", page=1)]))
        assert "bad xref" in only(out, "extractor_error").evidence[0].extracted


class TestBlockRules:
    def test_both_copies_of_a_duplicate_id_are_reported_once_on_the_document(self):
        r = clean()
        r.blocks[2].id = "p1-b0"
        i = only(run_integrity(r), "duplicate_block_id")
        assert i.locator.unit.key == "document:1" and i.evidence[0].detail["count"] == 1

    @pytest.mark.parametrize("bbox,fragment", [
        ((0.1, 0.1, 1.4, 0.3), "outside"), ((0.6, 0.1, 0.4, 0.3), "inverted"), ((float("nan"), 0, 0.5, 0.5), "non-finite"),
        ((0.1, 0.1, 0.5), "exactly 4"), (-0.2, "not a sequence"),
    ])
    def test_invalid_bbox_forms(self, bbox, fragment):
        r = clean()
        r.blocks[1].bbox = bbox
        i = only(run_integrity(r), "invalid_bbox")
        assert fragment in (i.evidence[0].extracted + str(i.evidence[0].detail))

    def test_an_invalid_bbox_is_never_copied_into_the_locator(self):
        r = clean()
        r.blocks[1].bbox = (0.1, 0.1, 1.4, 0.3)
        assert only(run_integrity(r), "invalid_bbox").locator.bbox is None

    def test_a_valid_bbox_is_carried_into_the_locator_so_the_ui_can_open_it(self):
        r = clean()
        r.blocks[1].confidence = 5
        i = only(run_integrity(r), "invalid_confidence")
        assert i.locator.bbox == (0.1, 0.2, 0.9, 0.3) and i.locator.block_ids == ["p1-b1"] and i.locator.page == 1

    def test_missing_bbox_is_only_required_where_the_format_has_coordinates(self):
        flow = resp([blk("a", 1, T.PARAGRAPH, LONG, None, 0)], "docx", 1)
        sheet = resp([blk("a", 1, T.TABLE, "x", None, 0, meta={"rows": [["x"]], "row_count": 1, "col_count": 1, "sheet_name": "S"})], "xlsx", 1)
        assert "missing_required_provenance" not in codes(run_integrity(flow))
        assert "missing_required_provenance" not in codes(run_integrity(sheet))
        for fmt in ("pdf", "pptx", "png", "jpg", "jpeg"):
            assert "missing_required_provenance" in codes(run_integrity(resp([blk("a", 1, T.PARAGRAPH, LONG, None, 0)], fmt, 1)))

    @pytest.mark.parametrize("page", [0, -1, 3, 99])
    def test_page_outside_one_to_page_count(self, page):
        r = clean()
        r.blocks[0].page = page
        assert only(run_integrity(r), "invalid_page_reference").locator.unit.key == "document:1"

    def test_a_page_reference_is_not_an_issue_when_it_is_in_range(self):
        assert "invalid_page_reference" not in codes(run_integrity(clean()))

    @pytest.mark.parametrize("content,allowed", [("", False), ("   \n", False)])
    def test_text_blocks_must_have_text(self, content, allowed):
        for t in (T.HEADING, T.PARAGRAPH, T.LIST, T.HEADER, T.FOOTER):
            r = clean()
            r.blocks[1].type, r.blocks[1].content = t, content
            assert ("empty_block_content" not in codes(run_integrity(r))) == allowed

    def test_figures_and_unknown_may_be_empty(self):
        for t in (T.FIGURE, T.UNKNOWN):
            r = clean()
            r.blocks[1].type, r.blocks[1].content = t, ""
            assert "empty_block_content" not in codes(run_integrity(r))

    def test_an_empty_chart_is_fine_only_when_the_extractor_said_why(self):
        r = clean()
        r.blocks[1].type, r.blocks[1].content = T.CHART, ""
        assert "empty_block_content" in codes(run_integrity(r))
        r.blocks[1].metadata["flags"] = ["chart_data_unreadable"]
        out = run_integrity(r)
        assert "empty_block_content" not in codes(out) and "extractor_flagged_blocks" in codes(out)

    def test_a_table_with_cells_but_no_text_is_not_empty(self):
        r = clean()
        r.blocks[3].content = ""
        assert "empty_block_content" not in codes(run_integrity(r))
        r.blocks[3].metadata["rows"] = []
        r.blocks[3].metadata["row_count"] = 0
        assert "empty_block_content" in codes(run_integrity(r))

    def test_replacement_characters_scale_with_how_much_is_damaged(self):
        r = clean()
        r.blocks[1].content = LONG + "�"
        assert codes(run_integrity(r))["invalid_block_content"] == Severity.LOW
        r.blocks[1].content = "�" * 20 + "ab"
        assert codes(run_integrity(r))["invalid_block_content"] == Severity.MEDIUM

    def test_non_text_content_is_high(self):
        r = clean()
        r.blocks[1].content = 12345
        assert codes(run_integrity(r))["invalid_block_content"] == Severity.HIGH

    def test_tabs_and_newlines_are_not_control_character_damage(self):
        r = clean()
        r.blocks[1].content = "col1\tcol2\nrow two"
        assert "invalid_block_content" not in codes(run_integrity(r))

    def test_confidence_null_and_boundaries_are_valid(self):
        for c in (None, 0, 0.0, 1, 1.0, 0.42):
            r = clean()
            r.blocks[1].confidence = c
            assert "invalid_confidence" not in codes(run_integrity(r))
        for c in (-0.01, 1.01, float("nan"), "high"):
            r = clean()
            r.blocks[1].confidence = c
            assert "invalid_confidence" in codes(run_integrity(r)), c


class TestMetadataRules:
    def test_table_without_rows(self):
        r = clean()
        del r.blocks[3].metadata["rows"]
        assert "malformed_metadata" in codes(run_integrity(r))

    def test_table_rows_must_be_lists_of_lists(self):
        r = clean()
        r.blocks[3].metadata["rows"] = ["A | B"]
        assert "malformed_metadata" in codes(run_integrity(r))

    def test_row_count_mismatch_is_medium_but_col_count_mismatch_is_low(self):
        r = clean()
        r.blocks[3].metadata["col_count"] = 5
        sev = codes(run_integrity(r))["malformed_metadata"]
        assert sev == Severity.LOW
        r.blocks[3].metadata["row_count"] = 9
        assert codes(run_integrity(r))["malformed_metadata"] == Severity.MEDIUM

    @pytest.mark.parametrize("level", [0, 7, "1", 2.5, True])
    def test_heading_level_must_be_one_to_six(self, level):
        r = clean()
        r.blocks[0].metadata["heading_level"] = level
        assert "malformed_metadata" in codes(run_integrity(r))

    def test_headings_without_a_level_are_fine(self):
        r = clean()
        r.blocks[0].metadata.pop("heading_level")
        assert run_integrity(r).issues == []

    def test_spreadsheet_tables_need_a_sheet_name(self):
        b = blk("sh1-b0", 1, T.TABLE, "x", None, 0, meta={"rows": [["x"]], "row_count": 1, "col_count": 1})
        assert "malformed_metadata" in codes(run_integrity(resp([b], "xlsx", 1)))

    def test_cross_page_merged_tables_are_valid_and_do_not_leave_phantom_empty_pages(self):
        merged = blk("p1-b0-merged", 1, T.TABLE, "h\n1\n2\n3", (0.1, 0.1, 0.9, 0.9), 0, meta={
            "rows": [["h"], ["1"], ["2"], ["3"]], "row_count": 4, "col_count": 1,
            "merged_from_pages": [1, 2, 3], "is_cross_page_merged": True,
            "part_bboxes": [{"page": 1, "bbox": (0.1, 0.1, 0.9, 0.9)}, {"page": 2, "bbox": (0.1, 0.1, 0.9, 0.5)}]})
        out = run_integrity(resp([merged], "pdf", 3))
        assert out.issues == []

    def test_merged_table_pointing_outside_the_document(self):
        merged = blk("m", 1, T.TABLE, "h\n1", (0.1, 0.1, 0.9, 0.9), 0, meta={
            "rows": [["h"], ["1"]], "row_count": 2, "col_count": 1, "merged_from_pages": [1, 8],
            "part_bboxes": [{"page": 1, "bbox": (0.5, 0.5, 0.1, 0.1)}]})
        i = only(run_integrity(resp([merged], "pdf", 2)), "malformed_metadata")
        assert i.evidence[0].detail["count"] == 2

    def test_non_object_metadata_is_reported_not_crashed_on(self):
        r = clean()
        r.blocks[1].metadata = "oops"
        assert "malformed_metadata" in codes(run_integrity(r))

    def test_pptx_slide_number_must_match_the_page(self):
        r = resp([blk("a", 2, T.PARAGRAPH, LONG, (0.1, 0.1, 0.5, 0.2), 0, meta={"slide_number": 3})], "pptx", 3)
        i = only(run_integrity(r), "unit_reference_mismatch")
        assert i.locator.unit.key == "slide:2"

    @pytest.mark.parametrize("preview", [{"page": 0}, {"page": "2"}, {"page": 99}, {"page": 1, "bbox": [0, 0, 2, 2]},
                                         "nope", {"page": 1, "bbox": 5}])
    def test_preview_locators_the_viewer_cannot_draw(self, preview):
        r = resp([blk("a", 1, T.PARAGRAPH, LONG, None, 0, meta={"preview": preview})], "docx", 1, preview_pages=2)
        assert "malformed_preview_locator" in codes(run_integrity(r))

    def test_valid_preview_locators_pass(self):
        r = resp([blk("a", 1, T.PARAGRAPH, LONG, None, 0, meta={"preview": {"page": 2, "bbox": [0.1, 0.1, 0.5, 0.2]}}),
                  blk("b", 1, T.PARAGRAPH, "Another paragraph of text.", None, 1, meta={"preview": {"page": 1, "bbox": None}})],
                 "docx", 1, preview_pages=2)
        assert run_integrity(r).issues == []

    def test_extractor_flags_are_surfaced_with_their_names(self):
        r = clean()
        r.blocks[1].metadata["flags"] = ["chart_data_unreadable", "x_axis_not_calibrated"]
        i = only(run_integrity(r), "extractor_flagged_blocks")
        assert i.evidence[0].detail["flags"] == ["chart_data_unreadable", "x_axis_not_calibrated"]


class TestReadingOrder:
    def test_no_reading_order_at_all(self):
        r = clean()
        for b in r.blocks:
            b.reading_order = None
        i = only(run_integrity(r), "reading_order_missing")
        assert "No block" in i.message

    def test_some_blocks_missing_it(self):
        r = clean()
        r.blocks[1].reading_order = None
        assert "1 of 4" in only(run_integrity(r), "reading_order_missing").message

    @pytest.mark.parametrize("bad", [-1, 1.5, "3"])
    def test_invalid_values(self, bad):
        r = clean()
        r.blocks[1].reading_order = bad
        assert "invalid_reading_order" in codes(run_integrity(r))

    def test_zero_is_a_valid_first_position(self):
        assert "invalid_reading_order" not in codes(run_integrity(clean()))

    def test_duplicates(self):
        r = clean()
        r.blocks[3].reading_order = 0
        assert "duplicate_reading_order" in codes(run_integrity(r))

    def test_list_must_follow_reading_order(self):
        r = clean()
        r.blocks[0], r.blocks[1] = r.blocks[1], r.blocks[0]
        i = only(run_integrity(r), "reading_order_not_monotonic")
        assert "followed by" in i.evidence[0].extracted

    def test_gaps_in_the_sequence_are_not_an_integrity_problem(self):
        r = clean()
        for b, v in zip(r.blocks, (0, 5, 9, 40)):
            b.reading_order = v
        assert run_integrity(r).issues == []


class TestHeuristicRules:
    def test_identical_overlapping_text_is_flagged_as_a_probable_double_extraction(self):
        r = clean()
        r.blocks.append(blk("mix-ocr", 1, T.PARAGRAPH, LONG, (0.11, 0.2, 0.9, 0.3), 4))
        i = only(run_integrity(r), "duplicate_block")
        assert i.evidence[0].kind == EvidenceKind.HEURISTIC and i.locator.unit.key == "page:1"

    def test_identical_text_in_different_places_is_legitimate_repetition(self):
        r = clean()
        r.blocks.append(blk("far", 1, T.PARAGRAPH, LONG, (0.1, 0.7, 0.9, 0.8), 4))
        assert "duplicate_block" not in codes(run_integrity(r))

    def test_the_same_text_on_different_pages_is_not_a_duplicate(self):
        r = clean()
        r.blocks.append(blk("p2-same", 2, T.PARAGRAPH, LONG, (0.1, 0.2, 0.9, 0.3), 4))
        assert "duplicate_block" not in codes(run_integrity(r))

    def test_short_text_is_never_called_a_duplicate(self):
        r = clean()
        r.blocks += [blk("t1", 1, T.PARAGRAPH, "Total", (0.1, 0.8, 0.3, 0.85), 4), blk("t2", 1, T.PARAGRAPH, "Total", (0.1, 0.8, 0.3, 0.85), 5)]
        assert "duplicate_block" not in codes(run_integrity(r))

    def test_different_block_types_with_the_same_text_are_not_duplicates(self):
        r = clean()
        r.blocks.append(blk("h", 1, T.HEADING, LONG, (0.1, 0.2, 0.9, 0.3), 4))
        assert "duplicate_block" not in codes(run_integrity(r))

    def test_repeated_adjacent_paragraphs_in_flow_formats_are_only_low(self):
        out = run_integrity(GALLERY["repeated_adjacent_block"][0]())
        assert codes(out)["repeated_adjacent_block"] == Severity.LOW

    def test_non_adjacent_repetition_in_flow_formats_is_ignored(self):
        mid = blk("m", 1, T.PARAGRAPH, "Something different in the middle.", None, 1)
        r = resp([blk("a", 1, T.PARAGRAPH, LONG, None, 0), mid, blk("c", 1, T.PARAGRAPH, LONG, None, 2)], "docx", 1)
        assert "repeated_adjacent_block" not in codes(run_integrity(r))

    def test_blank_pages_are_noted_but_never_block_a_unit(self):
        out = run_integrity(GALLERY["unit_without_blocks"][0]())
        assert codes(out)["unit_without_blocks"] == Severity.LOW
        report = build_report(out.units, out.issues)
        assert "low" in " ".join(next(u for u in report.units if u.unit.key == "page:2").status_reasons)


class TestAggregationAndLocators:
    def test_a_badly_broken_extraction_yields_one_issue_per_code_and_unit_not_one_per_block(self):
        blocks = [blk(f"b{i}", 1, T.PARAGRAPH, f"Paragraph number {i} with text.", (0.1, 0.1, 1.5, 0.2), i) for i in range(200)]
        out = run_integrity(resp(blocks, page_count=1))
        bad = [i for i in out.issues if i.code == "invalid_bbox"]
        assert len(bad) == 1 and bad[0].evidence[0].detail["count"] == 200
        assert len(bad[0].locator.block_ids) == MAX_BLOCK_IDS and len(bad[0].evidence[0].extracted) < 2000

    def test_repeated_findings_keep_the_worst_severity(self):
        r = clean()
        r.blocks[0].metadata["heading_level"] = 9
        r.blocks[3].metadata["col_count"] = 9  # low
        r.blocks[3].metadata["row_count"] = 9  # medium, same unit+code as nothing else; different unit
        assert codes(run_integrity(r))["malformed_metadata"] in (Severity.MEDIUM, Severity.LOW)
        r2 = clean()
        r2.blocks[2].metadata["heading_level"] = 9
        r2.blocks[2].type = T.HEADING
        r2.blocks[3].metadata["col_count"] = 9
        r2.blocks[3].metadata["row_count"] = 9
        page2 = [i for i in run_integrity(r2).issues if i.code == "malformed_metadata" and i.locator.unit.key == "page:2"]
        assert len(page2) == 1 and page2[0].severity == Severity.MEDIUM

    def test_ids_are_unique_sequential_and_shared_sequences_never_collide(self):
        seq = IdSequence("iss")
        a, b = run_integrity(GALLERY["invalid_bbox"][0](), seq), run_integrity(GALLERY["duplicate_block_id"][0](), seq)
        ids = [i.id for i in a.issues + b.issues]
        assert ids == sorted(ids) and len(set(ids)) == len(ids) == 2 and ids[0] == "iss-0001"

    def test_output_is_deterministic(self):
        a = run_integrity(GALLERY["invalid_bbox"][0]())
        b = run_integrity(GALLERY["invalid_bbox"][0]())
        assert [i.model_dump() for i in a.issues] == [i.model_dump() for i in b.issues]

    def test_huge_page_counts_do_not_explode_into_thousands_of_units(self):
        out = run_integrity(resp([blk("a", 1, T.PARAGRAPH, LONG, (0.1, 0.1, 0.5, 0.2), 0)], page_count=10**6))
        assert len(out.units) == 1

    def test_stats_and_timing_are_reported(self):
        out = run_integrity(clean())
        assert out.stats["blocks"] == 4 and out.stats["issues"] == 0 and out.duration_ms >= 0


class TestCheckOutcomes:
    def test_a_medium_or_worse_issue_fails_the_structural_check(self):
        out = run_integrity(GALLERY["invalid_bbox"][0]())
        c = next(u for u in out.units if u.unit.key == "page:1").checks[0]
        assert c.outcome == CheckOutcome.FAILED and c.issue_ids == [out.issues[0].id]

    def test_a_low_issue_is_recorded_on_a_passing_check(self):
        out = run_integrity(GALLERY["degenerate_bbox"][0]())
        c = next(u for u in out.units if u.unit.key == "page:1").checks[0]
        assert c.outcome == CheckOutcome.PASSED and c.issue_ids == [out.issues[0].id]

    def test_a_low_issue_never_changes_a_units_status(self):
        out = run_integrity(GALLERY["degenerate_bbox"][0]())
        report = build_report(out.units, out.issues)
        assert next(u for u in report.units if u.unit.key == "page:1").status == ValidationStatus.NOT_VERIFIABLE

    def test_heuristic_failure_is_labelled_heuristic_and_still_requires_review(self):
        out = run_integrity(GALLERY["duplicate_block"][0]())
        c = next(u for u in out.units if u.unit.key == "page:1").checks[1]
        assert (c.evidence_kind, c.outcome) == (EvidenceKind.HEURISTIC, CheckOutcome.FAILED)
        assert build_report(out.units, out.issues).status == ValidationStatus.REVIEW_REQUIRED


class TestRollupIntegration:
    def test_critical_integrity_problems_fail_the_unit_and_the_document(self):
        r = resp([], errors=[DocumentError(code="DOCUMENT_EXTRACTION_FAILED", message="m", page=1)])
        out = run_integrity(r)
        report = build_report(out.units, out.issues)
        assert report.status == ValidationStatus.FAILED
        assert ValidationReport.model_validate_json(report.model_dump_json()) == report

    def test_high_issue_means_review_required(self):
        out = run_integrity(GALLERY["duplicate_block_id"][0]())
        assert build_report(out.units, out.issues).status == ValidationStatus.REVIEW_REQUIRED

    def test_a_processing_success_is_not_a_validation_success(self):
        r = GALLERY["invalid_bbox"][0]()
        assert r.status == "success"  # extraction "worked"
        out = run_integrity(r)
        assert build_report(out.units, out.issues).status != ValidationStatus.VERIFIED

    def test_a_full_report_from_integrity_alone_is_self_consistent(self):
        out = run_integrity(GALLERY["extractor_error"][0]())
        report = build_report(out.units, out.issues, timings_ms={"integrity": out.duration_ms})
        assert report.summary.issues_total == len(out.issues) and report.checks[CheckName.EXTRACTION_INTEGRITY]


class TestRobustness:
    def test_hostile_blocks_never_raise(self):
        weird = [
            blk("a", "x", T.PARAGRAPH, None, "bbox", "ro", extractor=None, meta=[1, 2], conf="hi"),
            blk(None, None, T.TABLE, 5, (1, 2), object(), extractor=3, meta={"rows": 7, "part_bboxes": "x", "merged_from_pages": [None]}),
            blk("c", True, T.HEADING, "", (None,) * 4, -1, meta={"heading_level": [], "preview": {"page": None}}),
        ]
        out = run_integrity(resp(weird, page_count=3))
        assert out.issues  # reported, not raised
        build_report(out.units, out.issues)  # and still assemble into a valid report

    def test_empty_everything(self):
        out = run_integrity(resp([], page_count=1))
        assert {"empty_extraction"} <= set(codes(out))


SAMPLES = Path(__file__).resolve().parents[2] / "sample_files"


@pytest.mark.parametrize("name", ["01_cross_page_table.pdf", "07_report.docx", "08_deck.pptx", "09_financials.xlsx", "10_invoice_ocr.png"])
def test_real_clean_extractions_raise_nothing(name):
    """Calibration: the rules must not cry wolf on documents AXTRACT extracts correctly."""
    from starlette.datastructures import UploadFile

    from services.parse_service import parse_upload

    path = SAMPLES / name
    if not path.exists():
        pytest.skip("sample file missing")
    result = parse_upload(UploadFile(file=io.BytesIO(path.read_bytes()), filename=name))
    out = run_integrity(result)
    assert out.issues == [], [(i.code, i.evidence[0].extracted) for i in out.issues]
    assert out.duration_ms < 250
