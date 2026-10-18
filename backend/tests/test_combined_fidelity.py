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
