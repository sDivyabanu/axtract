"""Normalised financial facts (concept, period, value in base units) with provenance.

Facts are read from the typed table store (and from a few unambiguous sentences) so that numbers
from different documents can be compared on the same footing: same concept, same period, same
unit/scale. Every fact keeps the document, page, cell box and confidence it came from.
"""

from __future__ import annotations

import re
from typing import Any

from rag import db, meta as M

# (concept, label pattern). Order matters: the first match wins.
CONCEPTS: list[tuple[str, re.Pattern]] = [
    ("net_debt", re.compile(r"^\s*net\s+debt\b", re.I)),
    ("ebitda", re.compile(r"\bebitda\b", re.I)),
    ("revenue", re.compile(r"^\s*(?:total\s+)?(?:net\s+)?(?:revenue(?:s)?(?:\s+from\s+operations)?|sales|net\s+sales|turnover)\b", re.I)),
    ("pat", re.compile(r"^\s*(?:profit\s+after\s+tax|net\s+(?:profit|income)|pat)\b", re.I)),
    ("gross_profit", re.compile(r"^\s*gross\s+profit\b", re.I)),
    ("cash", re.compile(r"^\s*cash(?:\s+and\s+(?:cash\s+)?equivalents)?\b", re.I)),
    ("total_debt", re.compile(r"^\s*(?:total\s+)?(?:debt|borrowings?|indebtedness)\b", re.I)),
]
CONCEPT_LABEL = {
    "revenue": "Revenue", "ebitda": "EBITDA", "net_debt": "Net debt", "total_debt": "Total debt", "cash": "Cash",
    "pat": "Profit after tax", "gross_profit": "Gross profit",
}


def concept_of(label: str) -> str | None:
    for name, pat in CONCEPTS:
        if pat.search(label or ""):
            return name
    return None


def _decimals(raw: str | None) -> int:
    m = re.search(r"\.(\d+)", raw or "")
    return len(m.group(1)) if m else 0


def facts_from_table(t, doc_type: str) -> list[dict[str, Any]]:
    """Facts from a TableOut (rows labelled with a known concept, columns with a period)."""
    out: list[dict[str, Any]] = []
    grid = t.grid
    paths, periods = grid["col_paths"], grid["col_periods"]
    chart = t.kind == "chart"
    for r in grid["rows"]:
        label = r["label"] or ""
        row_period = M.normalize_period(label) if chart else None
        for ci, cell in enumerate(r["cells"]):
            if ci == 0 or cell["v"] is None or cell.get("pct"):
                continue
            concept = concept_of(label)
            if chart:  # chart rows are periods; the series name says what is measured
                concept = concept_of(paths[ci]) or concept_of(t.title)
            if concept is None and r["is_total"] and (t.statement == "debt_schedule" or doc_type == "debt_schedule"):
                concept = "total_debt"  # the Total row of a debt schedule
            if concept is None:
                continue
            period = row_period if chart else (periods[ci] or getattr(t, "period", None))
            if not period:
                continue
            out.append({
                "concept": concept, "value": float(cell["v"]) * (t.scale or 1.0), "unit": t.unit, "scale": t.scale,
                "currency": t.currency, "period": period, "block_id": t.table_ref, "page": int(cell["page"] or t.page),
                "bbox": cell.get("bbox"), "confidence": cell.get("conf"), "label": label, "raw": cell.get("raw"),
            })
    return out


_SENT = re.compile(
    r"(?P<label>total\s+borrowings|total\s+debt|net\s+debt|revenue(?:\s+from\s+operations)?|ebitda|net\s+profit)\b[^.\d]{0,50}?"
    r"(?:stood\s+at|was|were|is|of|amounted\s+to)\s+(?:Rs\.?|₹|INR|USD|\$)?\s*(?P<num>\d[\d,]*\.?\d*)\s*(?P<unit>crore|lakh|million|billion)?",
    re.I)


def facts_from_text(text: str, page: int, bbox, block_id: str, context: str = "") -> list[dict[str, Any]]:
    out = []
    for m in _SENT.finditer(text):
        concept = concept_of(m.group("label"))
        unit = (m.group("unit") or "").lower() or None
        period = M.normalize_period(text) or M.normalize_period(context)
        if not concept or not period:
            continue
        label_u, scale, currency = M.detect_unit(text[m.start(): m.end() + 12])
        scale = scale if unit else None
        v = float(m.group("num").replace(",", ""))
        out.append({"concept": concept, "value": v * (scale or 1.0), "unit": unit, "scale": scale, "currency": currency,
                    "period": period, "block_id": block_id, "page": page, "bbox": bbox, "confidence": None,
                    "label": m.group("label"), "raw": m.group("num")})
    return out


def store(workspace_id: str, doc_id: str, facts: list[dict[str, Any]]) -> None:
    seen = set()
    with db.connect() as c:
        c.execute("DELETE FROM facts WHERE doc_id=?", (doc_id,))
        for f in facts:
            key = (f["concept"], f["period"], round(f["value"], 6), f["page"])
            if key in seen:
                continue
            seen.add(key)
            c.execute(
                "INSERT INTO facts (workspace_id, doc_id, concept, value, unit, scale, currency, period, block_id, page, bbox_json,"
                " confidence, label, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (workspace_id, doc_id, f["concept"], f["value"], f["unit"], f["scale"], f["currency"], f["period"], f["block_id"],
                 f["page"], db.jdump(f["bbox"]), f["confidence"], f["label"], f["raw"]))


def all_facts(workspace_id: str) -> list[dict[str, Any]]:
    with db.connect() as c:
        docs = {r["id"]: (r["filename"], r["preview_pages"]) for r in c.execute("SELECT id, filename, preview_pages FROM documents WHERE workspace_id=?", (workspace_id,))}
        rows = c.execute("SELECT * FROM facts WHERE workspace_id=?", (workspace_id,)).fetchall()
    return [{
        "concept": r["concept"], "value": r["value"], "unit": r["unit"], "scale": r["scale"], "currency": r["currency"],
        "period": r["period"], "doc_id": r["doc_id"], "filename": docs.get(r["doc_id"], ("", 0))[0],
        "preview_pages": docs.get(r["doc_id"], ("", 0))[1], "page": r["page"], "bbox": db.jload(r["bbox_json"]),
        "confidence": r["confidence"], "label": r["label"], "raw": r["raw"], "block_id": r["block_id"],
    } for r in rows]
