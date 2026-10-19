"""VLM OCR engine: reply parsing, block mapping, Markdown, and fallback when the model server is absent."""
from __future__ import annotations

import json

import pytest

from models.document import BlockType
from services import vlm_engine
from services.layout_service import assign_reading_order
from services.markdown_service import blocks_to_markdown
from services.vlm_ocr import html_table_to_grid, parse_grounded, postprocess_markdown
from tests.fixtures.builders import pdf_with_paragraphs

REPLY = """![header](635,52,854,66) Chapter 2 Literature Review

![text](172,96,860,268) Calcium $Ca^{2+}$ replaces sodium.

![title](172,310,557,328) ## 2.3.1 Incorporation of cation

![table](100,400,900,600) <table><tr><th rowspan="2">A</th><th colspan="2">B</th></tr><tr><td>b1</td><td>b2</td></tr><tr><td>1</td><td>2</td><td>3</td></tr></table>

![page_number](500,922,527,940) 17"""


def test_parse_grounded_labels_and_boxes():
    blocks = parse_grounded(REPLY)
    assert [b.label for b in blocks] == ["header", "text", "title", "table", "page_number"]
    assert blocks[0].bbox == pytest.approx((0.635, 0.052, 0.854, 0.066))
    assert blocks[1].content == "Calcium $Ca^{2+}$ replaces sodium."
    assert parse_grounded("plain text without boxes") is None


def test_postprocess_drops_page_furniture():
    md = postprocess_markdown(REPLY)
    assert "Chapter 2 Literature Review" not in md and "\n17" not in md
    assert md.startswith("Calcium")


def test_html_table_grid_expands_spans():
    rows, merged, header_rows = html_table_to_grid('<table><tr><th rowspan="2">A</th><th colspan="2">B</th></tr>'
                                                   '<tr><td>b1</td><td>b2</td></tr></table>')
    assert rows == [["A", "B", ""], ["", "b1", "b2"]]
    assert merged == [[0, 0, 2, 1], [0, 1, 1, 2]] and header_rows == 1
    _, _, header_rows = html_table_to_grid("<table><thead><tr><th>A</th></tr><tr><td>a1</td></tr></thead>"
                                           "<tbody><tr><td>1</td></tr></tbody></table>")
    assert header_rows == 2
    assert html_table_to_grid("<table><tr><td>1</td></tr></table><table><tr><td>2</td></tr></table>") is None


def test_page_blocks_mapping_and_markdown(monkeypatch, tmp_path):
    pdf = pdf_with_paragraphs(tmp_path / "p.pdf", ["Some text"])
    monkeypatch.setattr(vlm_engine, "transcribe", lambda png, cfg: {
        "text": REPLY, "finish": "stop", "usage": {}, "seconds": 1.0, "cached": False})
    blocks, info = vlm_engine.page_blocks(pdf, 1)
    types = [b.type for b in blocks]
    assert types == [BlockType.HEADER, BlockType.PARAGRAPH, BlockType.HEADING, BlockType.TABLE, BlockType.FOOTER]
    assert all(b.metadata["bbox_source"] == "model_predicted" and b.extractor == "lightonocr" for b in blocks)
    table = blocks[3]
    assert table.metadata["rows"][0] == ["A", "B", ""] and not table.metadata["markdown_verbatim"]
    md = blocks_to_markdown(assign_reading_order(blocks))
    assert "Chapter 2" not in md and "17" not in md.split("---")[0].split("\n")[-2]
    assert "## 2.3.1 Incorporation of cation" in md
    assert 'rowspan="2"' in md and "<table>" in md  # spans survive as HTML
    assert md.index("Calcium") < md.index("2.3.1") < md.index("<table>")


def test_repetition_loop_is_trimmed_and_flagged(monkeypatch, tmp_path):
    pdf = pdf_with_paragraphs(tmp_path / "p.pdf", ["Some text"])
    looped = "![text](0,0,1000,1000) start\n" + "same line\n" * 50
    monkeypatch.setattr(vlm_engine, "transcribe", lambda png, cfg: {
        "text": looped, "finish": "length", "usage": {}, "seconds": 1.0, "cached": False})
    blocks, info = vlm_engine.page_blocks(pdf, 1)
    assert blocks[0].content.count("same line") == 3 and blocks[0].requires_review and info["repetition_trimmed"]


