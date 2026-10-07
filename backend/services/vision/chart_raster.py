"""Read bar / line / scatter / pie charts from a raster image (deterministic, no model).

Method: OCR for text, OpenCV for geometry.
  1. OCR the image (RapidOCR, already a dependency) -> words with boxes.
  2. Find the plot frame (long dark horizontal/vertical lines) -> axes.
  3. Calibrate pixel->value with the numeric tick labels, snapped to the real tick marks;
     keep the fit only when it is accurate.
  4. Separate series by colour, decide the chart type from component geometry, and read
     values (bar tops, line points at category ticks, scatter marker centres).
  5. Prefer printed data labels when they agree with the measurement (exact transcription),
     otherwise report the measured value with values_estimated=True.
  Pies (no axes): colour-cluster the disc, report printed percentages if present, else area
  shares.

Never invents data: anything that cannot be read is omitted (None) and flagged.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from PIL import Image

from services.table_service import _to_number

MAX_SIDE = 2000


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return self.y1 - self.y0


def parse_number(text: str) -> float | None:
    t = text.strip().replace("−", "-").replace("O", "0") if re.fullmatch(r"[\dO.,\-$%\s]+", text.strip()) else text.strip()
    t = re.sub(r"[$€£¥%]", "", t).strip()
    if not t:
        return None
    n = _to_number(t)
    if n is not None:
        return n
    try:
        return float(t) if re.fullmatch(r"[+-]?\d+(\.\d+)?", t) else None
    except ValueError:
        return None


def ocr_words(img: Image.Image) -> list[Word]:
    from extractors.ocr_extractor import _get_ocr

    res, _ = _get_ocr()(np.array(img.convert("RGB")))
    words: list[Word] = []
    for points, text, conf in res or []:
        text = str(text).strip()
        if not text:
            continue
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        words.append(Word(text, min(xs), min(ys), max(xs), max(ys), float(conf)))
    return words


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


@dataclass
class Axis:
    """Linear map pixel -> value fitted from labelled ticks."""

    slope: float
    intercept: float
    n: int
    max_err: float
    span: float

    def value(self, pix: float) -> float:
        return self.slope * pix + self.intercept

    def pixel(self, value: float) -> float:
        return (value - self.intercept) / self.slope if self.slope else float("nan")


def fit_axis(pairs: list[tuple[float, float]]) -> Axis | None:
    """Least squares on (pixel, value) pairs; drops one outlier if the fit is poor."""
    if len(pairs) < 2:
        return None

    def fit(ps):
        p = np.array([a for a, _ in ps])
        v = np.array([b for _, b in ps])
        if np.ptp(p) < 1e-6 or np.ptp(v) < 1e-12:
            return None
        slope, intercept = np.polyfit(p, v, 1)
        err = np.abs(slope * p + intercept - v)
        return slope, intercept, err

    f = fit(pairs)
    if f is None:
        return None
    slope, intercept, err = f
    span = float(np.ptp([b for _, b in pairs]))
    if err.max() > 0.01 * span and len(pairs) >= 4:
        worst = int(err.argmax())
        reduced = pairs[:worst] + pairs[worst + 1:]
        f2 = fit(reduced)
        if f2 is not None and f2[2].max() < err.max():
            slope, intercept, err = f2
            pairs = reduced
            span = float(np.ptp([b for _, b in pairs]))
    return Axis(float(slope), float(intercept), len(pairs), float(err.max()), span)


def _decimals(axis: Axis) -> int:
    res = abs(axis.slope)  # value change per pixel
    if res <= 0:
        return 2
    return max(0, -int(math.floor(math.log10(res))))


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _long_lines(dark: np.ndarray) -> tuple[list[tuple[int, int, int, int]], list[tuple[int, int, int, int]]]:
    """Long thin horizontal and vertical dark components as (x0, y0, x1, y1)."""
    h, w = dark.shape
    img = (dark.astype(np.uint8)) * 255
    hor = cv2.morphologyEx(img, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, w // 8), 1)))
    ver = cv2.morphologyEx(img, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(25, h // 8))))

    def comps(mask, horizontal):
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        out = []
        for i in range(1, n):
            x, y, ww, hh, _a = stats[i]
            if horizontal and hh <= 8 and ww >= 0.3 * w:
                out.append((x, y, x + ww, y + hh))
            if not horizontal and ww <= 8 and hh >= 0.3 * h:
                out.append((x, y, x + ww, y + hh))
        return out

    return comps(hor, True), comps(ver, False)


def find_plot_rect(rgb: np.ndarray) -> tuple[int, int, int, int] | None:
    gray = rgb.min(axis=2)
    dark = gray < 150
    hor, ver = _long_lines(dark)
    if not hor or not ver:
        return None
    xs0 = min(min(l[0] for l in hor), min(l[0] for l in ver))
    ys0 = min(min(l[1] for l in hor), min(l[1] for l in ver))
    xs1 = max(max(l[2] for l in hor), max(l[2] for l in ver))
    ys1 = max(max(l[3] for l in hor), max(l[3] for l in ver))
    h, w = gray.shape
    if (xs1 - xs0) * (ys1 - ys0) < 0.2 * w * h:
        return None
    return int(xs0), int(ys0), int(xs1), int(ys1)


def _tick_positions(dark: np.ndarray, rect, side: str) -> list[float]:
    """Centres of tick marks just outside the frame ('left' -> y positions, 'bottom' -> x)."""
    left, top, right, bottom = rect
    h, w = dark.shape
    if side == "left":
        cols = [c for c in range(max(0, left - 9), max(0, left - 2))]
        if not cols:
            return []
        band = dark[max(0, top - 2): min(h, bottom + 3), cols].sum(axis=1) >= max(2, len(cols) // 2)
        offset = max(0, top - 2)
    else:
        rows = [r for r in range(min(h - 1, bottom + 3), min(h, bottom + 10))]
        if not rows:
            return []
        band = dark[rows, max(0, left - 2): min(w, right + 3)].sum(axis=0) >= max(2, len(rows) // 2)
        offset = max(0, left - 2)
    pos, start = [], None
    for i, v in enumerate(band):
        if v and start is None:
            start = i
        elif not v and start is not None:
            pos.append(offset + (start + i - 1) / 2)
            start = None
    if start is not None:
        pos.append(offset + (start + len(band) - 1) / 2)
    return pos


def _snap(pix: float, ticks: list[float], tol: float = 7.0) -> tuple[float, bool]:
    if not ticks:
        return pix, False
    best = min(ticks, key=lambda t: abs(t - pix))
    return (best, True) if abs(best - pix) <= tol else (pix, False)


# ---------------------------------------------------------------------------
# Colour clustering
# ---------------------------------------------------------------------------


def _color_clusters(rgb: np.ndarray, region: np.ndarray, min_pixels: int) -> list[dict[str, Any]]:
    """Dominant saturated colours inside `region` (bool mask)."""
    sat = rgb.max(axis=2).astype(int) - rgb.min(axis=2).astype(int)
    colored = region & (sat >= 40) & (rgb.min(axis=2) < 235)
    if colored.sum() < min_pixels:
        return []
    px = rgb[colored].astype(int)
    keys = (px // 32)
    packed = keys[:, 0] * 64 + keys[:, 1] * 8 + keys[:, 2]
    uniq, inv, counts = np.unique(packed, return_inverse=True, return_counts=True)
    order = np.argsort(-counts)
    centers: list[np.ndarray] = []
    totals: list[int] = []
    for idx in order:
        if counts[idx] < min_pixels * 0.3:
            break
        c = np.median(px[inv == idx], axis=0)
        for k, existing in enumerate(centers):
            if np.linalg.norm(existing - c) < 75:
                totals[k] += int(counts[idx])
                break
        else:
            centers.append(c)
            totals.append(int(counts[idx]))
    clusters = []
    for c, t in sorted(zip(centers, totals), key=lambda z: -z[1]):
        if t >= min_pixels:
            dist = np.linalg.norm(rgb.astype(int) - c, axis=2)
            clusters.append({"color": [int(v) for v in c], "mask": region & (dist < 55), "pixels": t})
    return clusters


def _components(mask: np.ndarray, min_area: int):
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a >= min_area:
            out.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h), "area": int(a),
                        "cx": float(cents[i][0]), "cy": float(cents[i][1]), "fill": a / max(1, w * h)})
    return out


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


@dataclass
class ChartReading:
    data: dict[str, Any]
    confidence: float
    flags: list[str] = field(default_factory=list)


def read_chart(image: Image.Image, hint_title: str = "") -> ChartReading | None:
    """Return a ChartReading, or None when the image is not a chart we can read."""
    img = image.convert("RGB")
    if max(img.size) > MAX_SIDE:
        scale = MAX_SIDE / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
    if min(img.size) < 120:
        return None
    rgb = np.array(img)
    words = ocr_words(img)

    rect = find_plot_rect(rgb)
    if rect is not None:
        return _read_axis_chart(rgb, words, rect, hint_title)
    return _read_pie(rgb, words, hint_title)


def _title_of(words: list[Word], limit_y: float, width: int, hint: str) -> str:
    cands = [w for w in words if w.cy < limit_y and abs(w.cx - width / 2) < width * 0.35
             and parse_number(w.text) is None]
    if not cands:
        return hint
    tallest = max(w.h for w in cands)
    # the title is the topmost line among the tall ones (not a slice label / tick label)
    return min((w for w in cands if w.h >= 0.6 * tallest), key=lambda w: w.y0).text


def _read_axis_chart(rgb: np.ndarray, words: list[Word], rect, hint_title: str) -> ChartReading | None:
    H, W = rgb.shape[:2]
    left, top, right, bottom = rect
    gray = rgb.min(axis=2)
    dark = gray < 150
    flags: list[str] = []

    # --- axes ------------------------------------------------------------
    left_words = [w for w in words if w.x1 <= left + 2 and top - 20 <= w.cy <= bottom + 20 and w.x0 > left - 0.25 * W]
    below = [w for w in words if w.y0 >= bottom + 1 and w.cy <= bottom + 0.14 * H and left - 0.1 * W <= w.cx <= right + 0.1 * W]
    if below:
        row_y = min(w.cy for w in below)
        below = [w for w in below if abs(w.cy - row_y) <= max(6, 0.7 * np.median([w.h for w in below]))]

    y_ticks = _tick_positions(dark, rect, "left")
    x_ticks = _tick_positions(dark, rect, "bottom")

    y_pairs = []
    for w in left_words:
        v = parse_number(w.text)
        if v is not None:
            y_pairs.append((_snap(w.cy, y_ticks)[0], v))
    x_nums = [(w, parse_number(w.text)) for w in below]
    x_numeric = len(below) >= 2 and all(v is not None for _, v in x_nums)
    x_pairs = [(_snap(w.cx, x_ticks)[0], v) for w, v in x_nums if v is not None] if x_numeric else []

    y_axis = fit_axis(y_pairs)
    x_axis = fit_axis(x_pairs) if x_numeric else None
    cat_labels = [] if x_numeric else sorted(below, key=lambda w: w.cx)
    # categorical y labels (horizontal bars)
    y_cats = sorted([w for w in left_words if parse_number(w.text) is None], key=lambda w: w.cy)

    # --- series ----------------------------------------------------------
    inner = np.zeros((H, W), bool)
    inner[top + 3: bottom - 2, left + 3: right - 2] = True
    plot_area = (right - left) * (bottom - top)
    clusters = _color_clusters(rgb, inner, max(60, int(plot_area * 0.0015)))
    if not clusters:
        return None

    typed = []
    for cl in clusters:
        comps = _components(cl["mask"], max(8, int(plot_area * 0.00005)))
        if comps:
            typed.append((cl, comps))
    if not typed:
        return None

    def bar_like(c):
        return c["fill"] >= 0.85 and c["w"] >= 5 and c["h"] >= 3 and c["area"] >= plot_area * 0.002

    n_bars = sum(sum(bar_like(c) for c in comps) for _, comps in typed)
    all_comps = [c for _, comps in typed for c in comps]
    med_area = float(np.median([c["area"] for c in all_comps]))
    small_round = [c for c in all_comps if 0.5 <= c["w"] / max(1, c["h"]) <= 2.0 and c["w"] < 0.06 * (right - left)
                   and c["h"] < 0.06 * (bottom - top)]
    big_span = [c for c in all_comps if c["w"] >= 0.35 * (right - left) and c["fill"] < 0.6]

    if n_bars >= 2 and n_bars >= 0.6 * len(all_comps):
        horizontal = np.median([c["w"] for c in all_comps if bar_like(c)]) > np.median([c["h"] for c in all_comps if bar_like(c)]) \
            and y_axis is None and bool(y_cats) and not cat_labels
        chart_type = "bar"
        orientation = "horizontal" if horizontal else "vertical"
    elif big_span:
        chart_type, orientation = "line", "vertical"
    elif len(small_round) >= 3 and len(small_round) >= 0.7 * len(all_comps):
        chart_type, orientation = "scatter", "vertical"
    elif n_bars == 1:
        chart_type, orientation = "bar", "vertical"
    else:
        return None

    if y_axis is None and orientation == "vertical":
        flags.append("y_axis_not_calibrated")
    if y_axis is not None and y_axis.max_err > 0.02 * y_axis.span:
        flags.append("y_axis_calibration_poor")

    dec = _decimals(y_axis) if y_axis else 2
    categories: list[str] = []
    series_out: list[dict[str, Any]] = []
    labels_used = labels_total = 0
    number_words = [(w, parse_number(w.text)) for w in words]
    number_words = [(w, v) for w, v in number_words if v is not None]

    # --- legend names ----------------------------------------------------
    def series_name(cluster_idx: int, comps) -> str:
        if len(typed) == 1:
            return ""
        swatches = [c for c in comps if c["area"] < 0.25 * max(cc["area"] for cc in comps) and 0.4 < c["w"] / max(1, c["h"]) < 4]
        for s in swatches:
            near = [w for w in words if 0 <= w.x0 - (s["x"] + s["w"]) <= 40 and abs(w.cy - s["cy"]) <= max(8, s["h"])]
            if near:
                return min(near, key=lambda w: w.x0 - s["x"]).text
        return ""

    if chart_type == "bar" and orientation == "vertical":
        cats_x = [(w.cx, w.text) for w in cat_labels]
        baseline = y_axis.pixel(0.0) if (y_axis and top <= y_axis.pixel(0.0) <= bottom + 3) else float(bottom)
        for ci, (cl, comps) in enumerate(typed):
            bars = sorted([c for c in comps if bar_like(c)], key=lambda c: c["x"])
            if not bars:
                continue
            by_cat: dict[int, tuple[float | None, bool]] = {}
            for b in bars:
                if cats_x:
                    k = int(np.argmin([abs(b["cx"] - x) for x, _ in cats_x]))
                else:
                    k = len(by_cat)
                top_y, bot_y = b["y"], b["y"] + b["h"]
                if abs(bot_y - baseline) <= 4:
                    edge = top_y
                elif abs(top_y - baseline) <= 4:
                    edge = bot_y
                else:
                    edge = top_y
                    flags.append("floating_or_stacked_bar")
                measured = round(y_axis.value(edge), dec) if y_axis else None
                exact = False
                if measured is not None:
                    tol = max(0.02 * y_axis.span, 1.5 * abs(y_axis.slope))
                    cand = [(w, v) for w, v in number_words
                            if b["x"] - 4 <= w.cx <= b["x"] + b["w"] + 4 and top - 50 <= w.cy <= bottom
                            and abs(w.cy - edge) <= 60 and w.x0 > left and abs(v - measured) <= tol]
                    labels_total += 1
                    if cand:
                        near = min(cand, key=lambda t: abs(t[0].cy - edge))
                        measured, exact = near[1], True
                        labels_used += 1
                by_cat[k] = (measured, exact)
            n = len(cats_x) if cats_x else len(bars)
            values = [by_cat.get(i, (None, False))[0] for i in range(n)]
            if cats_x and not categories:
                categories = [t for _, t in cats_x]
            series_out.append({"name": series_name(ci, comps), "values": values,
                               "exact": [by_cat.get(i, (None, False))[1] for i in range(n)]})

    elif chart_type == "bar":  # horizontal
        if x_axis is None:
            flags.append("x_axis_not_calibrated")
        baseline_x = x_axis.pixel(0.0) if (x_axis and left - 3 <= x_axis.pixel(0.0) <= right) else float(left)
        ylab = [(w.cy, w.text) for w in y_cats]
        decx = _decimals(x_axis) if x_axis else 2
        for ci, (cl, comps) in enumerate(typed):
            bars = sorted([c for c in comps if bar_like(c)], key=lambda c: c["y"])
            by_cat = {}
            for b in bars:
                k = int(np.argmin([abs(b["cy"] - y) for y, _ in ylab])) if ylab else len(by_cat)
                edge = b["x"] + b["w"] if abs(b["x"] - baseline_x) <= 4 else b["x"]
                measured = round(x_axis.value(edge), decx) if x_axis else None
                exact = False
                if measured is not None:
                    tol = max(0.02 * x_axis.span, 1.5 * abs(x_axis.slope))
                    cand = [(w, v) for w, v in number_words
                            if abs(w.cy - b["cy"]) <= max(10, b["h"]) and abs(w.cx - edge) <= 80
                            and w.y0 < bottom and abs(v - measured) <= tol]
                    labels_total += 1
                    if cand:
                        measured, exact = min(cand, key=lambda t: abs(t[0].cx - edge))[1], True
                        labels_used += 1
                by_cat[k] = (measured, exact)
            n = len(ylab) if ylab else len(bars)
            if ylab and not categories:
                categories = [t for _, t in ylab]
            series_out.append({"name": series_name(ci, comps), "values": [by_cat.get(i, (None, False))[0] for i in range(n)],
                               "exact": [by_cat.get(i, (None, False))[1] for i in range(n)]})

    elif chart_type == "line":
        if cat_labels:
            xs = [(w.cx, w.text) for w in cat_labels]
        elif x_axis is not None:
            xs = [(p, f"{v:g}") for p, v in x_pairs]
        else:
            xs = []
            flags.append("no_x_labels")
        categories = [t for _, t in xs]
        win = max(2, int(0.004 * W))
        for ci, (cl, comps) in enumerate(typed):
            mask = cl["mask"]
            values = []
            for x, _t in xs:
                cols = mask[top:bottom, max(0, int(x) - win): int(x) + win + 1]
                ys, _ = np.nonzero(cols)
                if ys.size >= 2 and y_axis:
                    values.append(round(y_axis.value(float(ys.mean()) + top), dec))
                else:
                    values.append(None)
            series_out.append({"name": series_name(ci, comps), "values": values, "exact": [False] * len(values)})

    elif chart_type == "scatter":
        if x_axis is None:
            flags.append("x_axis_not_calibrated")
        for ci, (cl, comps) in enumerate(typed):
            pts = [c for c in comps if c in small_round]
            if not pts:
                continue
            med = float(np.median([c["area"] for c in pts]))
            pts = sorted([c for c in pts if 0.3 * med <= c["area"] <= 3 * med], key=lambda c: c["cx"])
            decx = _decimals(x_axis) if x_axis else 2
            series_out.append({
                "name": series_name(ci, comps),
                "x_values": [round(x_axis.value(c["cx"]), decx) if x_axis else None for c in pts],
                "values": [round(y_axis.value(c["cy"]), dec) if y_axis else None for c in pts],
                "exact": [False] * len(pts),
            })

    series_out = [s for s in series_out if any(v is not None for v in s["values"])]
    if not series_out:
        return None
    for i, s in enumerate(series_out):
        if not s["name"]:
            s["name"] = "Series 1" if len(series_out) == 1 else f"Series {i + 1}"

    all_exact = all(all(s["exact"]) for s in series_out) and labels_total > 0
    for s in series_out:
        s.pop("exact")
    missing = sum(v is None for s in series_out for v in s["values"])
    if missing:
        flags.append("some_values_unreadable")

    title = _title_of(words, top, W, hint_title)
    x_label = ""
    below_all = [w for w in words if w.y0 > bottom and w not in below and abs(w.cx - (left + right) / 2) < 0.3 * W]
    if below_all and not x_numeric_label_row(below_all):
        x_label = " ".join(w.text for w in sorted(below_all, key=lambda w: w.x0))
    y_label = _read_rotated_label(rgb, left_words, left, top, bottom)

    confidence = 0.85
    if "y_axis_not_calibrated" in flags or "x_axis_not_calibrated" in flags:
        confidence = 0.4
    if "y_axis_calibration_poor" in flags:
        confidence = min(confidence, 0.5)
    if missing:
        confidence = min(confidence, 0.6)
    if all_exact:
        confidence = min(0.95, confidence + 0.1)
    flags = sorted(set(flags))

    data = {
        "title": title,
        "chart_type": chart_type,
        "orientation": orientation,
        "categories": categories,
        "series": series_out,
        "x_label": x_label,
        "y_label": y_label,
        "extraction_method": "raster_chart_cv",
        "values_estimated": not all_exact,
        "calibration": {
            "y": None if not y_axis else {"labels": y_axis.n, "max_error": round(y_axis.max_err, 4), "span": round(y_axis.span, 4)},
            "x": None if not x_axis else {"labels": x_axis.n, "max_error": round(x_axis.max_err, 4), "span": round(x_axis.span, 4)},
            "data_labels_matched": f"{labels_used}/{labels_total}" if labels_total else None,
        },
    }
    return ChartReading(data=data, confidence=confidence, flags=flags)


def x_numeric_label_row(words: list[Word]) -> bool:
    return all(parse_number(w.text) is not None for w in words)


def _read_rotated_label(rgb: np.ndarray, left_words: list[Word], left: int, top: int, bottom: int) -> str:
    """The y-axis title is rotated 90 degrees; OCR the left strip rotated upright."""
    numeric = [w.x0 for w in left_words if parse_number(w.text) is not None]
    limit = int(min(numeric) - 3) if numeric else int(left - 0.08 * rgb.shape[1])
    if limit < 12:
        return ""
    strip = rgb[max(0, top - 10): bottom + 10, 0: limit]
    if strip.size == 0:
        return ""
    rotated = np.ascontiguousarray(np.rot90(strip, k=-1))  # clockwise
    from extractors.ocr_extractor import _get_ocr

    res, _ = _get_ocr()(rotated)
    return " ".join(str(t).strip() for _, t, _c in (res or []) if str(t).strip())


# ---------------------------------------------------------------------------
# Pie
# ---------------------------------------------------------------------------


def _read_pie(rgb: np.ndarray, words: list[Word], hint_title: str) -> ChartReading | None:
    H, W = rgb.shape[:2]
    sat = rgb.max(axis=2).astype(int) - rgb.min(axis=2).astype(int)
    colored = (sat >= 40) & (rgb.min(axis=2) < 235)
    if colored.sum() < 0.05 * H * W:
        return None
    n, labels, stats, _ = cv2.connectedComponentsWithStats(colored.astype(np.uint8), connectivity=8)
    # the disc may be split into slices by thin white borders: use the union's bbox
    big = [i for i in range(1, n) if stats[i][4] >= 0.01 * H * W]
    if not big:
        return None
    x0 = min(stats[i][0] for i in big)
    y0 = min(stats[i][1] for i in big)
    x1 = max(stats[i][0] + stats[i][2] for i in big)
    y1 = max(stats[i][1] + stats[i][3] for i in big)
    bw, bh = x1 - x0, y1 - y0
    if not (0.85 <= bw / max(1, bh) <= 1.18) or bw < 0.25 * min(H, W):
        return None
    cx, cy, r = (x0 + x1) / 2, (y0 + y1) / 2, min(bw, bh) / 2
    yy, xx = np.ogrid[:H, :W]
    disc = (xx - cx) ** 2 + (yy - cy) ** 2 <= (r * 0.98) ** 2
    if colored[disc].sum() < 0.8 * disc.sum():
        return None  # not a filled disc (e.g. a donut or a photo)

    clusters = _color_clusters(rgb, disc, int(disc.sum() * 0.01))
    if len(clusters) < 2:
        return None
    total = sum(c["pixels"] for c in clusters)
    flags: list[str] = []

    # printed percentages inside the disc -> exact values
    inside = []
    for w in words:
        v = parse_number(w.text)
        if v is not None and (w.cx - cx) ** 2 + (w.cy - cy) ** 2 < (r * 0.95) ** 2:
            inside.append((w, v))
    pct_by_cluster: dict[int, float] = {}
    for w, v in inside:
        votes = []
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            px = int(np.clip(w.cx + dx * (w.x1 - w.x0) * 0.75, 0, W - 1))
            py = int(np.clip(w.cy + dy * w.h * 0.9, 0, H - 1))
            col = rgb[py, px].astype(int)
            votes.append(int(np.argmin([np.linalg.norm(np.array(c["color"]) - col) for c in clusters])))
        pct_by_cluster[max(set(votes), key=votes.count)] = v

    shares = [100.0 * c["pixels"] / total for c in clusters]
    exact = len(pct_by_cluster) == len(clusters)
    values = [pct_by_cluster[i] if exact else round(shares[i], 1) for i in range(len(clusters))]
    if exact and abs(sum(values) - 100) > 1.5:
        flags.append("printed_percentages_do_not_sum_to_100")

    # category labels outside the disc, matched by angle
    outside = [w for w in words if parse_number(w.text) is None and
               (w.cx - cx) ** 2 + (w.cy - cy) ** 2 > (r * 0.9) ** 2 and w.cy > 0.1 * H]
    cats: list[str] = []
    taken: set[int] = set()
    for ci, cl in enumerate(clusters):
        ys, xs = np.nonzero(cl["mask"])
        ang = math.atan2(-(ys.mean() - cy), xs.mean() - cx) if xs.size else 0.0
        # mean direction (robust for slices > 180 degrees)
        a = np.arctan2(-(ys - cy), xs - cx)
        ang = math.atan2(np.sin(a).mean(), np.cos(a).mean())
        best, best_d = None, 1e9
        for wi, w in enumerate(outside):
            if wi in taken:
                continue
            wa = math.atan2(-(w.cy - cy), w.cx - cx)
            d = abs(math.atan2(math.sin(wa - ang), math.cos(wa - ang)))
            if d < best_d:
                best, best_d = wi, d
        if best is not None and best_d < 1.0:
            taken.add(best)
            cats.append(outside[best].text)
        else:
            cats.append("")
    if any(not c for c in cats):
        flags.append("some_slice_labels_unreadable")
        cats = [c or f"Slice {i + 1}" for i, c in enumerate(cats)]

    title = _title_of(words, cy - r, W, hint_title)
    confidence = 0.9 if exact else 0.65
    if flags:
        confidence = min(confidence, 0.6)
    data = {
        "title": title,
        "chart_type": "pie",
        "orientation": None,
        "categories": cats,
        "series": [{"name": "Share (%)", "values": values}],
        "x_label": "",
        "y_label": "",
        "extraction_method": "raster_chart_cv",
        "values_estimated": not exact,
        "calibration": {"y": None, "x": None, "data_labels_matched": f"{len(pct_by_cluster)}/{len(clusters)}"},
    }
    return ChartReading(data=data, confidence=confidence, flags=sorted(set(flags)))
