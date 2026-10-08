"""Tests for services: layout, markdown, PDF routing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.document import BlockType, DocumentBlock
from services.layout_service import assign_reading_order
from services.markdown_service import blocks_to_markdown


class TestLayoutService:
    def test_assigns_reading_order(self):
        blocks = [
            DocumentBlock(
                id="1", type=BlockType.PARAGRAPH, content="First",
                page=1, bbox=(0.1, 0.5, 0.9, 0.6), extractor="test",
            ),
            DocumentBlock(
                id="2", type=BlockType.HEADING, content="Title",
                page=1, bbox=(0.1, 0.1, 0.9, 0.2), extractor="test",
            ),
        ]
        ordered = assign_reading_order(blocks)
        assert ordered[0].content == "Title"  # Should be first (higher on page)
        assert ordered[0].reading_order == 0
        assert ordered[1].reading_order == 1

    def test_header_footer_detection(self):
        blocks = [
            DocumentBlock(
                id="h1", type=BlockType.PARAGRAPH, content="Annual Report",
                page=1, bbox=(0.1, 0.02, 0.5, 0.05), extractor="test", metadata={"route": "digital"},
            ),
            DocumentBlock(
                id="b", type=BlockType.PARAGRAPH, content="Body text",
                page=1, bbox=(0.1, 0.3, 0.9, 0.5), extractor="test", metadata={"route": "digital"},
            ),
            DocumentBlock(
                id="f", type=BlockType.PARAGRAPH, content="Page 1",
                page=1, bbox=(0.4, 0.95, 0.6, 0.98), extractor="test", metadata={"route": "digital"},
            ),
            DocumentBlock(
                id="h2", type=BlockType.PARAGRAPH, content="Annual Report",
                page=2, bbox=(0.1, 0.02, 0.5, 0.05), extractor="test", metadata={"route": "digital"},
            ),
            DocumentBlock(
                id="f2", type=BlockType.PARAGRAPH, content="Page 2",
                page=2, bbox=(0.4, 0.95, 0.6, 0.98), extractor="test", metadata={"route": "digital"},
            ),
            DocumentBlock(
                id="oneoff", type=BlockType.PARAGRAPH, content="Section opener",
                page=3, bbox=(0.1, 0.02, 0.5, 0.05), extractor="test", metadata={"route": "digital"},
            ),
        ]
        ordered = assign_reading_order(blocks)
        types = {b.id: b.type for b in ordered}
        assert types["h1"] == BlockType.HEADER
        assert types["h2"] == BlockType.HEADER
        assert types["b"] == BlockType.PARAGRAPH
        assert types["f"] == BlockType.FOOTER
        assert types["f2"] == BlockType.FOOTER
        assert types["oneoff"] == BlockType.PARAGRAPH

    def test_no_header_footer_for_non_page_content(self):
        """Slides / standalone images have no running headers, whatever their position."""
        blocks = [
            DocumentBlock(
                id="t", type=BlockType.PARAGRAPH, content="Slide subtitle",
                page=1, bbox=(0.1, 0.02, 0.5, 0.05), extractor="test",
            ),
        ]
        assert assign_reading_order(blocks)[0].type == BlockType.PARAGRAPH

    def test_multi_column_ordering(self):
        # Two-column layout with enough blocks for detection
        blocks = [
            DocumentBlock(
                id="r1", type=BlockType.PARAGRAPH, content="Right column top",
                page=1, bbox=(0.55, 0.1, 0.95, 0.3), extractor="test",
            ),
            DocumentBlock(
                id="r2", type=BlockType.PARAGRAPH, content="Right column bottom",
                page=1, bbox=(0.55, 0.4, 0.95, 0.6), extractor="test",
            ),
            DocumentBlock(
                id="l1", type=BlockType.PARAGRAPH, content="Left column top",
                page=1, bbox=(0.05, 0.1, 0.45, 0.3), extractor="test",
            ),
            DocumentBlock(
                id="l2", type=BlockType.PARAGRAPH, content="Left column bottom",
                page=1, bbox=(0.05, 0.4, 0.45, 0.6), extractor="test",
            ),
        ]
        ordered = assign_reading_order(blocks)
        contents = [b.content for b in ordered]
        # Left column should come before right column
        assert contents.index("Left column top") < contents.index("Right column top")
        assert contents.index("Left column bottom") < contents.index("Right column top")

    def test_blocks_without_bbox(self):
        blocks = [
            DocumentBlock(
                id="1", type=BlockType.PARAGRAPH, content="No bbox",
                page=1, extractor="test",
            ),
        ]
        ordered = assign_reading_order(blocks)
        assert len(ordered) == 1
        assert ordered[0].reading_order == 0

    def test_multi_page_ordering(self):
        blocks = [
            DocumentBlock(
                id="p2", type=BlockType.PARAGRAPH, content="Page 2",
                page=2, bbox=(0.1, 0.1, 0.9, 0.3), extractor="test",
            ),
            DocumentBlock(
                id="p1", type=BlockType.PARAGRAPH, content="Page 1",
                page=1, bbox=(0.1, 0.1, 0.9, 0.3), extractor="test",
            ),
        ]
        ordered = assign_reading_order(blocks)
        assert ordered[0].content == "Page 1"
        assert ordered[1].content == "Page 2"


class TestMarkdownService:
    def test_heading_levels(self):
        blocks = [
            DocumentBlock(
                id="1", type=BlockType.HEADING, content="Main Title",
                page=1, extractor="test", reading_order=0,
                metadata={"heading_level": 1},
            ),
            DocumentBlock(
                id="2", type=BlockType.HEADING, content="Sub Section",
                page=1, extractor="test", reading_order=1,
                metadata={"heading_level": 2},
            ),
        ]
        md = blocks_to_markdown(blocks)
        assert "# Main Title" in md
        assert "## Sub Section" in md

    def test_table_markdown(self):
        blocks = [
            DocumentBlock(
                id="1", type=BlockType.TABLE, content="A|B",
                page=1, extractor="test", reading_order=0,
                metadata={"rows": [["A", "B"], ["1", "2"]]},
            ),
        ]
        md = blocks_to_markdown(blocks)
        assert "| A | B |" in md
        assert "| --- | --- |" in md
        assert "| 1 | 2 |" in md

    def test_list_items(self):
        blocks = [
            DocumentBlock(
                id="1", type=BlockType.LIST, content="• Item one\n• Item two",
                page=1, extractor="test", reading_order=0,
            ),
        ]
        md = blocks_to_markdown(blocks)
        assert "- Item one" in md
        assert "- Item two" in md

    def test_page_breaks(self):
        blocks = [
            DocumentBlock(
                id="1", type=BlockType.PARAGRAPH, content="Page 1 text",
                page=1, extractor="test", reading_order=0,
            ),
            DocumentBlock(
                id="2", type=BlockType.PARAGRAPH, content="Page 2 text",
                page=2, extractor="test", reading_order=1,
            ),
        ]
        md = blocks_to_markdown(blocks)
        assert "---" in md

    def test_empty_blocks(self):
        md = blocks_to_markdown([])
        assert md == ""

    def test_equation_latex(self):
        blocks = [
            DocumentBlock(
                id="1", type=BlockType.EQUATION, content="\\frac{a}{b}",
                page=1, extractor="test", reading_order=0,
            ),
        ]
        md = blocks_to_markdown(blocks)
        assert "$$" in md
        assert "\\frac{a}{b}" in md

    def test_figure_placeholder(self):
        blocks = [
            DocumentBlock(
                id="1", type=BlockType.FIGURE, content="[image]",
                page=1, extractor="test", reading_order=0,
            ),
        ]
        md = blocks_to_markdown(blocks)
        assert "![" in md


class TestConfidenceClassification:
    """Verify confidence value handling in DocumentBlock model."""

    def test_null_confidence_is_not_zero(self):
        block = DocumentBlock(
            id="1", type=BlockType.PARAGRAPH, content="Text",
            page=1, extractor="pymupdf", confidence=None,
        )
        assert block.confidence is None
        data = block.model_dump()
        assert data["confidence"] is None
        json_data = block.model_dump_json()
        assert '"confidence":null' in json_data.replace(" ", "")

    def test_float_confidence_preserved(self):
        block = DocumentBlock(
            id="1", type=BlockType.PARAGRAPH, content="Text",
            page=1, extractor="rapidocr", confidence=0.93,
        )
        assert block.confidence == 0.93
        data = block.model_dump()
        assert data["confidence"] == 0.93

    def test_confidence_validation_range(self):
        with pytest.raises(Exception):
            DocumentBlock(
                id="1", type=BlockType.PARAGRAPH, content="Text",
                page=1, extractor="test", confidence=1.5,
            )
        with pytest.raises(Exception):
            DocumentBlock(
                id="1", type=BlockType.PARAGRAPH, content="Text",
                page=1, extractor="test", confidence=-0.1,
            )

    def test_boundary_values(self):
        for val in [0.0, 0.5, 0.85, 1.0]:
            block = DocumentBlock(
                id="1", type=BlockType.PARAGRAPH, content="Text",
                page=1, extractor="test", confidence=val,
            )
            assert block.confidence == val

    def test_json_roundtrip(self):
        block = DocumentBlock(
            id="1", type=BlockType.PARAGRAPH, content="OCR text",
            page=1, extractor="rapidocr", confidence=0.9321,
        )
        json_str = block.model_dump_json()
        restored = DocumentBlock.model_validate_json(json_str)
        assert restored.confidence == 0.9321

    def test_null_json_roundtrip(self):
        block = DocumentBlock(
            id="1", type=BlockType.PARAGRAPH, content="Text",
            page=1, extractor="pymupdf", confidence=None,
        )
        json_str = block.model_dump_json()
        restored = DocumentBlock.model_validate_json(json_str)
        assert restored.confidence is None
