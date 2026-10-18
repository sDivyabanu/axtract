"""Scanned-page accuracy repairs (old scans + scanned math sprint). Synthetic blocks only: no models, no network."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.document import BlockType, DocumentBlock
from services.table_service import reconstruct_table_from_ocr


def ocr(bid, text, x0, y0, x1=None, h=0.02):
    return DocumentBlock(id=bid, type=BlockType.PARAGRAPH, content=text, page=1, bbox=(x0, y0, x1 or x0 + 0.12, y0 + h),
                         confidence=0.9, extractor="rapidocr", metadata={"route": "scanned"})


def page_of_prose_with_three_aligned_fragments():
    """The old_scans_math/3_pg204 shape: running head, display equations and prose, plus a few fragments that happen to
    line up in columns at the top and bottom of the page."""
    blocks = [ocr("h0", "196", 0.15, 0.06), ocr("h1", "DEVELOPMENTS AND APPLICATIONS", 0.35, 0.06), ocr("h2", "[CHAP.", 0.80, 0.06)]
    blocks += [ocr(f"p{i}", f"ordinary line of text number {i}", 0.15, 0.10 + 0.05 * i, 0.60) for i in range(12)]
    for r, y in enumerate((0.80, 0.88)):
        blocks += [ocr(f"r{r}a", "and if at that point", 0.15, y), ocr(f"r{r}b", "is not zero", 0.55, y), ocr(f"r{r}c", "did not", 0.80, y)]
    return blocks


def compact_table(extra_inside=0):
    blocks = []
    for r, y in enumerate((0.30, 0.34, 0.38, 0.42)):
        blocks += [ocr(f"t{r}0", f"item {r}", 0.15, y), ocr(f"t{r}1", f"{r * 3}.5", 0.45, y), ocr(f"t{r}2", f"{r * 7}.1", 0.75, y)]
    blocks += [ocr(f"note{i}", "footnote", 0.15, 0.32 + 0.002 * i, 0.30) for i in range(extra_inside)]
    return blocks


def test_a_page_of_prose_is_not_swallowed_by_a_page_sized_false_table():
    assert reconstruct_table_from_ocr(page_of_prose_with_three_aligned_fragments(), 1) is None


def test_a_genuine_compact_scanned_table_is_still_reconstructed():
    found = reconstruct_table_from_ocr(compact_table(), 1)
    assert found is not None
    table, consumed = found
    assert table.type == BlockType.TABLE and table.metadata["row_count"] == 4 and len(consumed) == 12


def test_a_table_with_a_few_interior_notes_is_still_kept():
    assert reconstruct_table_from_ocr(compact_table(extra_inside=2), 1) is not None


# --- plain short typed lines are never offered to the formula recognizer ----------------------------------------

import pytest
from PIL import Image

from services import scanned_math


@pytest.mark.parametrize("text", ["Dear Sir:-", "Iam,", "Yours truly,", "Indiana County", "the", "Hon. Theodore Roosevelt"])
def test_plain_word_lines_are_text(text):
    assert scanned_math.plain_text_line(text)


@pytest.mark.parametrize("text", [
    "", "   ",                                   # no OCR evidence: the recognizer decides
    "x = 3", "y2", "dx", "f(x)", "x + y",        # relations, digits and operators are math signal
    "sin x", "log y", "lim n",                   # function names stay candidates
    "\u03b1 \u03b2", "\u222b", "x \u2264 y",     # Greek and symbols
    "Let x = 2", "Hence x2",                     # words mixed with real signal
])
def test_lines_with_mathematical_signal_are_still_candidates(text):
    assert not scanned_math.plain_text_line(text)


def test_candidate_lines_exclude_short_typed_text_but_keep_formula_lines(monkeypatch):
    monkeypatch.setattr(scanned_math, "segment_lines", lambda image: [(100, 50, 900, 100), (100, 450, 900, 500)])
    blocks = [ocr("greeting", "Dear Sir:-", 0.10, 0.05, 0.90, h=0.1), ocr("formula", "x2 + y2 = 9", 0.10, 0.45, 0.90, h=0.1)]
    image = Image.new("RGB", (1000, 1000), "white")
    kept = [text for _, text, _ in scanned_math.candidates(image, blocks, 1)]
    assert kept == ["x2 + y2 = 9"]
