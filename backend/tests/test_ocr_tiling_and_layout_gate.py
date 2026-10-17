"""Deterministic regressions for the tiled-OCR and scanned-chart blockers of the integration review.

1. OCR block ids stay unique across tiles          2. earlier tiles survive a later timeout / failure
3. every tile obeys BOTH the 2,500 px side cap and the pixel-area cap
4. RapidLayout discovery does not depend on the legacy LaTeX weights (timeout guard still applies)
All synthetic: no OCR model, no network, tiny PDFs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extractors.base import ExtractionResult
from models.document import BlockType, DocumentBlock
from models.errors import AppError
from services import pdf_router, region_router
from services.vision import models
from services.vision.models import Region

MAX_SIDE = pdf_router._MAX_OCR_SIDE
MAX_PIXELS = pdf_router._MAX_OCR_PIXELS


class RecordingOCR:
    """Stands in for RapidOCR: numbers its blocks per image (exactly like the real extractor) and records sizes."""

    def __init__(self, fail_on_call: int | None = None):
        self.sizes: list[tuple[int, int]] = []
        self.fail_on_call = fail_on_call

    def ocr_pil_image(self, img, page_number):
        self.sizes.append(img.size)
        if self.fail_on_call is not None and len(self.sizes) == self.fail_on_call:
            raise RuntimeError("ocr exploded")
        n = len(self.sizes)
        blocks = [DocumentBlock(id=f"p{page_number}-ocr{i}", type=BlockType.PARAGRAPH, content=f"tile{n}-line{i}",
                                page=page_number, bbox=(0.1, 0.1 + 0.2 * i, 0.4, 0.2 + 0.2 * i), confidence=0.9,
                                extractor="rapidocr", metadata={}) for i in range(2)]
        return blocks, []


def make_router(ocr: RecordingOCR) -> pdf_router.PDFRouter:
    router = pdf_router.PDFRouter.__new__(pdf_router.PDFRouter)
    router._ocr = ocr
    return router


def page_of(width: float, height: float):
    doc = pymupdf.open()
    doc.new_page(width=width, height=height)
    return doc, doc[0]


# --- 3. caps --------------------------------------------------------------------------------------

@pytest.mark.parametrize("w,h", [(612, 792), (4000, 1000), (6000, 6000), (14000, 600), (3000, 14000)])
def test_fit_dpi_respects_both_caps(w, h):
    dpi = pdf_router.fit_dpi(w, h)
    assert dpi >= 1
    assert (w * dpi / 72) * (h * dpi / 72) <= MAX_PIXELS or dpi == 1
    assert max(w, h) * dpi / 72 <= MAX_SIDE or dpi == 1


def test_area_only_dpi_would_break_the_side_cap_for_a_wide_tile():
    """The case the old area-only tile rule got wrong: 2,100 x 1,000 pt at the 3.5 MP tile budget."""
    area_only = 93  # old rule: largest DPI with area <= 3.5 MP
    assert 2100 * area_only / 72 > MAX_SIDE
    assert 2100 * pdf_router.fit_dpi(2100, 1000, max_pixels=3_500_000) / 72 <= MAX_SIDE


def test_every_rendered_tile_obeys_side_and_area_caps():
    ocr = RecordingOCR()
    doc, page = page_of(4000, 1000)  # forces the 2-tile fallback with a side-limited tile
    blocks, errors = make_router(ocr)._ocr_page(page, 1)
    assert len(ocr.sizes) == 2 and not errors
    for w, h in ocr.sizes:
        assert max(w, h) <= MAX_SIDE and w * h <= MAX_PIXELS
    assert len(blocks) == 4


def test_large_square_page_tiles_stay_within_caps():
    ocr = RecordingOCR()
    doc, page = page_of(6000, 6000)
    make_router(ocr)._ocr_page(page, 1)
    assert len(ocr.sizes) == 4
    assert all(max(w, h) <= MAX_SIDE and w * h <= MAX_PIXELS for w, h in ocr.sizes)


# --- 1. unique ids --------------------------------------------------------------------------------

def test_tile_block_ids_are_unique_and_traceable():
    ocr = RecordingOCR()
    doc, page = page_of(6000, 6000)
    blocks, _ = make_router(ocr)._ocr_page(page, 3)
    ids = [b.id for b in blocks]
    assert len(ids) == 8 and len(set(ids)) == 8
    assert all(i.startswith("p3-r") for i in ids)
    assert {b.metadata["ocr_tile"]["row"] for b in blocks} == {0, 1}
    assert {b.metadata["ocr_tile"]["col"] for b in blocks} == {0, 1}


def test_single_pass_ids_keep_the_established_format():
    ocr = RecordingOCR()
    doc, page = page_of(612, 792)
    blocks, _ = make_router(ocr)._ocr_page(page, 2)
    assert [b.id for b in blocks] == ["p2-ocr0", "p2-ocr1"]


def test_final_guard_renames_only_real_duplicates():
    mk = lambda i: DocumentBlock(id=i, type=BlockType.PARAGRAPH, content="x", page=1, bbox=None,
                                 confidence=None, extractor="t", metadata={})
    blocks = [mk("a"), mk("b"), mk("a"), mk("a")]
    assert pdf_router.ensure_unique_block_ids(blocks) == 2
    assert [b.id for b in blocks] == ["a", "b", "a~2", "a~3"]
    assert pdf_router.ensure_unique_block_ids(blocks) == 0


def test_tiled_page_ids_are_unique_through_the_whole_router(tmp_path, monkeypatch):
    """End to end through PDFRouter.extract: the Verify/provenance layer keys on block ids."""
    path = tmp_path / "huge.pdf"
    doc, _ = page_of(6000, 6000)
    doc.save(path)
    router = make_router(RecordingOCR())
    router._pymupdf = pdf_router.PyMuPDFExtractor()
    monkeypatch.setattr(pdf_router.PyMuPDFExtractor, "analyze_page",
                        staticmethod(lambda page: {"is_likely_scanned": True, "has_text": False, "has_images": True,
                                                   "image_coverage": 1.0, "text_coverage": 0.0, "text_char_count": 0}))
    result = router.extract(path)
    ids = [b.id for b in result.blocks]
    assert len(ids) >= 8 and len(ids) == len(set(ids))


# --- 2. partial results ---------------------------------------------------------------------------

def test_timeout_after_two_tiles_keeps_their_text_and_reports_partial(monkeypatch):
    ocr = RecordingOCR()
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        if calls["n"] == 3:
            raise AppError("TIMEOUT", "budget", status_code=504)

    monkeypatch.setattr(pdf_router.deadline, "check", check)
    doc, page = page_of(6000, 6000)
    blocks, errors = make_router(ocr)._ocr_page(page, 1)
    assert len(ocr.sizes) == 2  # tiles 3 and 4 were never rendered
    assert len(blocks) == 4 and {b.content.split("-")[0] for b in blocks} == {"tile1", "tile2"}
    codes = [e.code for e in errors]
    assert "OCR_TILES_PARTIAL" in codes and "OCR_RENDER_FAILED" not in codes
    partial = next(e for e in errors if e.code == "OCR_TILES_PARTIAL")
    assert "2 of 4" in partial.message and partial.page == 1


def test_a_failing_tile_does_not_discard_the_others():
    ocr = RecordingOCR(fail_on_call=2)
    doc, page = page_of(6000, 6000)
    blocks, errors = make_router(ocr)._ocr_page(page, 1)
    assert len(ocr.sizes) == 4 and len(blocks) == 6
    assert sorted(e.code for e in errors) == ["OCR_TILES_PARTIAL", "OCR_TILE_FAILED"]


def test_timeout_on_the_very_first_tile_yields_no_blocks_but_a_truthful_status(monkeypatch):
    def check():
        raise AppError("TIMEOUT", "budget", status_code=504)

    monkeypatch.setattr(pdf_router.deadline, "check", check)
    doc, page = page_of(6000, 6000)
    blocks, errors = make_router(RecordingOCR())._ocr_page(page, 1)
    assert blocks == [] and any(e.code == "OCR_TILES_PARTIAL" and "0 of 4" in e.message for e in errors)


def test_untiled_pages_report_no_partial_status():
    doc, page = page_of(612, 792)
    _, errors = make_router(RecordingOCR())._ocr_page(page, 1)
    assert errors == []


# --- 4. layout discovery independent of LaTeX weights ---------------------------------------------

def scanned_pdf(tmp_path: Path) -> tuple[Path, ExtractionResult]:
    path = tmp_path / "scan.pdf"
    doc, _ = page_of(300, 400)
    doc.save(path)
    block = DocumentBlock(id="p1-ocr0", type=BlockType.PARAGRAPH, content="caption", page=1, bbox=(0.1, 0.1, 0.5, 0.2),
                          confidence=0.9, extractor="rapidocr", metadata={"route": "scanned"})
    return path, ExtractionResult(page_count=1, blocks=[block])


def test_layout_detection_runs_without_formula_weights(tmp_path, monkeypatch):
    path, result = scanned_pdf(tmp_path)
    seen = []
    monkeypatch.setattr(models, "latex_model_available", lambda: False)
    monkeypatch.setattr(models, "detect_layout", lambda img, *a, **k: seen.append(img.size) or [Region("figure", 0.9, (.1, .3, .9, .8))])
    region_router._route_pdf(result, path)
    assert seen, "RapidLayout must still be consulted when the LaTeX weights are absent"


def test_layout_detection_still_obeys_the_time_budget(tmp_path, monkeypatch):
    path, result = scanned_pdf(tmp_path)
    monkeypatch.setattr(models, "latex_model_available", lambda: True)
    monkeypatch.setattr(models, "detect_layout", lambda *a, **k: pytest.fail("ran past the budget"))
    monkeypatch.setattr(region_router.deadline, "remaining", lambda: region_router._MIN_BUDGET - 1)
    region_router._route_pdf(result, path)
    assert any(e.code == "REGION_ROUTING_SKIPPED" for e in result.errors)


def test_equations_are_still_not_read_without_formula_weights(tmp_path, monkeypatch):
    path, result = scanned_pdf(tmp_path)
    monkeypatch.setattr(models, "latex_model_available", lambda: False)
    monkeypatch.setattr(models, "detect_layout", lambda *a, **k: [Region("equation", 0.9, (.1, .3, .9, .5))])
    monkeypatch.setattr(region_router, "recognize_formula", lambda *a, **k: pytest.fail("formula model used without weights"))
    region_router._route_pdf(result, path)
    assert all(b.type != BlockType.EQUATION for b in result.blocks)
