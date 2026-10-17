"""Interactions between the timeout-recovery guards, table/digital-math work and opt-in scanned math.

Each test pins one behaviour that would silently regress if two independently developed changes were
merged badly (bounded rendering, cached page images, deadline use, default-off, guards still present).
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import pytest
from PIL import Image

from extractors.base import ExtractionResult
from models.document import BlockType, DocumentBlock
from services import pdf_router, region_router, scanned_math
from services.vision import models, pix2text_mfr


def scanned_source():
    block = DocumentBlock(id="ocr", type=BlockType.PARAGRAPH, content="y=2", page=1, bbox=(.1, .1, .9, .2),
                          extractor="rapidocr", metadata={"route": "scanned"})
    return ExtractionResult(page_count=1, blocks=[block])


def enable(monkeypatch, remaining=60):
    monkeypatch.setenv("AXTRACT_SCANNED_MATH", "1")
    monkeypatch.setattr(scanned_math, "available_memory_bytes", lambda: 2 * 1024 ** 3)
    monkeypatch.setattr(pix2text_mfr, "mfr_status", lambda: {"available": True})
    monkeypatch.setattr(scanned_math.deadline, "remaining", lambda: remaining)
    monkeypatch.setattr(scanned_math, "hidden_scan_words", lambda *a: {})
    monkeypatch.setattr(scanned_math, "segment_lines", lambda image: [(20, 20, 180, 40)])


# --- timeout guards survive the merge -------------------------------------------------------------

def test_timeout_pixel_cap_constants_are_unchanged():
    assert pdf_router._MAX_OCR_PIXELS == 4_500_000
    assert pdf_router._MAX_OCR_SIDE == 2500
    assert pdf_router._BASE_OCR_DPI == 200


def test_vector_drawing_guards_still_present():
    import extractors.pymupdf_extractor as ex
    src = inspect.getsource(ex)
    assert "cdrawings_count <= 20000" in src and 'strategy="text"' in src
    assert "MAX_VECTOR_CHART_DRAWINGS = 5000" in inspect.getsource(region_router)


def test_math_reconstruction_is_still_wired_into_the_extractor():
    import extractors.pymupdf_extractor as ex
    src = inspect.getsource(ex)
    assert "rebuild_page(" in src and "page_has_math_fonts(" in src


# --- scanned math must obey the same budgets as everything else -------------------------------------

def test_default_off_never_touches_models_or_rendering(monkeypatch):
    monkeypatch.delenv("AXTRACT_SCANNED_MATH", raising=False)
    monkeypatch.setattr(pix2text_mfr, "mfr_status", lambda: pytest.fail("default-off queried the model"))
    monkeypatch.setattr("services.preview_service.render_page_pil", lambda *a, **k: pytest.fail("rendered"))
    result = scanned_source()
    scanned_math.route_scanned_math(result, Path("unused"))
    assert len(result.blocks) == 1


def test_exhausted_deadline_skips_rendering_and_recognition(monkeypatch):
    enable(monkeypatch, remaining=5)  # below the 6 s parser reserve
    monkeypatch.setattr("services.preview_service.render_page_pil", lambda *a, **k: pytest.fail("rendered"))
    monkeypatch.setattr(pix2text_mfr, "recognize", lambda *a, **k: pytest.fail("recognized"))
    result = scanned_source()
    scanned_math.route_scanned_math(result, Path("unused"))
    assert len(result.blocks) == 1 and result.blocks[0].content == "y=2"


def test_scanned_math_does_not_run_layout_inference(monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(models, "detect_layout", lambda *a, **k: pytest.fail("layout inference ran"))
    monkeypatch.setattr("services.preview_service.render_page_pil", lambda *a, **k: Image.new("RGB", (200, 200), "white"))
    monkeypatch.setattr(pix2text_mfr, "recognize",
                        lambda *a, **k: pix2text_mfr.Prediction("x^2=4", True, "eos", 8, .1))
    scanned_math.route_scanned_math(scanned_source(), Path("unused"))


def test_render_dpi_respects_the_pixel_cap():
    letter = scanned_math.render_dpi_for(612, 792)
    assert letter == 200
    for w, h in ((2000, 3000), (5000, 5000), (612, 12000)):
        dpi = scanned_math.render_dpi_for(w, h)
        assert dpi >= 1
        assert (w * dpi / 72) * (h * dpi / 72) <= pdf_router._MAX_OCR_PIXELS
        assert max(w, h) * dpi / 72 <= pdf_router._MAX_OCR_SIDE + 1


def test_ocr_cache_image_is_not_used_for_line_recognition(monkeypatch):
    """Measured: the OCR cache comes from another renderer and loses scanned-math assertions."""
    enable(monkeypatch)
    from utils import request_cache
    monkeypatch.setattr(request_cache, "get_cached_image", lambda path, page: pytest.fail("cache consulted"))
    rendered = []
    monkeypatch.setattr("services.preview_service.render_page_pil",
                        lambda path, page, dpi: rendered.append(dpi) or Image.new("RGB", (200, 200), "white"))
    monkeypatch.setattr(pix2text_mfr, "recognize",
                        lambda *a, **k: pix2text_mfr.Prediction("x^2=4", True, "eos", 8, .1))
    result = scanned_source()
    scanned_math.route_scanned_math(result, Path("unused"))
    assert rendered and len(result.blocks) == 2 and result.blocks[1].type == BlockType.EQUATION


def test_oversized_page_is_rendered_at_a_capped_dpi(monkeypatch):
    enable(monkeypatch)
    seen = []
    monkeypatch.setattr(scanned_math, "page_size_points", lambda path, page: (3000.0, 4000.0))

    def render(path, page, dpi):
        seen.append(dpi)
        return Image.new("RGB", (200, 200), "white")

    monkeypatch.setattr("services.preview_service.render_page_pil", render)
    monkeypatch.setattr(pix2text_mfr, "recognize",
                        lambda *a, **k: pix2text_mfr.Prediction("x^2=4", True, "eos", 8, .1))
    scanned_math.route_scanned_math(scanned_source(), Path("unused"))
    assert seen and all((3000 * d / 72) * (4000 * d / 72) <= 4_500_000 for d in seen) and seen[0] < 200
