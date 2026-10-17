"""Regressions for the Codex NO-GO review of 7e79064 (false charts, partial results, hard render caps, inventory).

All synthetic: no OCR/layout/chart models, no network."""
from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extractors.base import ExtractionResult
from models.document import BlockType, DocumentBlock
from models.errors import AppError
from services import pdf_router, region_router
from services.vision import models
from services.vision.chart_raster import ChartReading
from services.vision.models import Region

MAX_SIDE, MAX_PIXELS = pdf_router._MAX_OCR_SIDE, pdf_router._MAX_OCR_PIXELS


@pytest.fixture(autouse=True)
def no_leftover_parse_deadline():
    """Earlier tests start a 55 s parse deadline in this thread; without it the budget guards see time left."""
    from utils import deadline
    token = deadline._deadline.set(None)
    yield
    deadline._deadline.reset(token)


# --- 1. false chart conversion --------------------------------------------------------------------------

def scanned_letter(tmp_path: Path, text: str = "Dear Sir, I write to you about the estate.") -> tuple[Path, ExtractionResult]:
    path = tmp_path / "letter.pdf"
    doc = pymupdf.open()
    doc.new_page(width=300, height=400)
    doc.save(path)
    block = DocumentBlock(id="p1-ocr0", type=BlockType.PARAGRAPH, content=text, page=1, bbox=(0.1, 0.1, 0.5, 0.2),
                          confidence=0.9, extractor="rapidocr", metadata={"route": "scanned"})
    return path, ExtractionResult(page_count=1, blocks=[block])


def route_with(monkeypatch, tmp_path, reading, text="Dear Sir, I write to you about the estate."):
    path, result = scanned_letter(tmp_path, text)
    monkeypatch.setattr(models, "latex_model_available", lambda: False)  # layout/charts must not need LaTeX weights
    monkeypatch.setattr(models, "detect_layout", lambda *a, **k: [Region("figure", 0.9, (0.05, 0.05, 0.9, 0.9))])
    monkeypatch.setattr(region_router, "read_chart", lambda *a, **k: reading)
    region_router._route_pdf(result, path)
    return result


# the exact fabricated reading from old_scans/40.pdf: estimated pixel shares, unreadable labels, generic names
FAKE_PIE = ChartReading(
    data={"title": "", "chart_type": "pie", "categories": ["Slice 1", "420元", "Slice 3"],
          "series": [{"name": "Share (%)", "values": [94.3, 4.2, 1.5]}], "values_estimated": True},
    confidence=0.6, flags=["some_slice_labels_unreadable"])


def test_handwritten_letter_is_not_replaced_by_a_fabricated_pie_chart(monkeypatch, tmp_path):
    result = route_with(monkeypatch, tmp_path, FAKE_PIE)
    assert [b.type for b in result.blocks] == [BlockType.PARAGRAPH]
    assert result.blocks[0].content.startswith("Dear Sir")


@pytest.mark.parametrize("confidence,flags,categories", [
    (0.6, [], ["Revenue", "Costs"]),                                  # low confidence alone
    (0.9, ["y_axis_not_calibrated"], ["Revenue", "Costs"]),           # unreliable measurement
    (0.9, [], ["Slice 1", "Slice 2"]),                                # generic labels = nothing was read
    (0.9, [], ["Revenue", ""]),                                       # an unlabeled category
])
def test_any_single_weak_signal_keeps_the_original_ocr(monkeypatch, tmp_path, confidence, flags, categories):
    reading = ChartReading(data={"chart_type": "pie", "categories": categories,
                                 "series": [{"name": "s", "values": [60.0, 40.0]}]}, confidence=confidence, flags=flags)
    result = route_with(monkeypatch, tmp_path, reading)
    assert all(b.type != BlockType.CHART for b in result.blocks) and result.blocks[0].extractor == "rapidocr"


