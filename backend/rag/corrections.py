"""Live correction ripple: fix one cell in the typed table store and see everything it changes.

The correction touches the *derived* data only (the typed grid, the facts extracted from it). The original document is
never modified. The text chunks that were indexed from the table keep the original wording, so free-text answers may
still quote the printed figure; every computed answer, contradiction, total check, seller question and the maturity wall
is recomputed from the corrected grid.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from models.errors import AppError
from rag import db, diligence, facts as F, maturity, workspaces
from services.table_service import parse_financial_number


def _snapshot(workspace_id: str) -> dict[str, Any]:
    con = {c["id"]: c for c in diligence.find_contradictions(workspace_id)}
    tot = {(t["doc_id"], t["table"], t["column"]): t for t in diligence.total_checks(workspace_id)}
    try:
        wall = maturity.maturity_wall(workspace_id)
        wall_total = round(sum(b["value"] for w in wall for b in w["bars"]), 6)
    except Exception:  # noqa: BLE001 - the wall is optional context
        wall_total = None
    return {"contradictions": con, "totals": tot, "seller_questions": len(diligence.seller_questions(workspace_id)), "wall_total": wall_total}


def apply(workspace_id: str, table_id: str, row_idx: int, col_idx: int, new_value: str) -> dict[str, Any]:
    workspaces.require(workspace_id)
    with db.connect() as c:
        r = c.execute("SELECT * FROM tables_store WHERE table_id=? AND workspace_id=?", (table_id, workspace_id)).fetchone()
    if r is None:
        raise AppError("TABLE_NOT_FOUND", "Unknown table.", status_code=404)
    grid = db.jload(r["grid_json"], {})
    if not (0 <= row_idx < len(grid["rows"])) or not (0 < col_idx < grid["n_cols"]):
        raise AppError("CELL_NOT_FOUND", "That cell does not exist in the table.", status_code=404)
    text = " ".join(str(new_value).split())
    parsed = parse_financial_number(text)
    if not isinstance(parsed, float):
        raise AppError("NOT_A_NUMBER", "The corrected value must be a number.", status_code=422)

    before = _snapshot(workspace_id)
    cell = grid["rows"][row_idx]["cells"][col_idx]
    old = cell["raw"]
    cell.update({"raw": text, "v": float(parsed), "pct": text.endswith("%"), "conf": 1.0, "corrected": True, "original": old})
    t = SimpleNamespace(grid=grid, kind=r["kind"] or "table", title=r["title"] or "", statement=r["statement"], scale=r["scale"],
                        unit=r["unit"], currency=r["currency"], period=None, table_ref=r["block_id"], page=r["page"])
    with db.connect() as c:
        doc_type = c.execute("SELECT doc_type FROM documents WHERE id=?", (r["doc_id"],)).fetchone()["doc_type"]
        c.execute("UPDATE tables_store SET grid_json=? WHERE table_id=?", (db.jdump(grid), table_id))
        c.execute("DELETE FROM facts WHERE doc_id=? AND block_id=?", (r["doc_id"], r["block_id"]))
        for f in F.facts_from_table(t, doc_type or "other"):
            c.execute(
                "INSERT INTO facts (workspace_id, doc_id, concept, value, unit, scale, currency, period, block_id, page, bbox_json,"
                " confidence, label, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (workspace_id, r["doc_id"], f["concept"], f["value"], f["unit"], f["scale"], f["currency"], f["period"], f["block_id"],
                 f["page"], db.jdump(f["bbox"]), f["confidence"], f["label"], f["raw"]))
        c.execute("INSERT INTO corrections (workspace_id, doc_id, table_id, row_idx, col_idx, old_value, new_value, ts) VALUES (?,?,?,?,?,?,?,?)",
                  (workspace_id, r["doc_id"], table_id, row_idx, col_idx, old, text, time.time()))
    db.audit("correction", workspace_id, table_id, {"row": row_idx, "col": col_idx, "old": old, "new": text})
    after = _snapshot(workspace_id)

    resolved = [before["contradictions"][k]["summary"] for k in before["contradictions"] if k not in after["contradictions"]]
    created = [after["contradictions"][k]["summary"] for k in after["contradictions"] if k not in before["contradictions"]]
    changed = [{"before": before["contradictions"][k]["summary"], "after": after["contradictions"][k]["summary"]}
               for k in before["contradictions"] if k in after["contradictions"]
               and before["contradictions"][k]["gap_abs"] != after["contradictions"][k]["gap_abs"]]
    tot_resolved = [f"{k[1]} / {k[2]}: stated {before['totals'][k]['stated']} vs sum {before['totals'][k]['expected']}" for k in before["totals"] if k not in after["totals"]]
    tot_new = [f"{k[1]} / {k[2]}: stated {after['totals'][k]['stated']} vs sum {after['totals'][k]['expected']}" for k in after["totals"] if k not in before["totals"]]
    return {
        "table_id": table_id, "row": grid["rows"][row_idx]["label"], "column": grid["col_paths"][col_idx], "old": old, "new": text,
        "ripple": {
            "contradictions_resolved": resolved, "contradictions_new": created, "contradictions_changed": changed,
            "total_checks_resolved": tot_resolved, "total_checks_new": tot_new,
            "seller_questions": {"before": before["seller_questions"], "after": after["seller_questions"]},
            "maturity_total": {"before": before["wall_total"], "after": after["wall_total"]},
        },
        "note": "Derived data was updated; the source document is unchanged. Indexed text passages still show the printed value.",
    }
