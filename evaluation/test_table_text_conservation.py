"""Concrete synthetic regressions for rejected and partial table regions."""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
import pymupdf
from extractors.pymupdf_extractor import PyMuPDFExtractor
from models.document import BlockType
from services.markdown_service import blocks_to_markdown


def fixture_page():
    doc=pymupdf.open()
    page=doc.new_page(width=400,height=400)
    # All three lines belong to one native text block: the table detector can
    # cover most of it while the final line is surrounding prose.
    page.insert_text((40,80),"Name Value\nAlpha 10\nSurrounding prose must survive",fontsize=12)
    return doc,page


def test_rejected_non_chart_grid_preserves_original_text(monkeypatch):
    doc,page=fixture_page()
    candidate=SimpleNamespace(bbox=(30,60,360,120),col_count=1,extract=lambda:[["Name Value Alpha 10"]])
    monkeypatch.setattr(pymupdf.Page,"find_tables",lambda *a,**k:SimpleNamespace(tables=[candidate]))
    monkeypatch.setattr("extractors.pymupdf_extractor._has_aligned_text_columns",lambda words:True)
    blocks=PyMuPDFExtractor()._extract_page(page,1)
    md=blocks_to_markdown(blocks)
    assert "Name Value" in md and "Alpha 10" in md
    assert "Surrounding prose must survive" in md
    assert not any(b.type==BlockType.TABLE for b in blocks)
    doc.close()


def test_partial_table_keeps_prose_in_same_native_block(monkeypatch):
    doc,page=fixture_page()
    candidate=SimpleNamespace(bbox=(30,60,130,102),col_count=2,rows=[],extract=lambda:[["Name","Value"],["Alpha","10"]])
    monkeypatch.setattr(pymupdf.Page,"find_tables",lambda *a,**k:SimpleNamespace(tables=[candidate]))
    monkeypatch.setattr("extractors.pymupdf_extractor._has_aligned_text_columns",lambda words:True)
    blocks=PyMuPDFExtractor()._extract_page(page,1)
    md=blocks_to_markdown(blocks)
    assert "Surrounding prose must survive" in md
    assert md.count("Alpha") == 1
    assert any(b.type==BlockType.TABLE for b in blocks)
    doc.close()


def test_straddling_word_is_never_dropped():
    word=(5,5,25,15,"crossing",0,0,0)
    assert PyMuPDFExtractor._words_outside_tables([word],[pymupdf.Rect(10,0,30,20)]) == [word]


def test_rejected_grid_cannot_shadow_prose_during_deduplication(monkeypatch):
    from services.layout_service import assign_reading_order
    doc,page=fixture_page()
    bbox=page.get_text("dict")["blocks"][0]["bbox"]
    candidate=SimpleNamespace(bbox=bbox,col_count=1,extract=lambda:[["Name Value Alpha 10"]])
    monkeypatch.setattr(pymupdf.Page,"find_tables",lambda *a,**k:SimpleNamespace(tables=[candidate]))
    monkeypatch.setattr("extractors.pymupdf_extractor._has_aligned_text_columns",lambda words:True)
    md=blocks_to_markdown(assign_reading_order(PyMuPDFExtractor()._extract_page(page,1)))
    assert "Surrounding prose must survive" in md
    doc.close()
