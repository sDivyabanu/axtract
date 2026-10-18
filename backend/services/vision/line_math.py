"""Scanned math pages: split the page into text lines, run the formula model on non-prose lines.

Why lines: OCR boxes split stacked fractions and merge neighbouring lines, so crops from them rarely
contain exactly one equation. A projection profile of the ink separates lines cleanly, and keeps a
fraction (numerator, bar, denominator: gaps much smaller than a line gap) in one band.
The source prototype reported 22/84 on six scanned pages, without a saved crop
manifest. That historical experiment is not a verified pipeline result. Default
normalization preserves bold/vector notation and text spaces; the lossy legacy
cleanup is available only for separately labelled offline diagnostics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from PIL import Image

_CTRL_SPACE = chr(1)  # marks a TeX control space while ordinary spaces are removed
_KEEP = chr(2)  # marks the one space that must stay after a control word


@dataclass
class LineCrop:
    bbox_px: tuple[int, int, int, int]  # x0, y0, x1, y1 on the rendered page
    ocr_text: str = ""


def _without_scan_artifacts(ink: np.ndarray) -> np.ndarray:
    """Drop vertical scan artefacts (binding shadow, page-edge streaks, long rules) from the ink mask.

    Such a streak is dark on a large share of the rows, so the row profile never reaches a gap and every text line above and
    below it fuses into one tall band that the equation recogniser then rejects. Two cases are removed: any column inked over
    most of the page height (a long rule), and a narrow dark strip that starts at the page edge (shadow of the binding or of
    the scanner lid), measured inward while the columns stay inked."""
    h, w = ink.shape
    share = ink.mean(axis=0)
    streak = share > 0.60
    reach = max(3, int(0.05 * w))
    for edge_columns in (range(w - 1, w - 1 - reach, -1), range(0, reach)):
        run = []
        for x in edge_columns:
            if share[x] <= 0.06:
                break
            run.append(x)
        outer = abs(edge_columns[0] - run[0]) if run else reach
        if run and outer <= max(2, int(0.02 * w)) and max(share[x] for x in run) >= 0.15 and len(run) < reach:
            streak[run] = True
    if not streak.any():
        return ink
    grown = streak.copy()
    for shift in (1, 2, 3):
        grown[shift:] |= streak[:-shift]
        grown[:-shift] |= streak[shift:]
    cleaned = ink.copy()
    cleaned[:, grown] = False
    return cleaned


def segment_lines(img: Image.Image, gap_frac: float = 0.30, min_h: int = 14, min_w: int = 25) -> list[tuple[int, int, int, int]]:
    """Text-line bands of a page image by horizontal ink projection."""
    g = np.asarray(img.convert("L"), dtype=np.float32)
    if g.size == 0:
        return []
    ink = g < (np.percentile(g, 60) - 55)  # paper tone varies on old scans: threshold relative to it
    h, w = ink.shape
    ink = _without_scan_artifacts(ink)
    rows = ink.sum(axis=1) > max(2, 0.002 * w)
    bands: list[list[int]] = []
    start = None
    for y in range(h):
        if rows[y] and start is None:
            start = y
        elif not rows[y] and start is not None:
            bands.append([start, y])
            start = None
    if start is not None:
        bands.append([start, h])
    bands = [b for b in bands if b[1] - b[0] >= 3]
    if not bands:
        return []
    med = float(np.median([b[1] - b[0] for b in bands]))
    merged = [bands[0]]
    for b in bands[1:]:
        if b[0] - merged[-1][1] < gap_frac * med:  # small gap: fraction bar, accent, limits
            merged[-1][1] = b[1]
        else:
            merged.append(b)
    out = []
    for y0, y1 in merged:
        if y1 - y0 < min_h:
            continue
        cx = np.where(ink[y0:y1].sum(axis=0) > 0)[0]
        if len(cx) == 0 or cx[-1] - cx[0] < min_w:
            continue
        out.append((int(cx[0]), int(y0), int(cx[-1]), int(y1)))
    return out


def looks_like_prose(text: str) -> bool:
    """Three or more ordinary words: a sentence, not an equation line."""
    return len(re.findall(r"[A-Za-z]{4,}", text or "")) >= 3


def strip_style(latex: str, *, preserve_semantics: bool = True) -> str:
    """Remove display-size hints while preserving mathematical notation by default.

    preserve_semantics=False reproduces historical lossy cleanup for diagnostics;
    it can remove vector/bold distinctions and must not be used in production.
    """
    s = latex
    if preserve_semantics:
        # Bold/roman/script letters can distinguish vectors, operators and scalars.
        # Preserve them, all delimiters, text spaces and explicit TeX control spaces.
        return re.sub(r"\\(?:displaystyle|textstyle|scriptstyle|scriptscriptstyle|normalsize|small|footnotesize|large|Large)(?![A-Za-z])\s*", " ", s)
    # Historical experimental cleanup only: removing bold notation is lossy.
    s = re.sub(r"\\(?:left|right)\s*\.", " ", s)  # invisible delimiters
    s = re.sub(r"\\(?:left|right|bigl|bigr|Bigl|Bigr|biggl|biggr|Biggl|Biggr|big|Big|bigg|Bigg)(?![A-Za-z])\s*", "", s)
    s = re.sub(r"\\(?:displaystyle|textstyle|scriptstyle|scriptscriptstyle|normalsize|small|footnotesize|large|Large|boldmath)(?![A-Za-z])\s*", " ", s)
    s = re.sub(r"\\[,:;!]", " ", s)
    s = re.sub(r"\\(?:quad|qquad|enspace|thinspace|negthinspace)(?![A-Za-z])", " ", s)
    s = s.replace("\\ ", " ").replace("~", " ")
    s = re.sub(r"\{\\bf\s+([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\(?:mathbf|boldsymbol|textbf)\{([^{}]*)\}", r"\1", s)
    return s


def tidy_latex(raw: str, *, preserve_semantics: bool = True) -> str:
    """Preserve semantic TeX and escape angle relations for Markdown sanitation.

    The explicit legacy option compacts token spaces and is diagnostic only.
    """
    if preserve_semantics:
        return strip_style(raw, preserve_semantics=True).strip().replace("<", r"\lt ").replace(">", r"\gt ")
    s = strip_style(raw, preserve_semantics=False).replace("\\ ", _CTRL_SPACE)
    s = re.sub(r"(\\[A-Za-z]+) (?=[A-Za-z])", lambda m: m.group(1) + _KEEP, s)
    s = s.replace(" ", "")
    return s.replace(_KEEP, " ").replace(_CTRL_SPACE, "\\ ").strip()
