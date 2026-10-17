"""Ownership regression tests using raw glyph structures; no generated PDFs/answers."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from extractors import text_math as math


def raw_block(text, x=10, font="CMMI10"):
    return {"type": 0, "lines": [{"spans": [{"font": font, "size": 10, "flags": 0,
        "chars": [{"c": c, "bbox": (x+i*8, 10, x+i*8+7, 20),
                   "origin": (x+i*8, 18)} for i, c in enumerate(text)]}]}]}


def test_exclusion_preserves_same_block_math_and_does_not_mutate_source():
    raw = {"blocks": [raw_block("xyz")]}
    original = copy.deepcopy(raw)
    result = math.rebuild_page(raw, [], 600, excluded_rects=[(10, 10, 17, 20)])
    assert "x" not in result.blocks[0]
    assert "y" in result.blocks[0] and "z" in result.blocks[0]
    assert raw == original


def test_default_empty_exclusions_preserve_snapshot_api():
    raw = {"blocks": [raw_block("xyz")]}
    assert math.rebuild_page(raw, [], 600) == math.rebuild_page(
        raw, [], 600, excluded_rects=[], excluded_rule_rects=[])


def test_fully_owned_block_cannot_reemit_math():
    raw = {"blocks": [raw_block("xyz")]}
    assert math.rebuild_page(raw, [], 600, excluded_rects=[(0, 0, 100, 100)]).blocks == {}


def test_table_rule_is_not_consumed_as_fraction_bar(monkeypatch):
    raw = {"blocks": [raw_block("x")]}
    monkeypatch.setattr(math, "_bars", lambda *_: [(10, 80, 15, 1), (110, 180, 15, 1)])
    seen = []
    def consume(tokens, bars, body):
        seen.extend(bars)
        return tokens
    monkeypatch.setattr(math, "_consume_structures", consume)
    math.rebuild_page(raw, [], 600, excluded_rule_rects=[(0, 0, 100, 100)])
    assert seen == [(110, 180, 15, 1)]


def test_moved_prose_row_has_exactly_one_owner(monkeypatch):
    raw = {"blocks": [raw_block("x"), raw_block("abc", 18, "Arial"),
                       raw_block("def", 42, "Arial")]}
    tokens, body = math._tokens(raw)
    row1 = [t for t in tokens if t.block in (0, 1)]
    row2 = [copy.copy(tokens[0])] + [t for t in tokens if t.block == 2]
    row2[0].text = row2[0].latex = "p"
    row2[0].math = False
    monkeypatch.setattr(math, "_consume_structures", lambda *a: a[0])
    monkeypatch.setattr(math, "_rows", lambda *a: [row1, row2])
    monkeypatch.setattr(math, "_render_row", lambda row, body:
        ("math-row", 1, 0, True) if row is row1 else ("prose-row", 0, 0, False))
    rebuilt = math.rebuild_page(raw, [], 600)
    assert set(rebuilt.blocks) == {0, 1, 2}
    contents = " ".join(rebuilt.blocks.values())
    assert contents.count("math-row") == contents.count("prose-row") == 1


def test_fraction_consumed_from_second_block_is_not_reemitted(monkeypatch):
    import pymupdf
    numerator = raw_block("x")
    denominator = raw_block("y")
    ch = denominator["lines"][0]["spans"][0]["chars"][0]
    ch["bbox"] = (10, 25, 17, 35)
    ch["origin"] = (10, 33)
    drawing = [{"items": [("l", pymupdf.Point(9, 23), pymupdf.Point(18, 23))], "width": 1}]
    result = math.rebuild_page({"blocks": [numerator, denominator]}, drawing, 600)
    assert set(result.blocks) == {0, 1}
    assert "\\frac" in " ".join(result.blocks.values())
    assert result.blocks[1] == ""


def test_reconstructed_relations_survive_output_safety():
    from security.output_safety import sanitise_block_text
    raw = {"blocks": [raw_block("x>y")]}
    content = math.rebuild_page(raw, [], 600).blocks[0]
    assert "\\gt" in content and ">" not in content
    assert sanitise_block_text(content) == content
    assert "&lt;" in sanitise_block_text("<img src=x onerror='bad'>")
