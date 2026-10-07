"""Native Office charts (DrawingML chart parts) -> structured chart data.

DOCX stores them in word/charts/chartN.xml, PPTX in ppt/charts/chartN.xml. Each series
keeps its data cached in the XML (c:cat / c:val -> c:strCache / c:numCache), so values are
exact: no vision model and no estimation. Parsed with defusedxml.
"""

from __future__ import annotations

from typing import Any
from xml.etree.ElementTree import Element

from defusedxml.ElementTree import fromstring

C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

_TYPES = {
    "barChart": "bar", "bar3DChart": "bar", "lineChart": "line", "line3DChart": "line",
    "pieChart": "pie", "pie3DChart": "pie", "ofPieChart": "pie", "doughnutChart": "doughnut",
    "areaChart": "area", "area3DChart": "area", "scatterChart": "scatter", "bubbleChart": "bubble",
    "radarChart": "radar", "stockChart": "stock", "surfaceChart": "surface", "surface3DChart": "surface",
}


def _text(el: Element | None) -> str:
    if el is None:
        return ""
    return "".join(t.text or "" for t in el.iter(f"{A}t")).strip() or "".join(
        v.text or "" for v in el.iter(f"{C}v")
    ).strip()


def _cache_points(parent: Element | None) -> list[Any]:
    """Read a c:strRef/c:numRef/c:strLit/c:numLit (cache) into an ordered list."""
    if parent is None:
        return []
    cache_tags = {f"{C}strCache", f"{C}numCache", f"{C}multiLvlStrCache", f"{C}strLit", f"{C}numLit"}
    cache = None
    for ref in parent:
        if ref.tag in cache_tags:  # literal data directly under c:cat / c:val
            cache = ref
            break
        for child in ref:  # c:strRef / c:numRef / c:multiLvlStrRef -> cache
            if child.tag in cache_tags:
                cache = child
                break
        if cache is not None:
            break
    if cache is None:
        return []

    count_el = cache.find(f"{C}ptCount")
    count = int(count_el.get("val", "0")) if count_el is not None else 0
    pts: dict[int, str] = {}
    # multi-level categories: use the innermost level
    levels = cache.findall(f"{C}lvl")
    source = levels[0] if levels else cache
    for pt in source.findall(f"{C}pt"):
        v = pt.find(f"{C}v")
        pts[int(pt.get("idx", "0"))] = v.text if v is not None and v.text is not None else ""
    n = max(count, (max(pts) + 1) if pts else 0)
    return [pts.get(i) for i in range(n)]


def _numbers(raw: list[Any]) -> list[float | None]:
    out: list[float | None] = []
    for v in raw:
        try:
            out.append(float(v) if v not in (None, "") else None)
        except (TypeError, ValueError):
            out.append(None)
    return out


def _series_name(ser: Element, index: int) -> str:
    tx = ser.find(f"{C}tx")
    name = ""
    if tx is not None:
        pts = _cache_points(tx)
        name = (pts[0] if pts else "") or _text(tx)
    return name or f"Series {index + 1}"


def parse_chart_xml(xml: bytes | str) -> dict[str, Any] | None:
    """Parse one chart part. Returns the chart dict, or None when it has no plottable series."""
    root = fromstring(xml)
    chart = root.find(f"{C}chart")
    if chart is None:
        return None
    plot = chart.find(f"{C}plotArea")
    if plot is None:
        return None

    title = _text(chart.find(f"{C}title")) if chart.find(f"{C}title") is not None else ""

    series_out: list[dict[str, Any]] = []
    categories: list[str] = []
    chart_types: list[str] = []
    grouping = None
    bar_dir = None

    for el in plot:
        tag = el.tag.replace(C, "")
        if tag not in _TYPES:
            continue
        ctype = _TYPES[tag]
        if tag.startswith("bar"):
            bd = el.find(f"{C}barDir")
            bar_dir = bd.get("val") if bd is not None else "col"
            ctype = "column" if bar_dir == "col" else "bar"
        g = el.find(f"{C}grouping")
        if g is not None:
            grouping = g.get("val")
        chart_types.append(ctype)

        for i, ser in enumerate(el.findall(f"{C}ser")):
            name = _series_name(ser, len(series_out))
            if tag == "scatterChart" or tag == "bubbleChart":
                xs = _numbers(_cache_points(ser.find(f"{C}xVal")))
                ys = _numbers(_cache_points(ser.find(f"{C}yVal")))
                entry: dict[str, Any] = {"name": name, "type": ctype, "values": ys, "x_values": xs}
                if tag == "bubbleChart":
                    entry["sizes"] = _numbers(_cache_points(ser.find(f"{C}bubbleSize")))
            else:
                cats = _cache_points(ser.find(f"{C}cat"))
                vals = _numbers(_cache_points(ser.find(f"{C}val")))
                if cats and len(cats) >= len(categories):
                    categories = [c if c is not None else "" for c in cats]
                entry = {"name": name, "type": ctype, "values": vals}
            if entry["values"]:
                series_out.append(entry)

    if not series_out:
        return None

    def axis_title(tag: str) -> str:
        ax = plot.find(f"{C}{tag}")
        return _text(ax.find(f"{C}title")) if ax is not None and ax.find(f"{C}title") is not None else ""

    primary = chart_types[0] if chart_types else "unknown"
    return {
        "title": title,
        "chart_type": "bar" if primary in ("bar", "column") else primary,
        "orientation": ("horizontal" if bar_dir == "bar" else "vertical") if bar_dir else None,
        "grouping": grouping,
        "combo_types": sorted(set(chart_types)) if len(set(chart_types)) > 1 else None,
        "categories": categories,
        "series": series_out,
        "x_label": axis_title("catAx") or axis_title("dateAx"),
        "y_label": axis_title("valAx"),
        "extraction_method": "office_chart_xml",
        "values_estimated": False,
    }


def chart_summary(data: dict[str, Any]) -> str:
    """One-line human summary used as the block's text content."""
    title = data.get("title") or "Chart"
    cats = data.get("categories") or []
    parts = []
    for s in data["series"][:4]:
        vals = s["values"]
        if s.get("x_values"):
            pairs = [f"({x:g}, {y:g})" for x, y in zip(s["x_values"], vals) if x is not None and y is not None]
        else:
            pairs = [
                f"{cats[i] if i < len(cats) else i + 1}={v:g}" for i, v in enumerate(vals) if v is not None
            ]
        parts.append(f"{s['name']}: " + ", ".join(pairs[:12]) + (" …" if len(pairs) > 12 else ""))
    return f"{title} ({data['chart_type']}) — " + "; ".join(parts)
