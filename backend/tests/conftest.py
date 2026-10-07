"""Shared test fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

# Ensure backend modules are importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app  # noqa: E402


@pytest.fixture
def client():
    """Synchronous test client using httpx."""
    import httpx
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def make_pdf(tmp_path) -> Path:
    """Create a simple digital text PDF for testing."""
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Test Document Title", fontsize=20)
    page.insert_textbox(
        pymupdf.Rect(72, 140, 500, 250),
        "This is the body paragraph of the test PDF.",
        fontsize=11,
    )
    path = tmp_path / "test.pdf"
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def make_scanned_pdf(tmp_path) -> Path:
    """Create a PDF with an image-only page (simulates scanned document)."""
    import pymupdf
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (595, 842), "white")
    draw = ImageDraw.Draw(img)
    draw.text((72, 100), "Scanned text for OCR", fill="black")
    img_path = tmp_path / "scan.png"
    img.save(str(img_path))

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(0, 0, 595, 842), filename=str(img_path))
    path = tmp_path / "scanned.pdf"
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def make_docx(tmp_path) -> Path:
    """Create a simple DOCX file for testing."""
    import docx
    doc = docx.Document()
    doc.add_heading("Test Heading", level=1)
    doc.add_paragraph("Test paragraph content.")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "A"
    table.cell(0, 1).text = "B"
    table.cell(1, 0).text = "1"
    table.cell(1, 1).text = "2"
    path = tmp_path / "test.docx"
    doc.save(str(path))
    return path


@pytest.fixture
def make_pptx(tmp_path) -> Path:
    """Create a simple PPTX file for testing."""
    from pptx import Presentation
    prs = Presentation()
    layout = prs.slide_layouts[0]
    slide = prs.slides.add_slide(layout)
    slide.shapes.title.text = "Test Slide Title"
    slide.placeholders[1].text = "Slide body text"
    path = tmp_path / "test.pptx"
    prs.save(str(path))
    return path


@pytest.fixture
def make_xlsx(tmp_path) -> Path:
    """Create a simple XLSX file for testing."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Name", "Value"])
    ws.append(["Alpha", 100])
    ws.append(["Beta", 200])
    path = tmp_path / "test.xlsx"
    wb.save(str(path))
    return path


@pytest.fixture
def make_image(tmp_path) -> Path:
    """Create a simple JPG image with text for testing."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (400, 200), "white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 20), "Image OCR test text", fill="black")
    path = tmp_path / "test.jpg"
    img.save(str(path))
    return path