def test_router_falls_back_when_server_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("AXTRACT_VLM_OCR", "all")
    monkeypatch.setenv("AXTRACT_VLM_URL", "http://127.0.0.1:9")  # nothing listens here
    monkeypatch.setattr(vlm_engine, "_unavailable_until", 0.0)
    from services.pdf_router import PDFRouter

    pdf = pdf_with_paragraphs(tmp_path / "p.pdf", ["Fallback paragraph text stays readable."])
    result = PDFRouter().extract(pdf)
    assert any("Fallback paragraph" in b.content for b in result.blocks)
    assert all(b.extractor != "lightonocr" for b in result.blocks)
    assert [e.code for e in result.errors] == ["VLM_UNAVAILABLE"]


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("AXTRACT_VLM_OCR", raising=False)
    assert not vlm_engine.enabled_for("digital") and not vlm_engine.enabled_for("scanned")
    monkeypatch.setenv("AXTRACT_VLM_OCR", "scanned")
    assert vlm_engine.enabled_for("scanned") and not vlm_engine.enabled_for("digital")


def test_reply_cache_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("AXTRACT_VLM_CACHE_DIR", str(tmp_path / "cache"))
    calls = []

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "![text](0,0,10,10) hi"}, "finish_reason": "stop"}]}

    monkeypatch.setattr(vlm_engine.httpx, "post", lambda *a, **k: calls.append(1) or Resp())
    cfg = vlm_engine.settings()
    first = vlm_engine.transcribe(b"png-bytes", cfg)
    second = vlm_engine.transcribe(b"png-bytes", cfg)
    third = vlm_engine.transcribe(b"other-png", cfg)
    assert (first["cached"], second["cached"], third["cached"]) == (False, True, False)
    assert len(calls) == 2 and second["text"] == first["text"]
    assert json.loads(next((tmp_path / "cache").rglob("*.json")).read_text())["finish"] == "stop"


def test_unicode_guard_keeps_scripts_and_fractions():
    from security.unicode_guard import clean_and_flag

    assert clean_and_flag("kg/m² H₂O ½ ﬁle Ａ")["text"] == "kg/m² H₂O ½ file A"


def test_picture_descriptions_stay_out_of_body_text(monkeypatch, tmp_path):
    pdf = pdf_with_paragraphs(tmp_path / "p.pdf", ["Some text"])
    reply = ('![header_image](50,40,150,100) logo "ACME Corp"\n\n![text](100,200,900,300) Body text.\n\n'
             '![image](100,400,900,600) A diagram of a pump.\n\n'
             '![image](100,650,900,800) <table><tr><th>a</th></tr><tr><td>1</td></tr></table>')
    monkeypatch.setattr(vlm_engine, "transcribe", lambda png, cfg: {
        "text": reply, "finish": "stop", "usage": {}, "seconds": 1.0, "cached": False})
    blocks, _ = vlm_engine.page_blocks(pdf, 1)
    assert [b.type for b in blocks] == [BlockType.HEADER, BlockType.PARAGRAPH, BlockType.FIGURE, BlockType.TABLE]
    assert blocks[2].metadata["description"] == "A diagram of a pump." and blocks[2].metadata["description_source"] == "model_generated"
    md = blocks_to_markdown(assign_reading_order(blocks))
    assert "ACME" not in md and "pump" not in md and "Body text." in md and "| a |" in md


def test_scan_profile_routing(monkeypatch):
    monkeypatch.setenv("AXTRACT_VLM_OCR", "scanned")
    monkeypatch.delenv("AXTRACT_VLM_SCAN_MODEL_ID", raising=False)
    ocr_layer = {"image_coverage": 1.0, "has_text": True}  # a scan carrying a hidden OCR text layer
    assert vlm_engine.enabled_for("digital", ocr_layer) and not vlm_engine.enabled_for("digital", {"image_coverage": 0.1})
    assert vlm_engine.profile_for("scanned") == "general"  # no scan model configured: one model for every page
    monkeypatch.setenv("AXTRACT_VLM_SCAN_MODEL_ID", "LightOnOCR-3-4B-Q4_K_M")
    monkeypatch.setenv("AXTRACT_VLM_SCAN_URL", "http://127.0.0.1:8092")
    assert vlm_engine.profile_for("scanned") == "scan" and vlm_engine.profile_for("digital", ocr_layer) == "scan"
    assert vlm_engine.profile_for("digital", {"image_coverage": 0.1}) == "general"
    scan = vlm_engine.settings("scan")
    assert (scan["model"], scan["url"], scan["profile"]) == ("LightOnOCR-3-4B-Q4_K_M", "http://127.0.0.1:8092", "scan")
    assert vlm_engine.settings("general")["model"] != scan["model"]