def test_a_page_of_prose_is_never_a_chart_even_with_a_confident_reading(monkeypatch, tmp_path):
    strong = ChartReading(data={"chart_type": "pie", "categories": ["A", "B"],
                                "series": [{"name": "s", "values": [60.0, 40.0]}]}, confidence=0.9, flags=[])
    result = route_with(monkeypatch, tmp_path, strong, text="word " * 200)
    assert all(b.type != BlockType.CHART for b in result.blocks)


def test_a_genuine_scanned_chart_is_still_detected_without_latex_weights(monkeypatch, tmp_path):
    strong = ChartReading(data={"chart_type": "pie", "categories": ["Rent", "Food"],
                                "series": [{"name": "Share (%)", "values": [60.0, 40.0]}], "values_estimated": False},
                          confidence=0.9, flags=[])
    result = route_with(monkeypatch, tmp_path, strong, text="Rent")
    charts = [b for b in result.blocks if b.type == BlockType.CHART]
    assert len(charts) == 1 and not any(b.extractor == "rapidocr" for b in result.blocks)


# --- 2. partial results in the surrounding failure paths ------------------------------------------------

class FakeOCR:
    def __init__(self):
        self.n = 0

    def ocr_pil_image(self, img, page_number):
        self.n += 1
        return [DocumentBlock(id=f"p{page_number}-ocr0", type=BlockType.PARAGRAPH, content=f"tile{self.n}",
                              page=page_number, bbox=(0.1, 0.1, 0.4, 0.2), confidence=0.9, extractor="rapidocr",
                              metadata={})], []


def router(ocr=None) -> pdf_router.PDFRouter:
    r = pdf_router.PDFRouter.__new__(pdf_router.PDFRouter)
    r._ocr = ocr or FakeOCR()
    return r


def big_page():
    doc = pymupdf.open()
    doc.new_page(width=6000, height=6000)
    return doc, doc[0]


def test_overview_render_failure_after_two_good_tiles_keeps_their_text(monkeypatch):
    real = pdf_router.render_capped
    calls = {"tiles": 0}

    def flaky(page, clip, *a, **k):
        if clip is None:
            raise MemoryError("overview exploded")
        calls["tiles"] += 1
        return real(page, clip, *a, **k)

    monkeypatch.setattr(pdf_router, "render_capped", flaky)
    doc, page = big_page()
    blocks, errors = router()._ocr_page(page, 1)
    assert calls["tiles"] == 4 and len(blocks) == 4
    codes = [e.code for e in errors]
    assert "OCR_OVERVIEW_FAILED" in codes and "OCR_RENDER_FAILED" not in codes


def test_deadline_on_a_later_page_keeps_the_finished_pages(monkeypatch, tmp_path):
    path = tmp_path / "three.pdf"
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    doc.save(path)
    r = router()
    r._pymupdf = SimpleNamespace(_extract_page=lambda page, n: [DocumentBlock(
        id=f"p{n}-b0", type=BlockType.PARAGRAPH, content=f"page {n}", page=n, bbox=(0, 0, 1, .1), confidence=None,
        extractor="pymupdf", metadata={})])
    monkeypatch.setattr(pdf_router.PyMuPDFExtractor, "analyze_page", staticmethod(lambda p: {
        "is_likely_scanned": False, "has_text": True, "has_images": False, "image_coverage": 0.0,
        "text_coverage": 0.5, "text_char_count": 100}))
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        if calls["n"] == 2:
            raise AppError("TIMEOUT", "budget", status_code=504)

    monkeypatch.setattr(pdf_router.deadline, "check", check)
    result = r.extract(path)  # must not raise
    assert [b.content for b in result.blocks] == ["page 1"]
    partial = [e for e in result.errors if e.code == "PAGES_PARTIAL"]
    assert len(partial) == 1 and "1 of 3" in partial[0].message


