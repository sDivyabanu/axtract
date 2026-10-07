"""Typed, addressable table grids (the numeric source of truth for exact answers).

A parsed table block becomes: header rows resolved to column paths (merged headers spread over
the columns they span), one row label per data row, and per-cell raw text, numeric value, page
and bounding box. Computation always runs on these typed values, never on LLM output.
"""

from __future__ import annotations

import re
from typing import Any

from models.document import DocumentBlock
from rag import meta as M
from services.table_service import parse_financial_number

_TOTAL = re.compile(r"^\s*(?:grand\s+)?total\b|^\s*net\s+(?:debt|income|profit|loss)\b", re.I)


def _s(v: Any) -> str:
    return "" if v is None else " ".join(str(v).split())


def build_grid(block: DocumentBlock) -> dict[str, Any] | None:
    """Structured grid for a table block, or None if it has no usable rows."""
    rows: list[list[Any]] = block.metadata.get("rows") or []
    if not rows:
        return None
    n_cols = max(len(r) for r in rows)
    hr = int((block.metadata.get("multi_row_header") or {}).get("header_row_count", 1))
    hr = max(1, min(hr, len(rows) - 1)) if len(rows) > 1 else 1
    merged = (block.metadata.get("merged_cells") or {}).get("merged_regions") or []

    # spread merged header labels over the columns they cover
    header_text: list[list[str]] = [[_s(c) for c in rows[r]] + [""] * (n_cols - len(rows[r])) for r in range(hr)]
    for r, c, rs, cs in merged:
        if r < hr:
            label = _s(rows[r][c]) if c < len(rows[r]) else ""
            for cc in range(c, min(n_cols, c + cs)):
                for rr in range(r, min(hr, r + rs)):
                    header_text[rr][cc] = label
    col_paths: list[str] = []
    col_periods: list[str | None] = []
    for c in range(n_cols):
        parts: list[str] = []
        for r in range(hr):
            t = header_text[r][c]
            if t and t not in parts:
                parts.append(t)
        path = " / ".join(parts)
        col_paths.append(path)
        col_periods.append(M.normalize_period(path) if re.search(r"(?:FY|Q[1-4]|20\d\d|19\d\d)", path, re.I) else None)

    row_pages = block.metadata.get("row_pages") or []
    cell_boxes = block.metadata.get("cell_bboxes") or []
    table_bbox = list(block.bbox) if block.bbox else None
    prev = block.metadata.get("preview") or {}

    out_rows: list[dict[str, Any]] = []
    last_label = ""
    for ridx in range(hr, len(rows)):
        raw_row = rows[ridx] + [None] * (n_cols - len(rows[ridx]))
        page = (row_pages[ridx] if ridx < len(row_pages) else None) or prev.get("page") or block.page
        label = ""
        for c in range(min(2, n_cols)):  # the label is in the first (or second) column
            t = _s(raw_row[c])
            if t and isinstance(parse_financial_number(t), str):
                label = t
                break
        if not label:
            label = last_label  # rowspan continuation
        else:
            last_label = label
        cells = []
        for c in range(n_cols):
            raw = raw_row[c]
            text = _s(raw)
            parsed = parse_financial_number(text) if text else text
            v = float(parsed) if isinstance(parsed, float) else None
            box = None
            if ridx < len(cell_boxes) and cell_boxes[ridx] and c < len(cell_boxes[ridx]):
                box = cell_boxes[ridx][c]
            cells.append({
                "raw": text if raw is not None else None,
                "v": v,
                "pct": text.endswith("%") if text else False,
                "page": page,
                "bbox": box or (prev.get("bbox") if prev.get("bbox") else table_bbox),
                "exact_cell": bool(box),
                "conf": block.confidence,
            })
        out_rows.append({"ridx": ridx, "label": label, "is_total": bool(_TOTAL.match(label)), "cells": cells})

    return {
        "header_rows": hr,
        "col_paths": col_paths,
        "col_periods": col_periods,
        "rows": out_rows,
        "merged": [list(m) for m in merged],
        "n_cols": n_cols,
    }


def table_summary(grid: dict[str, Any], title: str, unit_label: str | None, currency: str | None, page: int) -> str:
    """Deterministic natural-language description used for retrieval (no LLM involved)."""
    cols = [c for c in grid["col_paths"] if c][:12]
    labels = [r["label"] for r in grid["rows"] if r["label"]]
    periods = [p for p in dict.fromkeys(p for p in grid["col_periods"] if p)]
    bits = [f"Table on page {page}" + (f" ({title})" if title else "")]
    if cols:
        bits.append("columns: " + "; ".join(cols))
    if labels:
        shown = labels[:14]
        bits.append("rows: " + "; ".join(shown) + (f" and {len(labels) - len(shown)} more" if len(labels) > len(shown) else ""))
    if periods:
        bits.append("periods: " + ", ".join(periods))
    if unit_label or currency:
        bits.append("amounts in " + " ".join(x for x in (currency, unit_label) if x))
    return ". ".join(bits) + "."


def cell_value_with_unit(cell: dict[str, Any], scale: float | None) -> float | None:
    """Numeric value converted to base units (scale carried through), None if not numeric."""
    if cell["v"] is None:
        return None
    return cell["v"] * (scale or 1.0) if not cell.get("pct") else cell["v"]
