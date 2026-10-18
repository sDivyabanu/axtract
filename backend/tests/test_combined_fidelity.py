"""Combined-branch regressions: bare numbers are never formulas; soft hyphens at line ends are rejoined, compounds are not."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extractors.pymupdf_extractor import PyMuPDFExtractor, soft_hyphen_break
from services import scanned_math


# --- a lone number (page number, margin number, equation tag) is not a formula ---------------------------------

@pytest.mark.parametrize("text", ["36", " 7 ", "- 36 -", "(12)", "[4]", "12.", "— 9 —", "1024"])
def test_a_line_that_is_only_a_number_is_not_offered_to_the_recognizer(text):
    assert scanned_math.bare_number_line(text)


@pytest.mark.parametrize("text", ["", "x2", "3x", "2 + 3", "12345", "y = 4", "page 36", "3.14159", "∫ 2"])
def test_other_short_lines_are_not_treated_as_bare_numbers(text):
    assert not scanned_math.bare_number_line(text)


# --- soft hyphen rule -----------------------------------------------------------------------------------------------

def counts(*words):
    return Counter(w.casefold() for w in words)


@pytest.mark.parametrize("head,tail", [("In", "dian"), ("glob", "ally"), ("meta", "bolic"), ("popula", "tion"), ("govern", "ment"),
                                       ("epidemiolog", "ical"), ("Indo", "nesia"), ("strength", "ened"), ("partici", "pants"), ("informa", "tion")])
def test_word_fragments_are_rejoined(head, tail):
    assert soft_hyphen_break(head, tail, counts(head + "-", tail))


@pytest.mark.parametrize("head,tail,page_words", [
    ("long", "term", ()), ("self", "consistent", ()), ("pre", "and", ()), ("state", "of", ()), ("well", "known", ()),
    ("data", "driven", ("driven", "driven")),            # second half is a word on its own elsewhere on the page
    ("decision", "making", ("decision",)),              # first half is a word on its own elsewhere on the page
    ("x", "ray", ()), ("co", "operate", ()), ("anti", "inflammatory", ()),
    ("COVID", "19", ()),                                  # tail has to be letters
    ("run", "supercalifragilistic", ()),                  # implausibly long second half
    ("resource", "limited", ()), ("inversion", "eversion", ()),   # two long halves are two words (audited false joins)
])
def test_compounds_and_ambiguous_breaks_keep_their_hyphen(head, tail, page_words):
    assert not soft_hyphen_break(head, tail, counts(head + "-", tail, *page_words))


def page_with_lines(lines, size=11):
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=400)
    y = 80
    for text in lines:
        page.insert_text((40, y), text, fontsize=size)
        y += size * 1.25
    return page


def paragraphs(page):
    return [b.content for b in PyMuPDFExtractor()._extract_page(page, 1)]


def test_a_real_line_end_soft_hyphen_is_rejoined_in_extracted_text():
    page = page_with_lines(["The popula-", "tion grew in the region.", "It reached Indo-", "nesia last year."])
    text = " ".join(paragraphs(page))
    assert "population grew" in text and "Indonesia last year" in text and "popula-" not in text


def test_a_real_compound_hyphen_is_preserved_in_extracted_text():
    page = page_with_lines(["This is a long-", "term plan and a self-", "consistent one."])
    text = " ".join(paragraphs(page))
    assert "long-" in text and "self-" in text and "longterm" not in text and "selfconsistent" not in text


# --- stacked sub/superscripts of one base are one script group, not two columns ----------------------------------------

BENCH_PDF = Path("C:/Users/sdivy/wdev/Projects/axtract/benchmarks/olmOCR-bench/bench_data/pdfs/arxiv_math/2503.09432_pg6.pdf")


@pytest.mark.skipif(not BENCH_PDF.exists(), reason="olmOCR-bench PDFs not available")
def test_stacked_subscript_and_superscript_stay_on_one_base():
    from extractors import text_math
    page = pymupdf.open(str(BENCH_PDF))[0]
    toks, body = text_math._tokens(page.get_text("rawdict"))
    latex = " ".join(text_math.build_latex(r) for r in text_math._rows(toks, body))
    assert "Fr_{q}^{s}" in latex


# --- paragraph reconstruction from one-line blocks ---------------------------------------------------------------------

def line_block(text, y, x0=50.0, x1=300.0, size=10.0):
    return {"content": text, "bbox": (x0, y, x1, y + 11.0), "font_size": size, "is_bold": False, "math_font": False,
            "footnote_refs": [], "superscript_spans": [], "single_line": True, "line_wrap_repairs": []}


def merged(blocks, words=()):
    from extractors.pymupdf_extractor import merge_line_blocks
    counts = Counter(w.casefold() for w in words)
    return [b["content"] for b in merge_line_blocks(blocks, set(counts), counts)]


def test_one_line_blocks_of_a_paragraph_become_one_paragraph_with_the_hyphen_rejoined():
    out = merged([line_block("Question Answer-", 100), line_block("ing aims at the answer of a", 111),
                  line_block("question in large collections.", 122, x1=210)])
    assert out == ["Question Answering aims at the answer of a question in large collections."]


def test_a_short_last_line_and_an_indented_line_start_new_paragraphs():
    out = merged([line_block("The first paragraph runs across the whole column", 100), line_block("and ends here.", 111, x1=120),
                  line_block("Second paragraph starts and is long enough.", 122), line_block("Indented new paragraph begins", 133, x0=64.0)])
    assert out == ["The first paragraph runs across the whole column and ends here.",
                   "Second paragraph starts and is long enough.", "Indented new paragraph begins"]


def test_lines_in_different_columns_or_sizes_are_not_merged():
    out = merged([line_block("left column text", 100, x0=50, x1=150), line_block("right column text", 111, x0=300, x1=400),
                  line_block("A big heading", 140, size=18.0)])
    assert len(out) == 3


def test_a_compound_broken_at_its_own_hyphen_is_joined_without_a_space():
    out = merged([line_block("a fast add-mix and no-", 100), line_block("wash protocol that runs fast", 111)], words=("wash", "wash"))
    assert "no-wash" in out[0]


def test_inflection_endings_are_rejoined_even_when_the_stem_is_a_word():
    assert soft_hyphen_break("Answer", "ing", counts("answer-", "ing", "answer"))
    assert soft_hyphen_break("incre", "mentally", Counter({"incre-": 2, "mentally": 2}))


def test_a_dropped_th_ligature_is_repaired_only_on_an_english_page():
    from extractors.pymupdf_extractor import repair_dropped_th_ligature
    english = ["the"] * 4
    assert repair_dropped_th_ligature("Te assay. Tese cells.", english) == "The assay. These cells."
    assert repair_dropped_th_ligature("Te quiero mucho", ["de", "la", "que"]) == "Te quiero mucho"


# --- digital math: formulas the page broke across lines, slashed relations, ellipses --------------------------------

def test_an_inline_formula_split_by_a_line_break_is_one_formula():
    from extractors.text_math import join_split_math
    assert join_split_math(r"holds for $\dot{x}(t)=$ $N_{t}(x)$ and $a$, $b$ stay") == r"holds for $\dot{x}(t)= N_{t}(x)$ and $a$, $b$ stay"


def test_displays_join_only_across_an_open_relation_or_operator():
    from extractors.text_math import join_split_math
    assert join_split_math("$$x_{i}=$$\n" + r"$$\mathrm{col}(x)$$") == r"$$x_{i}= \mathrm{col}(x)$$"
    assert join_split_math("$$a=1$$\n$$b=2$$") == "$$a=1$$\n$$b=2$$"
    assert join_split_math("$$a$$\n$$+b$$") == "$$a +b$$"


def test_combining_slash_over_equals_becomes_not_equal_and_primes_stay_primes():
    from extractors.text_math import _negate_relations
    assert _negate_relations(r"\lambda^{'" + "\u0338" + r"}=\rho'") == r"\lambda'\ne\rho'"
