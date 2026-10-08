"""Regression coverage for conservative PyMuPDF block extraction."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extractors.pymupdf_extractor import (
    PyMuPDFExtractor,
    _has_aligned_text_columns,
    _recover_invalid_text_bbox,
    table_plausibility,
)
from models.document import BlockType, DocumentBlock
from services.layout_service import assign_reading_order, deduplicate_blocks


FIXTURE = Path(__file__).resolve().parents[2] / "evaluation" / "fixtures" / "pymupdf_block_quality_pages.pdf"


def test_invalid_text_block_bbox_recovers_matching_span_geometry():
    page_dict = {"blocks": [{
        "type": 0,
        "bbox": (57.0, 211.6, 283.1, 225.7),
        "lines": [{"spans": [{"text": "Question 1: Solve the following equations"}]}],
    }]}
    assert _recover_invalid_text_bbox("Question 1 Solve the following equations", page_dict) == (
        57.0, 211.6, 283.1, 225.7
    )


def _blocks_by_page():
    return PyMuPDFExtractor().extract(FIXTURE).blocks


def test_table_plausibility_gate():
    plausible, reasons = table_plausibility([["Year", "2025"], ["Revenue", "10"]], 2)
    assert plausible
    assert reasons == []

    plausible, reasons = table_plausibility([["A very long " + "word " * 25, None], [None, None]], 2)
    assert not plausible
    assert "sparse_grid" in reasons
    assert "giant_cell" in reasons

    plausible, reasons = table_plausibility([["single cell"]], 1)
    assert not plausible
    assert "insufficient_dimensions" in reasons

    # Merged/sparse layouts are retained when row and column structure repeats.
    plausible, reasons = table_plausibility([
        ["Group", "Year", "Value", None, None, None, None, None],
        ["A", "2025", "10", None, None, None, None, None],
        ["B", "2024", "12", None, None, None, None, None],
    ], 8)
    assert plausible
    assert "sparse_grid" in reasons

    plausible, reasons = table_plausibility([
        ["", "2011", None, "2010 restated", None],
        ["General income", "", "250,000", "", "200,000"],
        ["Staff costs", "(200,000)", "", "(150,000)", ""],
        ["Surplus", "", "5,000", "", "30,000"],
    ], 5)
    assert plausible
    assert "sparse_cells" in reasons

    plausible, reasons = table_plausibility([
        ["Rainfall", "Americas", "Asia"],
        ["2010", None, None],
        ["Average", "104", "201"],
        ["2009", None, None],
        ["Average", "133", "244"],
    ], 3)
    assert plausible
    assert "grouped_header_rows" in reasons


def test_text_only_aligned_columns_are_a_table_candidate_signal():
    words = []
    for row, (label, detail) in enumerate([
        ("Area", "Remaining"), ("OCR", "Scanned"), ("Tables", "Detection"),
        ("Output", "Markdown"),
    ]):
        words.extend([
            (10.0, 20.0 + row * 14, 42.0, 30.0 + row * 14, label, row, 0, 0),
            (120.0, 20.0 + row * 14, 180.0, 30.0 + row * 14, detail, row, 0, 1),
        ])
    assert _has_aligned_text_columns(words)


def test_deduplicate_uses_text_and_bbox_iou_and_records_source():
    first = DocumentBlock(id="first", type=BlockType.PARAGRAPH, content="Same text",
                          page=1, bbox=(0.1, 0.1, 0.5, 0.2), extractor="test")
    duplicate = DocumentBlock(id="duplicate", type=BlockType.TABLE, content="SAME text!",
                              page=1, bbox=(0.105, 0.105, 0.505, 0.205), extractor="test")
    distinct = DocumentBlock(id="distinct", type=BlockType.PARAGRAPH, content="Same text",
                             page=1, bbox=(0.6, 0.1, 0.9, 0.2), extractor="test")

    result = deduplicate_blocks([first, duplicate, distinct])
    assert [block.id for block in result] == ["first", "distinct"]
    assert result[0].metadata["deduped_from"] == ["duplicate"]
    ordered = assign_reading_order([first, duplicate, distinct])
    assert [block.reading_order for block in ordered] == [0, 1]


def test_financial_highlights_text_and_footnotes_are_separated():
    extractor = PyMuPDFExtractor()
    blocks = [block for block in extractor.extract(FIXTURE).blocks if block.page == 1]
    content = "\n".join(block.content for block in blocks)
    assert "dataTotal net revenue" not in content
    assert "70,932Provision for credit losses" not in content
    assert any("Total net revenue" in block.content for block in blocks)
    assert any("Provision for credit losses" in block.content for block in blocks)
    assert not any(block.type == BlockType.HEADING and "in millions" in block.content.lower()
                   for block in blocks)
    assert not any("(g)" in block.content or "(e)" in block.content for block in blocks)
    assert any(block.metadata.get("footnote_refs") for block in blocks)
    assert all(block.confidence is None for block in blocks)
    assert extractor.page_diagnostics[1]["table_detection_count"] <= 2
    assert extractor.page_diagnostics[1]["block_count"] == len(blocks)


def test_chart_grid_becomes_reviewable_figure_not_giant_table():
    blocks = [block for block in _blocks_by_page() if block.page == 2]
    assert not any(block.type == BlockType.TABLE and any(
        len(str(cell).split()) > 25 for row in block.metadata.get("rows", []) for cell in row
    ) for block in blocks)
    figures = [block for block in blocks if block.type == BlockType.FIGURE]
    assert figures
    assert any(block.requires_review and block.metadata.get("reason_flags")
               and "chart_signal" in block.metadata["reason_flags"] for block in figures)
    assert all("rows" not in block.metadata and len(block.content) <= 5000 for block in figures)
    assert not any(block.type == BlockType.PARAGRAPH and block.content.strip() == "$2,061"
                   for block in blocks)


def test_page_fourteen_chart_is_not_table_and_markers_are_detached():
    blocks = [block for block in _blocks_by_page() if block.page == 3]
    assert not any(block.type == BlockType.TABLE for block in blocks)
    assert any(block.type == BlockType.FIGURE and block.metadata.get("raw_labels") for block in blocks)
    assert not any("ratio2 ROTCE" in block.content or "ROTCE4, 6" in block.content for block in blocks)
    ordered = assign_reading_order(blocks)
    assert len(deduplicate_blocks(ordered)) == len(ordered)
    assert [block.reading_order for block in ordered] == list(range(len(ordered)))
