"""Unit tests for individual extractors."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extractors.pymupdf_extractor import PyMuPDFExtractor
from extractors.docx_extractor import DocxExtractor
from extractors.pptx_extractor import PptxExtractor
from extractors.xlsx_extractor import XlsxExtractor
from extractors.ocr_extractor import OCRExtractor
from models.document import BlockType
from models.errors import AppError


class TestPyMuPDFExtractor:
    def test_extract_digital_pdf(self, make_pdf):
        ext = PyMuPDFExtractor()
        result = ext.extract(make_pdf)
        assert result.page_count >= 1
        assert len(result.blocks) > 0
        assert all(b.extractor == "pymupdf" for b in result.blocks)

    def test_heading_detection(self, tmp_path):
        """Heading detection needs multiple blocks with varied font sizes."""
        import pymupdf
        doc = pymupdf.open()
        p = doc.new_page()
        # Large font for heading
        p.insert_text((72, 80), "Document Title", fontsize=24)
        # Normal font for body (multiple blocks to establish median)
        p.insert_textbox(pymupdf.Rect(72, 120, 500, 200),
                         "First body paragraph with normal sized text.", fontsize=11)
        p.insert_textbox(pymupdf.Rect(72, 210, 500, 290),
                         "Second body paragraph with more content.", fontsize=11)
        p.insert_textbox(pymupdf.Rect(72, 300, 500, 380),
                         "Third body paragraph for median calculation.", fontsize=11)
        path = tmp_path / "heading_test.pdf"
        doc.save(str(path))
        doc.close()

        ext = PyMuPDFExtractor()
        result = ext.extract(path)
        headings = [b for b in result.blocks if b.type == BlockType.HEADING]
        assert len(headings) > 0, "Should detect headings from font size difference"

    def test_normalized_bbox(self, make_pdf):
        ext = PyMuPDFExtractor()
        result = ext.extract(make_pdf)
        for block in result.blocks:
            if block.bbox:
                x1, y1, x2, y2 = block.bbox
                assert 0 <= x1 <= 1, f"x1={x1} out of range"
                assert 0 <= y1 <= 1, f"y1={y1} out of range"
                assert 0 <= x2 <= 1, f"x2={x2} out of range"
                assert 0 <= y2 <= 1, f"y2={y2} out of range"

    def test_invalid_pdf_raises(self, tmp_path):
        ext = PyMuPDFExtractor()
        f = tmp_path / "bad.pdf"
        f.write_bytes(b"not a pdf")
        with pytest.raises(AppError) as exc_info:
            ext.extract(f)
        assert exc_info.value.code == "INVALID_FILE"


class TestDocxExtractor:
    def test_extract_docx(self, make_docx):
        ext = DocxExtractor()
        result = ext.extract(make_docx)
        assert result.page_count >= 1
        assert len(result.blocks) > 0
        types = {b.type for b in result.blocks}
        assert BlockType.HEADING in types
        assert BlockType.TABLE in types

    def test_table_has_rows(self, make_docx):
        ext = DocxExtractor()
        result = ext.extract(make_docx)
        tables = [b for b in result.blocks if b.type == BlockType.TABLE]
        assert len(tables) > 0
        assert "rows" in tables[0].metadata

    def test_bbox_is_null(self, make_docx):
        ext = DocxExtractor()
        result = ext.extract(make_docx)
        for block in result.blocks:
            assert block.bbox is None, "DOCX should not have bbox"


class TestPptxExtractor:
    def test_extract_pptx(self, make_pptx):
        ext = PptxExtractor()
        result = ext.extract(make_pptx)
        assert result.page_count >= 1
        assert len(result.blocks) > 0
        headings = [b for b in result.blocks if b.type == BlockType.HEADING]
        assert len(headings) > 0


class TestXlsxExtractor:
    def test_extract_xlsx(self, make_xlsx):
        ext = XlsxExtractor()
        result = ext.extract(make_xlsx)
        assert result.page_count >= 1
        tables = [b for b in result.blocks if b.type == BlockType.TABLE]
        assert len(tables) > 0
        assert tables[0].metadata["row_count"] == 3
        assert tables[0].metadata["sheet_name"] == "Sheet1"


class TestOCRExtractor:
    def test_extract_image(self, make_image):
        ext = OCRExtractor()
        result = ext.extract(make_image)
        assert result.page_count == 1
        if result.blocks:
            block = result.blocks[0]
            assert block.extractor == "rapidocr"
            assert block.confidence is not None
            assert 0 <= block.confidence <= 1

    def test_normalized_bbox(self, make_image):
        ext = OCRExtractor()
        result = ext.extract(make_image)
        for block in result.blocks:
            if block.bbox:
                x1, y1, x2, y2 = block.bbox
                assert 0 <= x1 <= 1
                assert 0 <= y1 <= 1
                assert 0 <= x2 <= 1
                assert 0 <= y2 <= 1
