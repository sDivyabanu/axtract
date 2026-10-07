"""Read vector-drawn charts from a PDF page with pdfplumber (MIT).

Charts exported to PDF by matplotlib, Excel, PowerPoint, LibreOffice ... are drawn with
paths, not images. The primitives carry exact geometry:
  * the plot frame (a stroked rect, or two long axis lines),
  * tick marks (short lines at the frame edge) + tick-label text,
  * bars (filled rects), line series (stroked open paths), markers (small filled paths).
Pixel positions are mapped to values through the numeric tick labels, so values are exact
up to float precision (values_estimated = False). Pies and anything else that cannot be
calibrated are left to the raster reader.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from services.vision.chart_raster import Axis, fit_axis, parse_number

_MIN_FRAME_W, _MIN_FRAME_H = 80.0, 50.0
_TICK_MAX = 9.0


def _color_key(color) -> tuple | None:
    """Hashable, comparable colour or None for white/black/grey/unset."""
    if color is None:
        return None
    if isinstance(color, (int, float)):
        return None  # greyscale
    c = tuple(round(float(v), 2) for v in color)
    if len(c) == 3:
        r, g, b = c
        if max(c) - min(c) < 0.12:  # grey / white / black
            return None
        return c
    if len(c) == 4:  # CMYK
        return c if max(c[:3]) > 0.15 else None
    return None


def _words(page) -> list[dict]:
    return page.extract_words(keep_blank_chars=False, use_text_flow=False, extra_attrs=["size"])


def _find_frames(page) -> list[tuple[float, float, float, float]]:
    """Plot frames as (x0, top, x1, bottom) in points."""
    frames = []
    for r in page.rects:
        w, h = r["x1"] - r["x0"], r["bottom"] - r["top"]
        if w < _MIN_FRAME_W or h < _MIN_FRAME_H:
            continue
        if w > page.width * 0.98 and h > page.height * 0.98:
            continue  # page background
        frames.append((r["x0"], r["top"], r["x1"], r["bottom"]))
    if frames:
        return frames
    # L-shaped axes (no frame rect): longest horizontal line + vertical line at its left end
    hor = [l for l in page.lines if abs(l["top"] - l["bottom"]) < 0.8 and l["x1"] - l["x0"] > _MIN_FRAME_W]
    ver = [l for l in page.lines if abs(l["x0"] - l["x1"]) < 0.8 and l["bottom"] - l["top"] > _MIN_FRAME_H]
    for h in sorted(hor, key=lambda l: -(l["x1"] - l["x0"])):
        for v in ver:
            if abs(v["x0"] - h["x0"]) < 3 and abs(v["bottom"] - h["top"]) < 3:
                frames.append((v["x0"], v["top"], h["x1"], h["top"]))
    return frames


def _ticks(page, frame) -> tuple[list[float], list[float]]:
    """(left-edge tick y positions, bottom-edge tick x positions)."""
    x0, top, x1, bottom = frame
    ys, xs = [], []
    for l in page.lines:
        length = max(abs(l["x1"] - l["x0"]), abs(l["bottom"] - l["top"]))
        if length > _TICK_MAX or length < 1:
            continue
        horizontal = abs(l["top"] - l["bottom"]) < 0.6
        if horizontal and abs(l["x1"] - x0) < 2.5 and top - 2 <= l["top"] <= bottom + 2:
            ys.append(l["top"])
        elif not horizontal and abs(l["top"] - bottom) < 2.5 and x0 - 2 <= l["x0"] <= x1 + 2:
            xs.append(l["x0"])
    return sorted(ys), sorted(xs)


def _nearest(v: float, options: list[float], tol: float = 6.0) -> float | None:
    if not options:
        return None
    best = min(options, key=lambda o: abs(o - v))
    return best if abs(best - v) <= tol else None


def read_vector_charts(page) -> list[dict[str, Any]]:
    """Return charts found on a pdfplumber page.

    Each item: {"bbox_pt": (x0, top, x1, bottom), "data": chart dict, "confidence": float,
                "flags": [...], "absorbed": (x0, top, x1, bottom)} with points, top-left origin.
    """
    out: list[dict[str, Any]] = []
    words = _words(page)
    for frame in _find_frames(page):
        x0, top, x1, bottom = frame
        y_ticks, x_ticks = _ticks(page, frame)
        if len(y_ticks) < 2 and len(x_ticks) < 2:
            continue  # a table cell, a box ... not an axis chart

        left_words = [w for w in words if w["x1"] <= x0 + 2 and top - 8 <= (w["top"] + w["bottom"]) / 2 <= bottom + 8
                      and w["x0"] > x0 - 70]
        below = [w for w in words if w["top"] >= bottom - 1 and (w["top"] + w["bottom"]) / 2 <= bottom + 26
                 and x0 - 20 <= (w["x0"] + w["x1"]) / 2 <= x1 + 20]

        y_pairs = []
        for w in left_words:
            v = parse_number(w["text"])
            t = _nearest((w["top"] + w["bottom"]) / 2, y_ticks) if v is not None else None
            if v is not None and t is not None:
                y_pairs.append((t, v))
        x_numeric = [(w, parse_number(w["text"])) for w in below]
        x_is_numeric = len(below) >= 2 and all(v is not None for _, v in x_numeric)
        x_pairs = []
        if x_is_numeric:
            for w, v in x_numeric:
                t = _nearest((w["x0"] + w["x1"]) / 2, x_ticks)
                if t is not None:
                    x_pairs.append((t, v))
        y_axis: Axis | None = fit_axis(y_pairs)
        x_axis: Axis | None = fit_axis(x_pairs) if x_is_numeric else None
        if y_axis is None and x_axis is None:
            continue
        cat_labels = [] if x_is_numeric else sorted(below, key=lambda w: w["x0"])

        # ---- primitives inside the frame ----
        def inside(o, pad=1.5):
            return o["x0"] >= x0 - pad and o["x1"] <= x1 + pad and o["top"] >= top - pad and o["bottom"] <= bottom + pad

        bars_by_color: dict[tuple, list[dict]] = defaultdict(list)
        for r in page.rects:
            ck = _color_key(r.get("non_stroking_color")) if r.get("fill") else None
            if ck and inside(r) and (r["x1"] - r["x0"]) < (x1 - x0) * 0.98:
                bars_by_color[ck].append(r)
        lines_by_color: dict[tuple, list[dict]] = defaultdict(list)
        markers_by_color: dict[tuple, list[dict]] = defaultdict(list)
        for c in page.curves:
            if not inside(c, 6):
                continue
            w, h = c["x1"] - c["x0"], c["bottom"] - c["top"]
            stroke = _color_key(c.get("stroking_color"))
            fillc = _color_key(c.get("non_stroking_color")) if c.get("fill") else None
            if c.get("fill") and fillc is None and w < 14 and h < 14:
                markers_by_color[(0.0, 0.0, 0.0)].append(c)  # black/grey markers
            elif c.get("fill") and fillc and w < 14 and h < 14:
                markers_by_color[fillc].append(c)
            elif not c.get("fill") and stroke and (w > 12 or h > 12):
                lines_by_color[stroke].append(c)
        for l in page.lines:
            stroke = _color_key(l.get("stroking_color"))
            if stroke and inside(l, 3) and max(l["x1"] - l["x0"], l["bottom"] - l["top"]) > 12:
                lines_by_color[stroke].append({"pts": [(l["x0"], l["top"]), (l["x1"], l["bottom"])], **l})

        flags: list[str] = []
        series: list[dict[str, Any]] = []
        categories: list[str] = []
        chart_type = None

        if bars_by_color and y_axis:
            chart_type = "bar"
            cats_x = [((w["x0"] + w["x1"]) / 2, w["text"]) for w in cat_labels]
            categories = [t for _, t in cats_x]
            baseline = y_axis.pixel(0.0) if top <= y_axis.pixel(0.0) <= bottom + 1 else bottom
            for color, rects in bars_by_color.items():
                vals: dict[int, float] = {}
                for r in sorted(rects, key=lambda r: r["x0"]):
                    k = int(np.argmin([abs((r["x0"] + r["x1"]) / 2 - cx) for cx, _ in cats_x])) if cats_x else len(vals)
                    edge = r["top"] if abs(r["bottom"] - baseline) < 1.5 else r["bottom"]
                    vals[k] = round(y_axis.value(edge), 4)
                n = len(cats_x) if cats_x else len(vals)
                series.append({"name": "", "values": [vals.get(i) for i in range(n)]})
        elif lines_by_color and (cat_labels or x_axis) and y_axis:
            chart_type = "line"
            if cat_labels:
                cats_x = [((w["x0"] + w["x1"]) / 2, w["text"]) for w in cat_labels]
            else:
                cats_x = [(p, f"{v:g}") for p, v in x_pairs]
            categories = [t for _, t in cats_x]
            for color, curves in lines_by_color.items():
                pts = [p for c in curves for p in c["pts"]]
                vals = []
                for cx, _t in cats_x:
                    near = [p for p in pts if abs(p[0] - cx) < 1.2]
                    vals.append(round(y_axis.value(float(np.mean([p[1] for p in near]))), 4) if near else None)
                series.append({"name": "", "values": vals})
        elif markers_by_color and x_axis and y_axis:
            chart_type = "scatter"
            for color, ms in markers_by_color.items():
                ms = sorted(ms, key=lambda m: m["x0"])
                series.append({
                    "name": "",
                    "x_values": [round(x_axis.value((m["x0"] + m["x1"]) / 2), 4) for m in ms],
                    "values": [round(y_axis.value((m["top"] + m["bottom"]) / 2), 4) for m in ms],
                })
        if not series or chart_type is None:
            continue

        for i, s in enumerate(series):
            s["name"] = "Series 1" if len(series) == 1 else f"Series {i + 1}"

        # title: the line of words just above the frame
        above = [w for w in words if w["bottom"] <= top + 1 and top - w["bottom"] < 40 and x0 - 20 <= (w["x0"] + w["x1"]) / 2 <= x1 + 20]
        title = ""
        if above:
            row_bottom = max(w["bottom"] for w in above)
            title = " ".join(w["text"] for w in sorted([w for w in above if row_bottom - w["bottom"] < 4], key=lambda w: w["x0"]))
        below_all = [w for w in words if w["top"] > bottom + 12 and w["top"] < bottom + 48 and x0 <= (w["x0"] + w["x1"]) / 2 <= x1]
        x_label = " ".join(w["text"] for w in sorted(below_all, key=lambda w: w["x0"]))
        if x_label and all(parse_number(t) is not None for t in x_label.split()):
            x_label = ""
        ylab_chars = [c for c in page.chars if c["x1"] < x0 - 20 and top <= (c["top"] + c["bottom"]) / 2 <= bottom and not c.get("upright", True)]
        y_label = "".join(c["text"] for c in sorted(ylab_chars, key=lambda c: -c["top"])) if ylab_chars else ""

        pad = 4.0
        region = (min(x0, *(w["x0"] for w in left_words + below)) - pad if (left_words or below) else x0 - pad,
                  (min(w["top"] for w in above) if above else top) - pad,
                  x1 + pad,
                  (max([w["bottom"] for w in below_all + below] or [bottom])) + pad)
        max_err = max([a.max_err / max(a.span, 1e-9) for a in (y_axis, x_axis) if a] or [0])
        confidence = 0.92 if max_err < 0.005 else 0.7
        if max_err >= 0.005:
            flags.append("axis_calibration_poor")
        out.append({
            "bbox_pt": (float(region[0]), float(region[1]), float(region[2]), float(region[3])),
            "data": {
                "title": title,
                "chart_type": chart_type,
                "orientation": "vertical",
                "categories": categories,
                "series": series,
                "x_label": x_label,
                "y_label": y_label,
                "extraction_method": "pdf_vector_chart",
                "values_estimated": False,
                "calibration": {
                    "y": {"labels": y_axis.n, "max_error": round(y_axis.max_err, 6), "span": round(y_axis.span, 6)} if y_axis else None,
                    "x": {"labels": x_axis.n, "max_error": round(x_axis.max_err, 6), "span": round(x_axis.span, 6)} if x_axis else None,
                },
            },
            "confidence": confidence,
            "flags": flags,
        })
    return out
