"""Old-scan / scanned-math accuracy v2: scan-edge streaks, running heads, candidate priority, salvage of cut-off arrays."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import scanned_math
from services.vision.line_math import segment_lines


def page_with_lines(streak: bool, lines: int = 8, width: int = 600, height: int = 500) -> Image.Image:
    arr = np.full((height, width), 235, dtype=np.uint8)
    for i in range(lines):
        y = 40 + i * 50
        arr[y:y + 20, 60:520] = 40            # one band of ink per text line
    if streak:
        arr[:, width - 8:] = 30               # dark scan-edge shadow, inked on every row
        arr[::3, width - 14:width - 8] = 30   # ragged inner edge
    return Image.fromarray(arr)


def test_text_lines_are_found_on_a_clean_page():
    assert len(segment_lines(page_with_lines(streak=False))) == 8


def test_a_dark_edge_strip_does_not_fuse_the_lines():
    bands = segment_lines(page_with_lines(streak=True))
    assert len(bands) == 8
    assert all(y1 - y0 < 40 for _, y0, _, y1 in bands)


def test_a_streak_in_the_page_body_is_not_mistaken_for_text():
    arr = np.asarray(page_with_lines(streak=False)).copy()
    arr[:, 300:303] = 30                      # a long vertical rule through the body
    assert len(segment_lines(Image.fromarray(arr))) == 8


@pytest.mark.parametrize("text", ["AFFINE TRANSFORMATIONS 29", "56 SIMPLE AREAS AND VOLUMES", "DEAR SIR"])
def test_capital_only_page_headers_are_not_formulas(text):
    assert scanned_math.running_head_line(text)


@pytest.mark.parametrize("text", ["x = 2", "A + B = 0", "56 Simple Areas and Volumes Chap. 4", "NOTE.", "A B C = D", "y = sin x"])
def test_equation_like_or_mixed_case_lines_are_still_offered(text):
    assert not scanned_math.running_head_line(text)


def test_display_equations_are_decoded_before_ordinary_lines():
    equation = scanned_math.math_priority("x = a cos3 <j>, y = a sin3 <j>", (0.3, 0.5, 0.7, 0.52))
    sentence = scanned_math.math_priority("about the y-axis. 13. Find the volume", (0.1, 0.5, 0.95, 0.52))
    assert equation > sentence


def test_a_cut_off_array_keeps_its_finished_rows():
    latex = r"\begin{array} { l l } { a = b } & { c } \\ { d = e } & { f } \\ { g = h } & { i"
    kept = scanned_math.salvage_complete_rows(latex)
    assert kept.endswith(r"\end{array}")
    assert "g = h" not in kept and "d = e" in kept and kept.count("{") == kept.count("}")


@pytest.mark.parametrize("latex", ["a = b", r"\begin{array} { l } { a = b", r"x^{2} + y"])
def test_anything_else_is_not_salvaged(latex):
    assert scanned_math.salvage_complete_rows(latex) == ""
