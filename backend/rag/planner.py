"""Turn a numeric question into a table-operation plan (rules first, LLM second).

Both planners emit the same JSON DSL (see table_ops). They only *choose* tables, rows, columns
and operations; they never produce a number. Execution is table_ops.execute().
"""

from __future__ import annotations

import re
from typing import Any

from rag import db, llm, meta as M
from rag.table_ops import PlanError, Table, execute, validate_plan

_STOP = {"what", "which", "how", "much", "many", "is", "are", "was", "were", "the", "a", "an", "of", "in", "for", "to", "and",
         "or", "by", "on", "at", "as", "from", "with", "does", "do", "did", "have", "has", "had", "total", "overall", "all",
         "me", "tell", "show", "give", "company", "year", "fiscal", "between", "than", "per", "cent", "percent"}
_PAIR = re.compile(r"\b(?:between|from)\s+(.+?)\s+(?:and|to)\s+(.+?)(?:\?|$|\bin\b)", re.I)


def _norm(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def _q_tokens(q: str) -> set[str]:
    return {t for t in _norm(q) if t not in _STOP}


# ---------------------------------------------------------------------------
# table loading
# ---------------------------------------------------------------------------


def load_tables(workspace_id: str, table_ids: list[str]) -> dict[str, Table]:
    """Tables from the store, keyed by a short alias (T1, T2, ...) used in plans."""
    if not table_ids:
        return {}
    out: dict[str, Table] = {}
    with db.connect() as c:
        docs = {r["id"]: r["filename"] for r in c.execute("SELECT id, filename FROM documents WHERE workspace_id=?", (workspace_id,))}
        for i, tid in enumerate(table_ids, 1):
            r = c.execute("SELECT * FROM tables_store WHERE table_id=? AND workspace_id=?", (tid, workspace_id)).fetchone()
            if r is None:
                continue
            alias = f"T{i}"
            t = Table(alias, r["doc_id"], docs.get(r["doc_id"], ""), r["title"] or "", r["unit"], r["scale"], r["currency"],
                      db.jload(r["grid_json"], {}), db.jload(r["printed_json"], {}) or {}, r["conf"], bool(r["estimated"]))
            t.real_id = tid  # type: ignore[attr-defined]
            out[alias] = t
    return out


def candidate_table_ids(workspace_id: str, question: str, ranked_chunk_rows: list[dict[str, Any]], limit: int = 4,
                        only_docs: set[str] | None = None) -> list[str]:
    """Tables referenced by the best retrieved chunks, plus tables whose row labels match the question.

    `only_docs` restricts the candidates to the documents the question names explicitly.
    """
    ids: list[str] = []
    for row in ranked_chunk_rows:
        if row.get("table_ref") and (not only_docs or row["doc_id"] in only_docs):
            tid = f"{row['doc_id']}:{row['table_ref']}"
            if tid not in ids:
                ids.append(tid)
    qt = _q_tokens(question)
    scored: list[tuple[float, str]] = []
    with db.connect() as c:
        for r in c.execute("SELECT table_id, doc_id, title, grid_json FROM tables_store WHERE workspace_id=?", (workspace_id,)):
            if r["table_id"] in ids or (only_docs and r["doc_id"] not in only_docs):
                continue
            grid = db.jload(r["grid_json"], {})
            labels = " ".join((x["label"] or "") for x in grid.get("rows", []))
            hit = len(qt & set(_norm((r["title"] or "") + " " + labels + " " + " ".join(grid.get("col_paths", [])))))
            if hit >= 2:
                scored.append((hit, r["table_id"]))
    ids += [t for _, t in sorted(scored, reverse=True)]
    return ids[:limit]


def catalog(tables: dict[str, Table]) -> list[dict[str, Any]]:
    out = []
    for alias, t in tables.items():
        out.append({
            "table": alias, "title": t.title, "document": t.filename, "unit": " ".join(x for x in (t.currency, t.unit) if x) or None,
            "columns": [p for p in t.grid["col_paths"] if p],
            "rows": [r["label"] for r in t.grid["rows"] if r["label"]][:60],
        })
    return out


# ---------------------------------------------------------------------------
# rule planner
# ---------------------------------------------------------------------------

_MATURING = re.compile(r"\b(?:matur\w*|due|expir\w*|repayable|falls? due|payable)\b", re.I)
_GROWTH = re.compile(r"\b(?:growth|grew|grow|increase[ds]?|decrease[ds]?|decline[ds]?|change[ds]?|yoy|year[- ]over[- ]year|y/y)\b", re.I)
_CAGR = re.compile(r"\bcagr\b|compound annual", re.I)
_RATIO = re.compile(r"\bratio\b|\bdivided by\b|\bas a (?:percentage|%|multiple) of\b|\bmultiple of\b|\btimes\b", re.I)
_DIFF = re.compile(r"\bdifference\b|\bhow much (?:more|less|higher|lower)\b|\bgap\b|\bexceeds?\b", re.I)
_TOTAL = re.compile(r"\b(?:total|sum|combined|aggregate|add up|altogether)\b", re.I)
_AVG = re.compile(r"\baverage\b|\bmean\b", re.I)
_MAX = re.compile(r"\b(?:highest|largest|biggest|maximum|max|greatest)\b", re.I)
_MIN = re.compile(r"\b(?:lowest|smallest|minimum|min|least)\b", re.I)
_COUNT = re.compile(r"\bhow many\b|\bnumber of\b|\bcount\b", re.I)
_AMOUNT_COL = re.compile(r"amount|outstanding|principal|balance|facility|sanction|limit|total|value", re.I)
_MATURITY_COL = re.compile(r"matur|due|expir|repay|tenor|term", re.I)


def _label_tokens(label: str) -> set[str]:
    return {x for x in _norm(label) if x not in _STOP}


def _mentioned_rows(t: Table, q_tokens: set[str]) -> list[dict]:
    """Data rows the question refers to.

    Strict: every token of the label is in the question (most specific wins). Partial: at least half of a
    label's tokens are in the question and they add something the strict rows do not already cover
    ("revenue" -> "Revenue from operations"); a partial match is dropped if two rows tie.
    """
    strict = []
    for r in t.grid["rows"]:
        lt = _label_tokens(r["label"])
        # a row the question names outright counts even if it is flagged as a "total-like" row (Net debt)
        if lt and lt <= q_tokens:
            strict.append((len(lt), r))
    strict.sort(key=lambda x: -x[0])
    keep: list[dict] = []
    for _, r in strict:  # drop rows that are a subset of a more specific mentioned row ("loan" vs "term loan a")
        if not any(_label_tokens(r["label"]) < _label_tokens(k["label"]) for k in keep):
            keep.append(r)
    covered = set().union(*[_label_tokens(k["label"]) for k in keep]) if keep else set()

    part = []
    for r in t.grid["rows"]:
        lt = _label_tokens(r["label"])
        if not lt or r["is_total"] or r in keep:
            continue
        got = (lt & q_tokens) - covered
        if got and len(lt & q_tokens) / len(lt) >= 0.5:
            part.append((len(lt & q_tokens) / len(lt), len(got), r))
    part.sort(key=lambda x: (-x[0], -x[1]))
    if part:
        top = [x for x in part if (x[0], x[1]) == (part[0][0], part[0][1])]
        if len(top) == 1:
            keep.append(top[0][2])
    return keep


def _period_cols(t: Table) -> list[tuple[int, str]]:
    return [(i, p) for i, p in enumerate(t.grid["col_periods"]) if p]


def _pick_col(t: Table, periods: list[str], q_tokens: set[str]) -> tuple[str | None, str | None]:
    """(column path, warning). Prefers an explicit period, then a named column, then the only numeric column."""
    paths = t.grid["col_paths"]
    if periods:
        for i, p in _period_cols(t):
            if p == periods[0]:
                return paths[i], None
    named = []
    for i, p in enumerate(paths):
        pt = {x for x in _norm(p) if x not in _STOP}
        if pt and pt & q_tokens and i > 0:
            named.append((len(pt & q_tokens), i))
    if named:
        return paths[max(named)[1]], None
    numeric_cols = [i for i in range(1, len(paths)) if any(r["cells"][i]["v"] is not None for r in t.grid["rows"] if i < len(r["cells"]))]
    if len(numeric_cols) == 1:
        return paths[numeric_cols[0]], None
    pc = _period_cols(t)
    if pc:
        i, p = pc[-1]
        return paths[i], f"no period was specified, so the latest column ({p}) was used"
    amt = [i for i in numeric_cols if _AMOUNT_COL.search(paths[i])]
    return (paths[amt[0]], None) if amt else (None, None)


_UNIT_PAREN = re.compile(r"\s*\((?:[^()]*\b(?:crore|lakh|million|billion|thousand|rs|inr|usd|eur|gbp|in)\b[^()]*|[₹$€£%]+[^()]*)\)", re.I)


def _short(col: str) -> str:
    """Column name without its unit note: 'Amount (Rs crore)' -> 'Amount'."""
    return _UNIT_PAREN.sub("", col).strip() or col


def _lookup(i: str, t: str, row: str, col: str) -> dict:
    return {"id": i, "op": "lookup", "table": t, "row": row, "col": col}


def rule_plans(question: str, tables: dict[str, Table]) -> list[dict[str, Any]]:
    """Deterministic plans for the common numeric questions: one per candidate table, best-matching first."""
    qt = _q_tokens(question)
    scored = []
    for alias, t in tables.items():
        rows = _mentioned_rows(t, qt)
        score = len(rows) * 3 + len(qt & {x for p in t.grid["col_paths"] for x in _norm(p)}) + len(qt & set(_norm(t.title)))
        scored.append((score, alias, t, rows))
    plans = []
    for _, alias, t, rows in sorted(scored, key=lambda x: -x[0]):
        plan = _rule_plan_for(question, alias, t, rows)
        if plan:
            plans.append(plan)
    return plans


def rule_plan(question: str, tables: dict[str, Table]) -> dict[str, Any] | None:
    plans = rule_plans(question, tables)
    return plans[0] if plans else None


def _rule_plan_for(question: str, alias: str, t: Table, rows: list[dict]) -> dict[str, Any] | None:
    qt = _q_tokens(question)
    periods = M.periods_in(question)
    years = re.findall(r"\b(20\d{2}|19\d{2})\b", question)
    paths = t.grid["col_paths"]

    # --- debt maturing / falling due in a given year
    if _MATURING.search(question) and years:
        year = years[0]
        noun = "Debt" if qt & {"debt", "borrowings", "loans", "loan", "facilities", "facility"} else "Amounts"
        mat_cols = [p for i, p in enumerate(paths) if i > 0 and _MATURITY_COL.search(p)]
        amt_cols = [p for i, p in enumerate(paths) if i > 0 and _AMOUNT_COL.search(p) and not _MATURITY_COL.search(p)]
        year_cols = [p for i, p in enumerate(paths) if i > 0 and year in p]
        if mat_cols and amt_cols:
            return {"title": f"{noun} maturing in {year}", "steps": [
                {"id": "s1", "op": "aggregate", "fn": "sum", "table": alias, "col": amt_cols[0],
                 "where": {"col": mat_cols[0], "eq": year}}], "output": "s1"}
        if year_cols:  # a maturity wall laid out with one column per year
            return {"title": f"{noun} maturing in {year}", "steps": [
                {"id": "s1", "op": "aggregate", "fn": "sum", "table": alias, "col": year_cols[0]}], "output": "s1"}

    # --- growth / change between two periods
    if (_GROWTH.search(question) or _CAGR.search(question)) and len(periods) >= 2:
        old, new = sorted(periods[:2])
        target = rows[0]["label"] if rows else next((r["label"] for r in t.grid["rows"] if r["is_total"]), None)
        if target:
            steps = [_lookup("a", alias, target, old), _lookup("b", alias, target, new)]
            if _CAGR.search(question):
                n = int(re.sub(r"\D", "", new)) - int(re.sub(r"\D", "", old))
                steps.append({"id": "c", "op": "cagr", "args": ["a", "b"], "years": n})
                return {"title": f"CAGR of {target} {old}–{new}", "steps": steps, "output": "c"}
            steps.append({"id": "c", "op": "percent_change", "args": ["a", "b"]})
            return {"title": f"Change in {target} from {old} to {new}", "steps": steps, "output": "c"}

    col, warn = _pick_col(t, periods, qt)
    if col is None:
        return None
    title_col = f" ({_short(col)})" if col and not periods else ""

    # --- two rows: ratio / difference / sum
    if len(rows) >= 2:
        a, b = rows[0], rows[1]
        la = [_lookup("a", alias, a["label"], col), _lookup("b", alias, b["label"], col)]
        if _RATIO.search(question):
            return {"title": f"{a['label']} ÷ {b['label']}{title_col}", "steps": la + [{"id": "c", "op": "ratio", "args": ["a", "b"]}], "output": "c"}
        if _DIFF.search(question):
            return {"title": f"{a['label']} − {b['label']}{title_col}", "steps": la + [{"id": "c", "op": "difference", "args": ["a", "b"]}], "output": "c"}
        if _TOTAL.search(question) or len(rows) >= 2:
            return {"title": "Total of " + ", ".join(r["label"] for r in rows) + title_col,
                    "steps": [{"id": "s1", "op": "aggregate", "fn": "sum", "table": alias, "col": col, "rows": [r["label"] for r in rows]}],
                    "output": "s1"}

    # --- two periods of one row: difference
    if len(rows) == 1 and len(periods) >= 2 and _DIFF.search(question):
        r = rows[0]["label"]
        return {"title": f"{r}: {periods[0]} − {periods[1]}", "steps": [_lookup("a", alias, r, periods[0]), _lookup("b", alias, r, periods[1]),
                                                                      {"id": "c", "op": "difference", "args": ["a", "b"]}], "output": "c"}

    # --- one row: lookup
    if len(rows) == 1:
        return {"title": f"{rows[0]['label']}{title_col}", "steps": [_lookup("a", alias, rows[0]["label"], col)], "output": "a"}

    # --- whole-column statistics
    fn = "average" if _AVG.search(question) else "max" if _MAX.search(question) else "min" if _MIN.search(question) \
        else "count" if _COUNT.search(question) else None
    if fn:
        return {"title": f"{fn.capitalize()} of {col}", "steps": [{"id": "s1", "op": "aggregate", "fn": fn, "table": alias, "col": col}], "output": "s1"}

    # --- "total X": the table's own total row, else the sum of its rows
    if _TOTAL.search(question):
        total = next((r for r in t.grid["rows"] if r["is_total"] and r["cells"][paths.index(col)]["v"] is not None), None)
        if total:
            plan = {"title": f"{total['label']}{title_col}", "steps": [_lookup("a", alias, total["label"], col)], "output": "a"}
        else:
            plan = {"title": f"Total of {col}", "steps": [{"id": "s1", "op": "aggregate", "fn": "sum", "table": alias, "col": col}], "output": "s1"}
        if warn:
            plan["warning"] = warn
        return plan
    return None


# ---------------------------------------------------------------------------
# LLM planner (fallback)
# ---------------------------------------------------------------------------

_PLAN_SYSTEM = (
    "You translate a finance question into a JSON plan for a table calculator. You NEVER compute numbers. "
    "Output ONLY a JSON object: {\"title\": str, \"steps\": [...], \"output\": step id}. "
    "Allowed step ops: "
    "lookup {id,op,table,row,col}; "
    "aggregate {id,op,fn:sum|average|min|max|count,table,col,rows:[labels]|where:{col,contains|eq}}; "
    "sum|average|min|max|count {id,op,args:[step ids]}; difference {args:[a,b]} = a-b; ratio {args:[a,b]} = a/b; "
    "percent_change {args:[old,new]}; cagr {args:[start,end],years:n}. "
    "Use ONLY table aliases, row labels and column names exactly as given in the catalog. "
    "If the question cannot be answered from these tables output {\"steps\": []}."
)


def llm_plan(question: str, tables: dict[str, Table]) -> dict[str, Any] | None:
    cat = catalog(tables)
    if not cat:
        return None
    import json

    obj = llm.chat_json([
        {"role": "system", "content": _PLAN_SYSTEM},
        {"role": "user", "content": f"Catalog:\n{json.dumps(cat, ensure_ascii=False)}\n\nQuestion: {question}"},
    ], max_tokens=500)
    if not obj or not obj.get("steps"):
        return None
    try:
        return validate_plan(obj)
    except PlanError:
        return None


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def plan_and_run(question: str, tables: dict[str, Table]) -> dict[str, Any]:
    """{'result': Result|None, 'plan': dict|None, 'planner': 'rules'|'llm'|None, 'error': str|None, 'refused': bool}"""
    attempts: list[tuple[str, dict[str, Any] | None]] = [("rules", p) for p in rule_plans(question, tables)]
    if llm.status()["available"]:
        attempts.append(("llm", None))
    last_error: str | None = None
    for kind, plan in attempts:
        if kind == "llm":
            plan = llm_plan(question, tables)
        if not plan:
            continue
        try:
            res = execute(plan, tables)
            if plan.get("warning"):
                res.receipt["warnings"].append(plan["warning"])
            return {"result": res, "plan": plan, "planner": kind, "error": None, "refused": False}
        except PlanError as exc:
            last_error = str(exc)
            # unit / kind mismatches are refusals (explain); unresolved rows/columns try the next plan
            if "different currencies" in last_error or "different kinds" in last_error:
                return {"result": None, "plan": plan, "planner": kind, "error": last_error, "refused": True}
    return {"result": None, "plan": None, "planner": None, "error": last_error, "refused": False}
