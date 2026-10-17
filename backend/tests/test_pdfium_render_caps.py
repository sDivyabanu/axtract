"""Real PDFium regression coverage for every production PDF rendering entry point.

PDF documents stay in memory; no benchmark PDFs, datasets or expected answers are generated.
"""
from __future__ import annotations
import io
from pathlib import Path
import pymupdf
import pypdfium2
import pytest
from PIL import Image
from extractors.base import ExtractionResult
from models.document import BlockType, DocumentBlock
from services import preview_service, region_router, scanned_math


def memory_source(monkeypatch, width, height):
    real_fitz, real_pdfium = pymupdf.open, pypdfium2.PdfDocument
    with real_fitz() as doc:
        doc.new_page(width=width, height=height)
        raw = doc.tobytes()
    monkeypatch.setattr(pymupdf, "open", lambda *a, **k: real_fitz(stream=raw, filetype="pdf"))
    monkeypatch.setattr(pypdfium2, "PdfDocument", lambda *a, **k: real_pdfium(raw))
    return Path("in-memory-render-review.pdf"), raw, real_pdfium


def bounded(image):
    assert max(image.size) <= 2500
    assert image.width * image.height <= 4_500_000


def test_reproduced_thin_page_uses_actual_region_fallback_and_pdfium(monkeypatch):
    path, _, _ = memory_source(monkeypatch, 500_000, 100)
    image = preview_service.render_page_pil(path, 1, region_router._capped_render_dpi(path, 1))
    bounded(image)


@pytest.mark.parametrize("width,height", [(500_000,500_000), (763.6,763.6), (2500.4,100), (612,792)])
def test_production_fallback_handles_square_rounding_and_normal_pages(monkeypatch, width, height):
    path, _, _ = memory_source(monkeypatch, width, height)
    bounded(preview_service.render_page_pil(path,1,region_router._capped_render_dpi(path,1)))


def test_ordinary_page_pixels_are_unchanged(monkeypatch):
    path, raw, real_pdfium = memory_source(monkeypatch, 612, 792)
    with real_pdfium(raw) as doc:
        page = doc[0]
        bitmap = page.render(scale=200/72, may_draw_forms=False)
        expected = bitmap.to_pil().convert("RGB")
        bitmap.close(); page.close()
    actual = preview_service.render_page_pil(path,1,200)
    assert actual.size == expected.size and actual.tobytes() == expected.tobytes()


def test_preview_png_entry_point_also_obeys_caps(monkeypatch):
    path, _, _ = memory_source(monkeypatch, 500_000,100)
    with Image.open(io.BytesIO(preview_service.render_pdf_page(path,1,200))) as image:
        bounded(image)


def test_opt_in_math_renderer_cannot_bypass_caps(monkeypatch):
    path, _, _ = memory_source(monkeypatch, 500_000,100)
    bounded(scanned_math.page_image(path,1))


def test_region_render_failure_keeps_preexisting_text(monkeypatch):
    path, _, _ = memory_source(monkeypatch, 612,792)
    block = DocumentBlock(id="source",type=BlockType.PARAGRAPH,content="Original text stays.",
                          page=1,bbox=(.1,.1,.8,.2),extractor="rapidocr",metadata={"route":"scanned"})
    result = ExtractionResult(page_count=1,blocks=[block])
    monkeypatch.setattr(region_router.deadline,"remaining",lambda:60)
    monkeypatch.setattr(pypdfium2.PdfPage,"render",lambda *a,**k: (_ for _ in ()).throw(RuntimeError("renderer failed")))
    region_router._route_pdf(result,path)
    assert result.blocks == [block] and block.content == "Original text stays."
    assert any(e.code == "PAGE_RENDER_FAILED" for e in result.errors)


@pytest.mark.parametrize("dpi", [0,-1,float("inf"),float("nan")])
def test_invalid_requested_scale_fails_before_rendering(monkeypatch,dpi):
    path, _, _ = memory_source(monkeypatch,612,792)
    with pytest.raises(preview_service.PreviewError):
        preview_service.render_page_pil(path,1,dpi)


def test_oversized_renderer_output_is_rejected_and_released(monkeypatch):
    class Bitmap:
        width = height = 7000
        def to_pil(self):
            pytest.fail("An oversized bitmap must not be copied into PIL")
        def close(self):
            closed.append(True)
    closed = []
    page = type("Page", (), {"get_size": lambda self: (612,792),
                              "render": lambda self, **kw: Bitmap()})()
    with pytest.raises(preview_service.PreviewError, match="exceeds"):
        preview_service._render_capped_pdfium_page(page,200)
    assert len(closed) == 8


def test_impossible_scale_fails_before_renderer_allocation(monkeypatch):
    from services import pdf_router
    monkeypatch.setattr(pdf_router,"fit_zoom",lambda *a,**kw:0)
    page = type("Page", (), {"get_size": lambda self:(612,792),
                              "render": lambda *a,**kw:pytest.fail("zero scale rendered")})()
    with pytest.raises(preview_service.PreviewError,match="positive bounded scale"):
        preview_service._render_capped_pdfium_page(page,200)
