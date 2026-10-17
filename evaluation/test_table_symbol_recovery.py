"""Source-font and geometry controls for table-only symbol recovery."""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from extractors.pymupdf_extractor import PyMuPDFExtractor


def recover(font="Wingdings",bbox=(10,10,20,20),geometry=True):
    table=SimpleNamespace(rows=[SimpleNamespace(cells=[(0,0,30,30)])] if geometry else [])
    rows=[["SOURCE\uf0e0TARGET"]]
    spans=[{"font":font,"text":"\uf0e0","bbox":bbox}]
    return rows,PyMuPDFExtractor._recover_table_symbols(table,rows,spans)


def test_verified_wingdings_arrow_is_recovered_with_provenance():
    original,(rows,changes)=recover()
    assert rows==[["SOURCE\u2192TARGET"]]
    assert original==[["SOURCE\uf0e0TARGET"]]
    assert changes==[{"row":0,"column":0,"font":"Wingdings","source":"\uf0e0","unicode":"\u2192"}]


def test_subset_font_prefix_is_supported():
    assert recover(font="ABCDEF+Wingdings")[1][0]==[["SOURCE\u2192TARGET"]]


def test_other_fonts_private_use_text_is_preserved():
    assert recover(font="Symbol")[1][0]==[["SOURCE\uf0e0TARGET"]]


def test_glyph_outside_cell_is_not_used():
    assert recover(bbox=(40,40,50,50))[1][0]==[["SOURCE\uf0e0TARGET"]]


def test_missing_cell_geometry_is_not_guessed():
    assert recover(geometry=False)[1][0]==[["SOURCE\uf0e0TARGET"]]