def test_non_timeout_deadline_errors_still_propagate(monkeypatch, tmp_path):
    path = tmp_path / "one.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(path)
    monkeypatch.setattr(pdf_router.deadline, "check", lambda: (_ for _ in ()).throw(AppError("OTHER", "x")))
    with pytest.raises(AppError):
        router().extract(path)


# --- 3. render caps without exceptions -------------------------------------------------------------------

class FakePage:
    """Honours the matrix like a real page and rounds the raster UP, as rasters do."""

    def __init__(self, w, h, inflate=0):
        self.rect = SimpleNamespace(width=w, height=h)
        self.w, self.h, self.inflate, self.sizes = w, h, inflate, []

    def get_pixmap(self, matrix=None, clip=None, alpha=False, dpi=None):
        z = matrix.a if matrix is not None else (dpi or 72) / 72.0
        w = (clip.width if clip is not None else self.w)
        h = (clip.height if clip is not None else self.h)
        pw, ph = math.ceil(w * z) + self.inflate, math.ceil(h * z) + self.inflate
        self.sizes.append((pw, ph))
        return SimpleNamespace(width=pw, height=ph, samples=bytes(pw * ph * 3))


@pytest.mark.parametrize("w,h", [(612, 792), (2500.2, 100), (100, 2499.9), (500_000, 100), (200_000, 200_000), (14_400, 14_400)])
def test_rendered_size_obeys_both_caps_even_for_extreme_geometry(w, h):
    page = FakePage(w, h)
    pix, zoom = pdf_router.render_capped(page, None, w, h)
    assert max(pix.width, pix.height) <= MAX_SIDE and pix.width * pix.height <= MAX_PIXELS
    assert zoom > 0


def test_the_cap_holds_below_one_dpi_without_exempting_it():
    zoom = pdf_router.fit_zoom(500_000, 100)
    assert zoom < 1 / 72  # fractional: 1 DPI alone would give a 6,944 px side
    assert math.ceil(500_000 * zoom) <= MAX_SIDE


def test_pixel_rounding_never_pushes_a_raster_over_the_cap():
    for w in (2499.1, 2499.9, 2500.0, 2500.4):
        z = pdf_router.fit_zoom(w, 50, base_dpi=72)
        assert math.ceil(w * z) <= MAX_SIDE


def test_a_renderer_that_always_oversizes_fails_explicitly():
    with pytest.raises(ValueError):
        pdf_router.render_capped(FakePage(600, 800, inflate=5000), None, 600, 800)


def test_extreme_page_still_ocrs_every_tile_within_caps():
    page = FakePage(500_000, 100)
    ocr = FakeOCR()
    blocks, errors = router(ocr)._ocr_page(page, 1)
    assert blocks and all(max(w, h) <= MAX_SIDE and w * h <= MAX_PIXELS for w, h in page.sizes)
    assert not [e for e in errors if e.code in ("OCR_RENDER_FAILED", "OCR_TILE_FAILED")]


def test_a_tile_that_cannot_be_rendered_keeps_the_others():
    class Flaky(FakePage):
        def get_pixmap(self, **k):
            if len(self.sizes) == 1:
                self.sizes.append(None)
                raise RuntimeError("boom")
            return super().get_pixmap(**k)

    blocks, errors = router()._ocr_page(Flaky(6000, 6000), 1)
    assert len(blocks) == 3 and "OCR_TILE_FAILED" in [e.code for e in errors]


# --- 5. inventory keeps its own diagnostic ---------------------------------------------------------------

def test_early_inventory_failure_reports_the_original_error(monkeypatch, tmp_path):
    import pypdfium2
    from verify.inventory.pdf import inventory_pdf

    def explode(*a, **k):
        raise RuntimeError("cannot open")

    monkeypatch.setattr(pypdfium2, "PdfDocument", explode)
    path = tmp_path / "x.pdf"
    path.write_bytes(b"%PDF-1.4")
    inv = inventory_pdf(path)
    assert inv.error == "RuntimeError: cannot open" and inv.timing_ms is not None
