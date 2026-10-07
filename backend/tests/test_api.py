"""Tests for the API endpoints via httpx async client."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from main import app  # noqa: E402


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.anyio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_parse_missing_file(client):
    resp = await client.post("/api/parse")
    assert resp.status_code == 400
    data = resp.json()
    assert data["status"] == "error"
    assert data["error"]["code"] == "MISSING_FILE"


@pytest.mark.anyio
async def test_parse_unsupported_format(client, tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello")
    resp = await client.post(
        "/api/parse",
        files={"file": ("test.txt", f.read_bytes(), "text/plain")},
    )
    assert resp.status_code == 415
    assert resp.json()["error"]["code"] == "UNSUPPORTED_FORMAT"


@pytest.mark.anyio
async def test_parse_empty_file(client, tmp_path):
    f = tmp_path / "empty.pdf"
    f.write_bytes(b"")
    resp = await client.post(
        "/api/parse",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "EMPTY_FILE"


@pytest.mark.anyio
async def test_parse_corrupt_pdf(client):
    resp = await client.post(
        "/api/parse",
        files={"file": ("bad.pdf", b"%PDF-1.4 corrupt", "application/pdf")},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_FILE"


@pytest.mark.anyio
async def test_parse_digital_pdf(client, make_pdf):
    data = make_pdf.read_bytes()
    resp = await client.post(
        "/api/parse",
        files={"file": ("test.pdf", data, "application/pdf")},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert result["status"] in ("success", "partial")
    assert result["page_count"] >= 1
    assert len(result["blocks"]) > 0
    assert result["markdown"]  # Non-empty
    # Verify block structure
    block = result["blocks"][0]
    assert "id" in block
    assert "type" in block
    assert "content" in block
    assert "page" in block
    assert "extractor" in block
    assert "reading_order" in block
    assert "requires_review" in block


@pytest.mark.anyio
async def test_parse_docx(client, make_docx):
    data = make_docx.read_bytes()
    resp = await client.post(
        "/api/parse",
        files={"file": ("test.docx", data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert result["status"] in ("success", "partial")
    types = [b["type"] for b in result["blocks"]]
    assert "heading" in types
    assert "table" in types
    assert result["markdown"]


@pytest.mark.anyio
async def test_parse_pptx(client, make_pptx):
    data = make_pptx.read_bytes()
    resp = await client.post(
        "/api/parse",
        files={"file": ("test.pptx", data, "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert result["status"] in ("success", "partial")
    assert len(result["blocks"]) > 0
    assert result["markdown"]


@pytest.mark.anyio
async def test_parse_xlsx(client, make_xlsx):
    data = make_xlsx.read_bytes()
    resp = await client.post(
        "/api/parse",
        files={"file": ("test.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert result["status"] in ("success", "partial")
    assert any(b["type"] == "table" for b in result["blocks"])
    assert result["markdown"]


@pytest.mark.anyio
async def test_parse_image_jpg(client, make_image):
    data = make_image.read_bytes()
    resp = await client.post(
        "/api/parse",
        files={"file": ("test.jpg", data, "image/jpeg")},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert result["status"] in ("success", "partial")
    # OCR should find some text
    if result["blocks"]:
        block = result["blocks"][0]
        assert block["extractor"] == "rapidocr"
        assert block["confidence"] is not None  # Real OCR confidence


@pytest.mark.anyio
async def test_parse_scanned_pdf(client, make_scanned_pdf):
    data = make_scanned_pdf.read_bytes()
    resp = await client.post(
        "/api/parse",
        files={"file": ("scanned.pdf", data, "application/pdf")},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert result["status"] in ("success", "partial")
    # Scanned pages should be routed to OCR
    ocr_blocks = [b for b in result["blocks"] if b["extractor"] == "rapidocr"]
    assert len(ocr_blocks) > 0, "Scanned page should produce OCR blocks"


@pytest.mark.anyio
async def test_parse_invalid_magic_bytes(client):
    """File extension says PDF but content is not a PDF."""
    resp = await client.post(
        "/api/parse",
        files={"file": ("fake.pdf", b"this is not a pdf at all", "application/pdf")},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_FILE"
