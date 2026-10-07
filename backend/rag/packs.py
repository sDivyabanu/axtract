"""Diligence packs: one click, a matrix of cited answers (rows = questions, columns = documents)."""

from __future__ import annotations

import re
import time
from typing import Any

from rag import db, diligence, facts as F, qa, workspaces

PACKS: dict[str, dict[str, Any]] = {
    "financials": {
        "title": "Financials", "doc_types": ["financial_statement", "cim", "spreadsheet", "presentation"],
        "rows": [("Revenue", "fact:revenue"), ("EBITDA", "fact:ebitda"), ("Net debt", "fact:net_debt"), ("Total debt", "fact:total_debt"),
                 ("Cash", "fact:cash"), ("Profit after tax", "fact:pat"), ("Revenue growth (latest vs prior)", "growth:revenue")],
    },
    "contracts": {
        "title": "Contracts", "doc_types": ["contract"],
        "rows": [("Parties", "Who is the Borrower and who is the Lender under the agreement?"), ("Term / maturity", "What is the Maturity Date or term?"),
                 ("Termination", "On what grounds can the lender terminate the facilities?"),
                 ("Change of control", "What happens upon a Change of Control?"), ("Governing law", "Which law governs the agreement?"),
                 ("Assignment", "May either party assign or transfer its rights under the agreement?")],
    },
    "debt": {
        "title": "Debt", "doc_types": ["debt_schedule", "contract"],
        "rows": [("Facilities and amounts", "debt:facilities"), ("Maturities", "debt:maturities"), ("Interest rates", "debt:rates"),
                 ("Financial covenants", "Are there financial covenants or maintenance tests?")],
    },
}


def _period_key(p: str) -> tuple:
    m = re.search(r"(\d{4})", p or "")
    q = re.match(r"Q(\d)", p or "")
    return (int(m.group(1)) if m else 0, int(q.group(1)) if q else 5)


def _cite(f: dict[str, Any]) -> dict[str, Any]:
    pm = diligence._printed_map(f["doc_id"])
    return {"doc_id": f["doc_id"], "filename": f["filename"], "page": f["page"], "printed_page": pm.get(f["page"]),
            "bbox": f["bbox"], "preview_pages": f["preview_pages"]}


def _fact_cell(facts: list[dict], doc_id: str, concept: str) -> dict[str, Any]:
    mine = [f for f in facts if f["doc_id"] == doc_id and f["concept"] == concept]
    if not mine:
        return {"status": "not_found", "text": "Not found"}
    f = max(mine, key=lambda x: _period_key(x["period"]))
    badges = [{"label": f"confidence {f['confidence']:.0%}", "level": "amber"}] if f["confidence"] is not None and f["confidence"] < 0.85 else []
    return {"status": "found", "text": f"{diligence.fmt_value(f['value'], f['scale'], f['currency'], f['unit'], f['raw'])} ({f['period']})",
            "citations": [_cite(f)], "badges": badges}


def _growth_cell(facts: list[dict], doc_id: str, concept: str) -> dict[str, Any]:
    mine = sorted({(f["period"], f["value"]): f for f in facts if f["doc_id"] == doc_id and f["concept"] == concept}.values(),
                  key=lambda x: _period_key(x["period"]))
    if len(mine) < 2 or mine[-2]["value"] == 0:
        return {"status": "not_found", "text": "Not found (needs two periods)"}
    a, b = mine[-2], mine[-1]
    return {"status": "found", "text": f"{(b['value'] / a['value'] - 1) * 100:+.1f}% ({a['period']}→{b['period']})",
            "citations": [_cite(a), _cite(b)], "badges": []}


def _debt_cell(workspace_id: str, doc_id: str, which: str) -> dict[str, Any]:
    with db.connect() as c:
        rows = c.execute("SELECT * FROM tables_store WHERE workspace_id=? AND doc_id=? AND kind='table'", (workspace_id, doc_id)).fetchall()
    for r in rows:
        grid = db.jload(r["grid_json"], {})
        paths = grid.get("col_paths", [])
        mat = next((i for i, p in enumerate(paths) if i and re.search(r"matur|due|expir|repay", p, re.I)), None)
        amt = next((i for i, p in enumerate(paths) if i and re.search(r"amount|outstanding|principal|balance", p, re.I)), None)
        rate = next((i for i, p in enumerate(paths) if i and re.search(r"rate|interest|coupon", p, re.I)), None)
        if mat is None or amt is None:
            continue
        data = [x for x in grid["rows"] if not x["is_total"] and x["label"] and x["cells"][amt]["v"] is not None]
        if not data:
            continue
        first = data[0]["cells"][amt]
        cite = {"doc_id": doc_id, "filename": "", "page": first["page"], "printed_page": None, "bbox": first.get("bbox"), "preview_pages": 0}
        with db.connect() as c:
            d = c.execute("SELECT filename, preview_pages FROM documents WHERE id=?", (doc_id,)).fetchone()
        cite.update(filename=d["filename"], preview_pages=d["preview_pages"], printed_page=diligence._printed_map(doc_id).get(first["page"]))
        unit = " ".join(x for x in (r["currency"], r["unit"]) if x)
        if which == "facilities":
            total = sum(x["cells"][amt]["v"] for x in data)
            return {"status": "found", "text": f"{len(data)} facilities, total {diligence.fmt_value(total * (r['scale'] or 1), r['scale'], r['currency'], r['unit'], '0.0')}",
                    "citations": [cite], "badges": []}
        if which == "maturities":
            by_year: dict[str, float] = {}
            for x in data:
                m = re.search(r"(20\d{2})", str(x["cells"][mat]["raw"] or ""))
                if m:
                    by_year[m.group(1)] = by_year.get(m.group(1), 0) + x["cells"][amt]["v"]
            if by_year:
                ys = sorted(by_year)
                return {"status": "found", "text": f"{ys[0]}–{ys[-1]}; largest year {max(by_year, key=by_year.get)}", "citations": [cite], "badges": []}
        if which == "rates" and rate is not None:
            vals = []
            for x in data:
                m = re.search(r"(\d+\.?\d*)\s*%", str(x["cells"][rate]["raw"] or ""))
                if m:
                    vals.append(float(m.group(1)))
            if vals:
                return {"status": "found", "text": f"{min(vals):.2f}% – {max(vals):.2f}% (across {len(vals)} facilities)", "citations": [cite], "badges": []}
    return {"status": "not_found", "text": "Not found"}


