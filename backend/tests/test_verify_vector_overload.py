"""Regression tests for the vector-overload guard in pdf.py.

When a page contains more vector objects (including nested form XObject children)
than ``pdf_vector_object_limit``, pdfplumber is skipped and text regions come from
pypdfium2 character boxes instead.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verify.inventory.pdf import inventory_pdf, _pdfium_words
from verify.inventory.options import InventoryOptions


# ---- _pdfium_words unit tests -----------------------------------------------

class FakeTextPage:
    """Minimal stub for pypdfium2 TextPage."""

    def __init__(self, chars: list[tuple[str, float, float, float, float]]):
        # chars: list of (ch, left, bottom_y, right, top_y) in PDF coords
        self._chars = chars

    def count_chars(self):
        return len(self._chars)

    def get_text_range(self, index, count):
        return self._chars[index][0]

    def get_charbox(self, index):
        return self._chars[index][1:]  # (left, bottom_y, right, top_y)


def test_pdfium_words_basic():
    """Characters on one baseline are grouped into words; space breaks a word."""
    # page_h = 100; chars at y=80..90 in PDF coords -> top=10, bottom=20 in pdfplumber coords
    chars = [
        ("H", 10, 80, 20, 90),
        ("i", 20, 80, 25, 90),
        (" ", 25, 80, 30, 90),  # space -> word break
        ("!", 35, 80, 40, 90),
    ]
    words = _pdfium_words(FakeTextPage(chars), page_h=100.0)
    assert len(words) == 2
    assert words[0]["text"] == "Hi"
    assert words[1]["text"] == "!"


def test_pdfium_words_multiline():
    """Characters on different baselines are separate words."""
    chars = [
        ("A", 10, 80, 20, 90),  # line 1
        ("B", 10, 60, 20, 70),  # line 2 (different y)
    ]
    words = _pdfium_words(FakeTextPage(chars), page_h=100.0)
    assert len(words) == 2
    assert words[0]["text"] == "A"
    assert words[1]["text"] == "B"


def test_pdfium_words_gap_breaks():
    """A horizontal gap > 3 pt splits words even without a space character."""
    chars = [
        ("A", 10, 80, 15, 90),
        ("B", 20, 80, 25, 90),  # gap = 20 - 15 = 5 > 3
    ]
    words = _pdfium_words(FakeTextPage(chars), page_h=100.0)
    assert len(words) == 2


def test_pdfium_words_empty():
    words = _pdfium_words(FakeTextPage([]), page_h=100.0)
    assert words == []


# ---- InventoryOptions default -----------------------------------------------

def test_default_vector_limit():
    opts = InventoryOptions()
    assert opts.pdf_vector_object_limit == 20_000


# ---- Integration: overload triggers on a real PDF if available ----------------

PATHOLOGICAL = Path(
    r"C:\Users\sdivy\wdev\Projects\axtract\benchmarks\olmOCR-bench\bench_data\pdfs\arxiv_math\2503.09528_pg16.pdf"
)


@pytest.mark.skipif(not PATHOLOGICAL.exists(), reason="benchmark PDF not present")
def test_pathological_pdf_triggers_overload():
    opts = InventoryOptions(pdf_vector_object_limit=20_000)
    inv = inventory_pdf(PATHOLOGICAL, opts)
    assert inv.properties.get("vector_overload_pages") == [1]
    unit = inv.units[0]
    assert unit.properties.get("vector_overload") is not None
    assert unit.properties["vector_overload"]["page_objects"] > 20_000
    # regions should exist (from pypdfium2 fallback)
    regions = [o for o in unit.objects if o.metadata.get("scope") == "region"]
    assert len(regions) > 0
    assert all(o.metadata.get("fallback") == "vector_overload" for o in regions)


@pytest.mark.skipif(not PATHOLOGICAL.exists(), reason="benchmark PDF not present")
def test_pathological_pdf_no_overload_with_high_limit():
    """With a very high limit, the guard does not fire and pdfplumber runs normally."""
    opts = InventoryOptions(pdf_vector_object_limit=999_999)
    inv = inventory_pdf(PATHOLOGICAL, opts)
    assert inv.properties.get("vector_overload_pages") in (None, [])


@pytest.mark.skipif(not PATHOLOGICAL.exists(), reason="benchmark PDF not present")
def test_pathological_completes_under_5s():
    """With the guard, inventory should complete in well under 5 seconds."""
    import time
    opts = InventoryOptions(pdf_vector_object_limit=20_000)
    t0 = time.perf_counter()
    inventory_pdf(PATHOLOGICAL, opts)
    assert time.perf_counter() - t0 < 5.0, "inventory took >5s even with the vector guard"


# ---- Normal PDF does not trigger overload ------------------------------------

NORMAL = Path(
    r"C:\Users\sdivy\wdev\Projects\axtract\benchmarks\olmOCR-bench\bench_data\pdfs\arxiv_math\2503.03765_pg1.pdf"
)


@pytest.mark.skipif(not NORMAL.exists(), reason="benchmark PDF not present")
def test_normal_pdf_no_overload():
    opts = InventoryOptions(pdf_vector_object_limit=20_000)
    inv = inventory_pdf(NORMAL, opts)
    assert inv.properties.get("vector_overload_pages") in (None, [])
    # regions come from pdfplumber (no fallback marker)
    regions = [o for o in inv.units[0].objects if o.metadata.get("scope") == "region"]
    assert len(regions) > 0
    assert all(o.metadata.get("fallback") is None for o in regions)
