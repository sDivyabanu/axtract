"""Debt maturity wall (with receipts) and the deal timeline of dated events (all cited)."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from rag import db, diligence, index, planner
from rag.table_ops import PlanError, execute

_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_D1 = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(20\d{2})\b", re.I)
_D2 = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b", re.I)
_D3 = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](20\d{2})\b")
_EVENT_WORDS = re.compile(r"matur|expir|due|renew|notice|terminat|deadline|repay|payable|effective|commenc|closing|long[- ]stop|redemption", re.I)
_KINDS = [("maturity", re.compile(r"matur|redemption|repay", re.I)), ("expiry", re.compile(r"expir", re.I)),
          ("renewal", re.compile(r"renew", re.I)), ("notice", re.compile(r"notice|deadline", re.I)),
          ("termination", re.compile(r"terminat", re.I))]


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:  # "31 Feb 2026" and other impossible dates are ignored, never fatal
        return None


def _parse_dates(text: str) -> list[tuple[date, int, int]]:
    found: list[tuple[date | None, int, int]] = []
    for m in _D1.finditer(text):
        found.append((_mk(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1))), m.start(), m.end()))
    for m in _D2.finditer(text):
        found.append((_mk(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2))), m.start(), m.end()))
    for m in _D3.finditer(text):
        found.append((_mk(int(m.group(3)), int(m.group(2)), int(m.group(1))), m.start(), m.end()))
    return [(d, a, b) for d, a, b in found if d is not None]


def maturity_wall(workspace_id: str) -> list[dict[str, Any]]:
    """One wall per debt table: amount maturing per year, each bar backed by a receipt."""
    walls = []
    with db.connect() as c:
        rows = c.execute("SELECT table_id, doc_id, title FROM tables_store WHERE workspace_id=? AND kind='table'", (workspace_id,)).fetchall()
    for r in rows:
        tabs = planner.load_tables(workspace_id, [r["table_id"]])
        if not tabs:
            continue
        t = next(iter(tabs.values()))
        paths = t.grid["col_paths"]
        mat = next((p for i, p in enumerate(paths) if i and re.search(r"matur|due|expir|repay", p, re.I)), None)
        amt = next((p for i, p in enumerate(paths) if i and re.search(r"amount|outstanding|principal|balance", p, re.I)
                    and not re.search(r"matur|due|expir", p, re.I)), None)
        if not mat or not amt:
            continue
        mi = paths.index(mat)
        years = sorted({m.group(1) for x in t.grid["rows"] if not x["is_total"] for m in [re.search(r"(20\d{2})", str(x["cells"][mi]["raw"] or ""))] if m})
        bars = []
        for y in years:
            plan = {"title": f"Debt maturing in {y}", "steps": [{"id": "s1", "op": "aggregate", "fn": "sum", "table": "T1", "col": amt,
                                                                 "where": {"col": mat, "eq": y}}], "output": "s1"}
            try:
                res = execute(plan, {"T1": t})
            except PlanError:
                continue
            bars.append({"year": int(y), "value": res.receipt["result"], "display": res.receipt["result_display"], "receipt": res.receipt})
        if bars:
            walls.append({"table_id": r["table_id"], "title": r["title"] or t.filename, "filename": t.filename, "doc_id": r["doc_id"],
                          "unit": " ".join(x for x in (t.currency, t.unit) if x), "bars": bars})
    return walls


def timeline(workspace_id: str, limit: int = 60) -> list[dict[str, Any]]:
    """Dated events (maturities, expiries, renewals, notice periods, ...), each with its source."""
    events: dict[tuple, dict[str, Any]] = {}
    idx = index.get_index(workspace_id)
    with db.connect() as c:
        docs = {r["id"]: (r["filename"], r["preview_pages"]) for r in c.execute("SELECT id, filename, preview_pages FROM documents WHERE workspace_id=?", (workspace_id,))}
    for row in idx.rows.values():
        if row["kind"] == "section":
            for sent in re.split(r"(?<=[.!?])\s+", row["text"]):
                if not _EVENT_WORDS.search(sent):
                    continue
                for d, _a, _b in _parse_dates(sent):
                    kind = next((k for k, pat in _KINDS if pat.search(sent)), "other")
                    key = (d, re.sub(r"\W+", " ", sent.lower())[:60])
                    bb = next((b for b in row["bboxes"] if b.get("bbox")), {})
                    events.setdefault(key, {"date": d.isoformat(), "label": " ".join(sent.split())[:180], "kind": kind,
                                            "doc_id": row["doc_id"], "filename": docs.get(row["doc_id"], ("", 0))[0],
                                            "preview_pages": docs.get(row["doc_id"], ("", 0))[1], "page": row["pages"][0] if row["pages"] else None,
                                            "bbox": bb.get("bbox")})
    # dated maturity cells in debt tables
    with db.connect() as c:
        trows = c.execute("SELECT table_id, doc_id, grid_json, unit, currency, scale FROM tables_store WHERE workspace_id=? AND kind='table'", (workspace_id,)).fetchall()
    for r in trows:
        grid = db.jload(r["grid_json"], {})
        paths = grid.get("col_paths", [])
        mi = next((i for i, p in enumerate(paths) if i and re.search(r"matur|due|expir|repay", p, re.I)), None)
        ai = next((i for i, p in enumerate(paths) if i and re.search(r"amount|outstanding|principal|balance", p, re.I)), None)
        if mi is None:
            continue
        for x in grid["rows"]:
            if x["is_total"] or not x["label"]:
                continue
            cell = x["cells"][mi]
            for d, _a, _b in _parse_dates(str(cell["raw"] or "")):
                amount = ""
                if ai is not None and x["cells"][ai]["v"] is not None:
                    amount = " " + diligence.fmt_value(x["cells"][ai]["v"] * (r["scale"] or 1), r["scale"], r["currency"], r["unit"], x["cells"][ai]["raw"])
                events[(d, x["label"].lower())] = {
                    "date": d.isoformat(), "label": f"{x['label']}{amount} matures", "kind": "maturity", "doc_id": r["doc_id"],
                    "filename": docs.get(r["doc_id"], ("", 0))[0], "preview_pages": docs.get(r["doc_id"], ("", 0))[1],
                    "page": int(cell["page"] or 1), "bbox": cell.get("bbox")}
    # many facilities maturing the same day are one event, not fifty rows
    grouped: dict[tuple, list[dict]] = {}
    keep: list[dict] = []
    for e in events.values():
        if e["kind"] == "maturity" and e["label"].endswith(" matures"):
            grouped.setdefault((e["date"], e["doc_id"]), []).append(e)
        else:
            keep.append(e)
    for (d, doc), es in grouped.items():
        if len(es) == 1:
            keep.append(es[0])
            continue
        names = [e["label"].replace(" matures", "") for e in es]
        first = es[0]
        keep.append({**first, "label": f"{len(es)} facilities mature, e.g. {names[0]}; {names[1]}", "count": len(es), "members": names})
    out = sorted(keep, key=lambda e: (e["date"], -e.get("count", 1)))
    return out[:limit]
