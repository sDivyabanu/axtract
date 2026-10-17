"""Margin furniture classification must preserve body citations and footnotes."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from extractors.table_geometry import is_detached_margin_header


def block(bbox,font=10,bold=False,text=None):
    return {"bbox":bbox,"font_size":font,"is_bold":bold,"content":text or "word "*30}


def body():
    return [block((60,100,280,200)),block((60,220,280,300)),block((320,100,540,200)),block((320,220,540,300))]


def test_detached_small_wide_margin_line_has_multiple_geometric_signals():
    raw=block((60,8,540,28))
    assert is_detached_margin_header(raw,[raw]+body(),600,1000,10)


def test_large_or_bold_title_is_preserved():
    for raw in [block((60,8,540,28),font=16),block((60,8,540,28),bold=True)]:
        assert not is_detached_margin_header(raw,[raw]+body(),600,1000,10)


def test_narrow_body_citation_and_bottom_footnote_are_preserved():
    for raw in [block((60,8,280,28)),block((60,960,540,990)),block((60,110,540,130))]:
        assert not is_detached_margin_header(raw,[raw]+body(),600,1000,10)


def test_text_close_to_body_is_preserved():
    raw=block((60,8,540,28))
    nearby=block((60,32,540,60))
    assert not is_detached_margin_header(raw,[raw,nearby]+body(),600,1000,10)


def test_single_column_body_is_not_enough_evidence():
    raw=block((60,8,540,28)); blocks=[block((60,100,540,200)),block((60,220,540,400))]
    assert not is_detached_margin_header(raw,[raw]+blocks,600,1000,10)



def test_math_marked_source_is_never_margin_furniture():
    for flag in ["math_font","text_layer_math"]:
        raw=block((60,8,540,28));raw[flag]=True
        assert not is_detached_margin_header(raw,[raw]+body(),600,1000,10)
