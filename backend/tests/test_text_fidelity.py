"""Text-fidelity regressions from Ram's review: punctuation after footnote markers and HTML entities in Markdown."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extractors.pymupdf_extractor import strip_superscript_refs
from models.document import BlockType, DocumentBlock
from security.output_safety import sanitise_block_text
from services.markdown_service import blocks_to_markdown


# --- punctuation next to superscript footnote markers ---------------------------------------------------------

@pytest.mark.parametrize("text,refs,expected", [
    ("Smith et al. and later work2, showed that", ["2"], "Smith et al. and later work, showed that"),
    ("values rose1, then fell", ["1"], "values rose, then fell"),
    ("the result3. Next", ["3"], "the result. Next"),
    ("see work1,2 for details", ["1", "2"], "see work for details"),            # a marker LIST loses its inner comma
    ("see work1,2, and then", ["1", "2"], "see work, and then"),                # the sentence comma after it stays
    ("a note4; a second5: end", ["4", "5"], "a note; a second: end"),
    ("quoted (work6), and more", ["6"], "quoted (work), and more"),
])
def test_footnote_markers_are_removed_but_punctuation_survives(text, refs, expected):
    assert strip_superscript_refs(text, refs) == expected


def test_text_without_markers_is_untouched():
    text = "Totals: 1, 2, 3; ratios 4:5, and 6."
    assert strip_superscript_refs(text, []) == text
    assert strip_superscript_refs(text, ["x"]) == text  # non-digit refs are ignored


def test_multi_digit_markers_are_handled_longest_first():
    assert strip_superscript_refs("trial12, was run; trial1, ended", ["12"]) == "trial, was run; trial1, ended"


# --- HTML entities in Markdown ----------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "BMI was categorized as normal (<25 kg/m2), overweight (25-29.9)",   # 027c3905...order_c48119cc
    "p < 0.05 and x > y", "5 < 6", "a <= b", "n<10", "value >= 3", "-> arrow", "R&D & \"quotes\" and 'single'",
    "literal &lt; and &gt; and &amp; stay as the writer typed them",
])
def test_ordinary_text_and_comparisons_are_not_rewritten(text):
    assert sanitise_block_text(text) == text


@pytest.mark.parametrize("text", ["<b>bold</b>", "<img src=x onerror='bad'>", "<!-- unterminated", "<?php x ?>", "</div>", "x<y and z>w"])
def test_anything_that_could_open_html_is_neutralised(text):
    out = sanitise_block_text(text)
    assert "<" not in out and "&lt;" in out


def test_scripts_and_handlers_are_still_removed():
    assert sanitise_block_text("a <script>alert(1)</script> b") == "a [script removed] b"
    assert "onerror" not in sanitise_block_text("<img src=x onerror='bad'>")


def test_markdown_output_never_double_escapes():
    blocks = [DocumentBlock(id="p", type=BlockType.PARAGRAPH, page=1, bbox=(0, 0, 1, .1), confidence=None, extractor="t", metadata={},
                            content=sanitise_block_text("normal (<25 kg/m2) & <b>x</b> &lt;ok&gt;"))]
    md = blocks_to_markdown(blocks)
    assert "&amp;lt;" not in md and "&amp;gt;" not in md and "(<25 kg/m2)" in md and "&lt;ok&gt;" in md


# --- exponents of units and variables are text, not footnote markers ---------------------------------------------

@pytest.mark.parametrize("text,refs,expected", [
    ("normal (<25 kg/m2), overweight", ["2"], "normal (<25 kg/m\u00b2), overweight"),     # 027c3905: kg/m^2
    ("area 5 cm2 and 3 m3", ["2", "3"], "area 5 cm\u00b2 and 3 m\u00b3"),
    ("x2 + y2 = 9", ["2"], "x\u00b2 + y\u00b2 = 9"),
    ("cited in work2, and Smith et al.2 agree", ["2"], "cited in work, and Smith et al. agree"),  # markers after words/punctuation go
    ("the result3 held", ["3"], "the result held"),
])
def test_exponents_become_superscript_characters_and_footnote_markers_still_go(text, refs, expected):
    assert strip_superscript_refs(text, refs) == expected


@pytest.mark.parametrize("text,refs,expected", [
    ("page 2 of 3 and 3 m3", ["3"], "page 2 of 3 and 3 m³"),             # real numbers survive a same-digit marker
    ("see page 2 for details2, and more", ["2"], "see page 2 for details, and more"),
    ("Smith et al.2 and Jones (2020).3 agree", ["2", "3"], "Smith et al. and Jones (2020). agree"),
])
def test_real_numbers_are_never_deleted_because_a_footnote_shares_the_digit(text, refs, expected):
    assert strip_superscript_refs(text, refs) == expected