def _qa_cell(workspace_id: str, doc_id: str, question: str) -> dict[str, Any]:
    final = None
    for ev in qa.ask_stream(workspace_id, question, doc_ids=[doc_id], use_llm=False):
        if ev["event"] == "answer":
            final = ev["answer"]
    if not final or final["abstained"]:
        return {"status": "not_found", "text": "Not found"}
    text = final["sentences"][0]["text"] if final["sentences"] else final["text"]
    cites = []
    for c in final["citations"][:2]:
        bb = next((b for b in c["bboxes"] if b.get("bbox")), {})
        cites.append({"doc_id": c["doc_id"], "filename": c["filename"], "page": (bb.get("page") or (c["pages"][0] if c["pages"] else None)),
                      "printed_page": (c["printed_pages"][0] if c["printed_pages"] else None), "bbox": bb.get("bbox"),
                      "preview_pages": c["preview_pages"]})
    return {"status": "found", "text": text[:260], "citations": cites, "badges": final["badges"], "receipts": final["receipts"]}


def run_pack(workspace_id: str, pack: str) -> dict[str, Any]:
    spec = PACKS.get(pack)
    if spec is None:
        raise KeyError(pack)
    docs = [d for d in workspaces.documents(workspace_id) if d["status"] == "ready"]
    cols = [d for d in docs if d["doc_type"] in spec["doc_types"]] or docs
    facts = F.all_facts(workspace_id)
    t0 = time.time()
    rows = []
    for label, q in spec["rows"]:
        cells = []
        for d in cols:
            if q.startswith("fact:"):
                cell = _fact_cell(facts, d["doc_id"], q[5:])
            elif q.startswith("growth:"):
                cell = _growth_cell(facts, d["doc_id"], q[7:])
            elif q.startswith("debt:"):
                cell = _debt_cell(workspace_id, d["doc_id"], q[5:])
            else:
                cell = _qa_cell(workspace_id, d["doc_id"], q)
            cells.append({"doc_id": d["doc_id"], **cell})
        rows.append({"label": label, "question": q if not re.match(r"^(fact|growth|debt):", q) else None, "cells": cells})
    result = {"pack": pack, "title": spec["title"], "columns": [{"doc_id": d["doc_id"], "filename": d["filename"], "doc_type": d["doc_type"],
                                                                    "preview_pages": d["preview_pages"]} for d in cols],
              "rows": rows, "seconds": round(time.time() - t0, 1), "ts": time.time()}
    with db.connect() as c:
        c.execute("INSERT INTO pack_runs (workspace_id, pack, ts, json) VALUES (?,?,?,?)", (workspace_id, pack, time.time(), db.jdump(result)))
    db.audit("pack", workspace_id, pack, {"seconds": result["seconds"], "docs": len(cols)})
    return result


def latest_run(workspace_id: str, pack: str) -> dict[str, Any] | None:
    with db.connect() as c:
        r = c.execute("SELECT json FROM pack_runs WHERE workspace_id=? AND pack=? ORDER BY ts DESC LIMIT 1", (workspace_id, pack)).fetchone()
    return db.jload(r["json"]) if r else None


def latest_gaps(workspace_id: str) -> list[dict[str, Any]]:
    """Not-found cells of the most recent run of each pack (they become low-severity seller questions)."""
    out = []
    for pack in PACKS:
        run = latest_run(workspace_id, pack)
        if not run:
            continue
        for row in run["rows"]:
            if row["cells"] and all(cell["status"] == "not_found" for cell in row["cells"]):
                out.append({"pack": run["title"], "question": row["label"]})
    return out


def to_table(run: dict[str, Any]) -> tuple[list[str], list[list[str]]]:
    cols = ["Question"] + [c["filename"] for c in run["columns"]]
    rows = []
    for row in run["rows"]:
        r = [row["label"]]
        for cell in row["cells"]:
            cite = "; ".join(f"{c['filename']} p.{c.get('printed_page') or c.get('page')}" for c in cell.get("citations", []))
            r.append(cell["text"] + (f" [{cite}]" if cite and cell["status"] == "found" else ""))
        rows.append(r)
    return cols, rows
