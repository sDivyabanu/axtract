"""Deterministic table engine: a whitelisted operation DSL executed by our code.

The LLM (or the rule planner) only produces a JSON *plan*; every number is computed here, on
typed cell values, with units carried through. Plans are data, never code: nothing is eval'd.

Plan:
  {"title": "...", "steps": [step, ...], "output": "<step id>"}
Steps (id is unique, args reference earlier step ids):
  {"id","op":"lookup",    "table","row","col"}                      one cell
  {"id","op":"aggregate", "fn":"sum|average|min|max|count", "table","col",
                          "rows":[labels] | "where":{"col","contains"|"eq"} | omitted (all data rows)}
  {"id","op":"sum|average|min|max|count","args":[ids]}
  {"id","op":"difference","args":[a,b]}          a - b
  {"id","op":"ratio","args":[a,b]}               a / b
  {"id","op":"percent_change","args":[old,new]}
  {"id","op":"cagr","args":[start,end],"years":n}
  {"id","op":"filter", ... }                     alias of aggregate with fn=count over a `where`
Mismatched currencies / mixing percentages with amounts are refused with an explanation.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

OPS = {"lookup", "aggregate", "sum", "average", "min", "max", "count", "difference", "ratio",
       "percent_change", "cagr", "filter"}

_SYMBOL = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
_UNIT_ABBR = {"crore": "Cr", "lakh": "L", "million": "M", "billion": "B", "thousand": "K"}


class PlanError(Exception):
    """The plan cannot be executed (unknown table/row/column, unit mismatch, bad op). Message is user-facing."""


@dataclass
class Cell:
    table_id: str
    label: str
    col: str
    value: float
    raw: str
    pct: bool
    doc_id: str
    filename: str
    page: int
    printed: str | None
    bbox: list[float] | None
    exact_cell: bool
    conf: float | None
    period: str | None
    estimated: bool = False


@dataclass
class Val:
    value: float
    scale: float | None
    currency: str | None
    unit_label: str | None
    kind: str  # amount | pct | ratio | count | number
    operands: list[Cell] = field(default_factory=list)
    expr: str = ""
    period: str | None = None
    decimals: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class Table:
    """A table from the table store, ready for execution."""
    table_id: str
    doc_id: str
    filename: str
    title: str
    unit: str | None
    scale: float | None
    currency: str | None
    grid: dict[str, Any]
    printed: dict[str, str]
    conf: float | None = None
    estimated: bool = False


def _norm(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


# NB: single letters ("A", "B") are significant in labels such as "Term Loan A", so they are not stopwords.
_STOP = {"the", "an", "of", "and", "in", "on", "for", "to", "as", "at", "by", "total", "net", "from", "with"}


def _tok(s: str) -> set[str]:
    return {t for t in _norm(s) if t not in _STOP}


def label_score(wanted: str, label: str) -> float:
    """How well `label` matches what was asked for. 1.0 = identical after normalisation.

    A wanted phrase that is only part of a label ("Term Loan" vs "Term Loan A") scores below
    an exact match, so two such rows tie and the caller must disambiguate instead of guessing.
    """
    a, b = _norm(wanted), _norm(label)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = _tok(wanted) or set(a), _tok(label) or set(b)
    if ta == tb:
        return 0.97
    if ta <= tb:
        return 0.9
    if tb <= ta:
        return 0.75
    inter = len(ta & tb)
    return (inter / len(ta | tb)) * 0.85 if inter else 0.0


def resolve_row(table: Table, wanted: str, threshold: float = 0.7):
    rows = table.grid["rows"]
    scored = [(label_score(wanted, r["label"]), r) for r in rows if r["label"]]
    scored = [x for x in scored if x[0] >= threshold]
    if not scored:
        raise PlanError(f"No row matching '{wanted}' in the table '{table.title or table.table_id}'.")
    scored.sort(key=lambda x: -x[0])
    if len(scored) > 1 and scored[0][0] == scored[1][0] and scored[0][0] < 1.0:
        raise PlanError(f"'{wanted}' matches several rows ({scored[0][1]['label']}; {scored[1][1]['label']}). Be more specific.")
    return scored[0][1]


def resolve_col(table: Table, wanted: str, threshold: float = 0.6) -> int:
    paths = table.grid["col_paths"]
    periods = table.grid["col_periods"]
    from rag import meta as M

    want_period = M.normalize_period(wanted) if re.search(r"(?:FY|Q[1-4]|20\d\d|19\d\d)", wanted, re.I) else None
    best, best_s = -1, 0.0
    for i, p in enumerate(paths):
        s = label_score(wanted, p) if p else 0.0
        if want_period and periods[i] == want_period:
            s = max(s, 0.98)
        if s > best_s:
            best, best_s = i, s
    if best < 0 or best_s < threshold:
        raise PlanError(f"No column matching '{wanted}' in the table '{table.title or table.table_id}' (columns: {', '.join(p for p in paths if p)}).")
    return best


def _cell(table: Table, row: dict, ci: int) -> Cell:
    c = row["cells"][ci]
    if c["v"] is None:
        raise PlanError(f"The cell '{row['label']}' / '{table.grid['col_paths'][ci]}' is not a number ({c['raw']!r}).")
    page = int(c["page"] or 1)
    return Cell(
        table_id=table.table_id, label=row["label"], col=table.grid["col_paths"][ci], value=float(c["v"]),
        raw=c["raw"] or "", pct=bool(c.get("pct")), doc_id=table.doc_id, filename=table.filename, page=page,
        printed=table.printed.get(str(page)), bbox=c.get("bbox"), exact_cell=bool(c.get("exact_cell")),
        conf=table.conf, period=table.grid["col_periods"][ci], estimated=table.estimated,
    )


def _decimals(raw: str) -> int:
    m = re.search(r"\.(\d+)", raw or "")
    return len(m.group(1)) if m else 0


def _leaf_val(table: Table, cell: Cell) -> Val:
    kind = "pct" if cell.pct else ("amount" if (table.scale or table.currency) else "number")
    # a percentage cell is stored as a fraction (50% -> 0.5); show it as a percentage
    value = cell.value * 100 if cell.pct else cell.value
    v = Val(value, None if cell.pct else table.scale, None if cell.pct else table.currency,
            None if cell.pct else table.unit, kind, [cell], f"{cell.label} {cell.raw}", cell.period,
            _decimals(cell.raw))
    if cell.estimated:
        v.warnings.append("values estimated from a chart image")
    return v


def _base(v: Val) -> float:
    return v.value * (v.scale or 1.0) if v.kind == "amount" else v.value


def _combine_check(vals: list[Val], op: str) -> None:
    kinds = {v.kind for v in vals}
    if len(kinds) > 1 and not ({"amount", "number"} >= kinds):
        raise PlanError(f"Cannot {op} values of different kinds ({', '.join(sorted(kinds))}).")
    cur = {v.currency for v in vals if v.currency}
    if len(cur) > 1:
        raise PlanError(f"Cannot {op} amounts in different currencies ({', '.join(sorted(cur))}); no conversion rate is available in the data room.")


def _merge(vals: list[Val]) -> tuple[float | None, str | None, str | None, int, list[str]]:
    """Reference scale/currency/unit for a combined result + warnings for unstated units."""
    ref = next((v for v in vals if v.scale or v.currency), vals[0])
    warns = []
    if any(v.kind == "amount" and not v.currency and not v.scale for v in vals) and any(v.currency or v.scale for v in vals):
        warns.append("the unit of one operand is not stated in its table")
    for v in vals:
        warns.extend(w for w in v.warnings if w not in warns)
    return ref.scale, ref.currency, ref.unit_label, max(v.decimals for v in vals), warns


def _combine(vals: list[Val], fn: str, expr_op: str) -> Val:
    if not vals:
        raise PlanError("There is nothing to combine.")
    _combine_check(vals, fn)
    scale, cur, unit, dec, warns = _merge(vals)
    kind = vals[0].kind if len({v.kind for v in vals}) == 1 else "amount"
    bases = [_base(v) for v in vals]
    if fn == "sum":
        res = sum(bases)
    elif fn == "average":
        res = sum(bases) / len(bases)
    elif fn == "min":
        res = min(bases)
    elif fn == "max":
        res = max(bases)
    elif fn == "count":
        res, kind = float(len(vals)), "count"
    else:
        raise PlanError(f"Unsupported operation '{fn}'.")
    out_value = res / (scale or 1.0) if kind == "amount" else res
    operands = [c for v in vals for c in v.operands]
    expr = f" {expr_op} ".join(v.expr for v in vals) if fn == "sum" else f"{fn}({', '.join(v.expr for v in vals)})"
    return Val(out_value, scale, cur, unit, kind, operands, expr, vals[0].period, dec if fn != "average" else dec + 1, warns)


def _two(args: list[Val], op: str) -> tuple[Val, Val]:
    if len(args) != 2:
        raise PlanError(f"'{op}' needs exactly two operands.")
    return args[0], args[1]


def _apply(step: dict, env: dict[str, Val], tables: dict[str, Table]) -> Val:
    op = step.get("op")
    if op not in OPS:
        raise PlanError(f"Operation '{op}' is not allowed.")

    if op == "lookup":
        t = _table(tables, step.get("table"))
        row = resolve_row(t, str(step.get("row", "")))
        ci = resolve_col(t, str(step.get("col", "")))
        return _leaf_val(t, _cell(t, row, ci))

    if op in ("aggregate", "filter"):
        t = _table(tables, step.get("table"))
        ci = resolve_col(t, str(step.get("col", "")))
        rows = t.grid["rows"]
        if step.get("rows"):
            chosen = [resolve_row(t, str(r)) for r in step["rows"]]
        else:
            chosen = [r for r in rows if r["label"] and not r["is_total"]]
        where = step.get("where")
        if where:
            wi = resolve_col(t, str(where.get("col", "")))
            def hit(r):
                raw = str(r["cells"][wi]["raw"] or "")
                if "eq" in where:
                    return raw.strip().lower() == str(where["eq"]).strip().lower() or str(where["eq"]) in re.findall(r"\d{4}", raw)
                return str(where.get("contains", "")).lower() in raw.lower()
            chosen = [r for r in chosen if hit(r)]
        vals = []
        for r in chosen:
            if r["cells"][ci]["v"] is not None:
                vals.append(_leaf_val(t, _cell(t, r, ci)))
        fn = step.get("fn") or ("count" if op == "filter" else "sum")
        if not vals:
            if fn in ("sum", "count"):
                zero = Val(0.0, t.scale, t.currency, t.unit, "count" if fn == "count" else ("amount" if (t.scale or t.currency) else "number"),
                           [], "no matching rows", None, 0)
                return zero
            raise PlanError("No rows matched the condition.")
        return _combine(vals, fn, "+")

    args = [env[a] for a in step.get("args", []) if a in env]
    if len(args) != len(step.get("args", [])):
        raise PlanError("A step refers to a value that was not computed.")

    if op in ("sum", "average", "min", "max", "count"):
        return _combine(args, op, "+")

    if op == "difference":
        a, b = _two(args, op)
        _combine_check([a, b], "subtract")
        scale, cur, unit, dec, warns = _merge([a, b])
        res = (_base(a) - _base(b)) / ((scale or 1.0) if a.kind == "amount" else 1.0)
        return Val(res, scale, cur, unit, a.kind, a.operands + b.operands, f"{a.expr} − {b.expr}", a.period, dec, warns)

    if op == "ratio":
        a, b = _two(args, op)
        _combine_check([a, b], "divide")
        if _base(b) == 0:
            raise PlanError("Division by zero.")
        return Val(_base(a) / _base(b), None, None, None, "ratio", a.operands + b.operands, f"{a.expr} ÷ {b.expr}", None, 2,
                   _merge([a, b])[4])

    if op == "percent_change":
        old, new = _two(args, op)
        _combine_check([old, new], "compare")
        if _base(old) == 0:
            raise PlanError("The starting value is zero, so a percentage change is undefined.")
        return Val((_base(new) - _base(old)) / abs(_base(old)) * 100, None, None, None, "pct", old.operands + new.operands,
                   f"({new.expr} − {old.expr}) ÷ {old.expr}", new.period, 1, _merge([old, new])[4])

    if op == "cagr":
        start, end = _two(args, op)
        years = float(step.get("years") or 0)
        if years <= 0 or _base(start) <= 0 or _base(end) <= 0:
            raise PlanError("CAGR needs positive start/end values and a positive number of years.")
        return Val(((_base(end) / _base(start)) ** (1 / years) - 1) * 100, None, None, None, "pct",
                   start.operands + end.operands, f"({end.expr} ÷ {start.expr})^(1/{years:g}) − 1", end.period, 1,
                   _merge([start, end])[4])
    raise PlanError(f"Unsupported operation '{op}'.")


def _table(tables: dict[str, Table], ref: Any) -> Table:
    t = tables.get(str(ref))
    if t is None:
        raise PlanError(f"Unknown table '{ref}'.")
    return t


def validate_plan(plan: Any) -> dict[str, Any]:
    """Structural validation (shape + allowed ops). Raises PlanError."""
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list) or not plan["steps"]:
        raise PlanError("The plan has no steps.")
    if len(plan["steps"]) > 24:
        raise PlanError("The plan is too long.")
    ids = set()
    for s in plan["steps"]:
        if not isinstance(s, dict) or s.get("op") not in OPS or not isinstance(s.get("id"), str):
            raise PlanError("A step is malformed or uses a disallowed operation.")
        if s["id"] in ids:
            raise PlanError("Duplicate step id.")
        ids.add(s["id"])
        for a in s.get("args", []) or []:
            if a not in ids:
                raise PlanError("A step refers to a later or unknown step.")
    out = plan.get("output") or plan["steps"][-1]["id"]
    if out not in ids:
        raise PlanError("The plan output is not a step.")
    plan["output"] = out
    return plan


# ---------------------------------------------------------------------------
# formatting + receipts
# ---------------------------------------------------------------------------


def fmt_number(x: float, decimals: int) -> str:
    return f"{x:,.{decimals}f}"


def display(v: Val, value: float | None = None) -> str:
    x = v.value if value is None else value
    if v.kind == "pct":
        return f"{x:+.{max(v.decimals, 1)}f}%" if v.expr.startswith("(") else f"{x:.{max(v.decimals, 1)}f}%"
    if v.kind == "ratio":
        return f"{x:.2f}×"
    if v.kind == "count":
        return f"{int(round(x))}"
    s = fmt_number(x, v.decimals)
    sym = _SYMBOL.get(v.currency or "", "")
    unit = _UNIT_ABBR.get(v.unit_label or "", v.unit_label or "")
    if sym:
        return f"{'-' if x < 0 else ''}{sym}{fmt_number(abs(x), v.decimals)}{(' ' + unit) if unit else ''}"
    return f"{s}{(' ' + unit) if unit else ''}"


def _cell_display(c: Cell, table: Table | None) -> str:
    if c.pct:
        return f"{c.value * 100:.{max(_decimals(c.raw), 1)}f}%"
    cur = table.currency if table else None
    sym = _SYMBOL.get(cur or "", "")
    unit = _UNIT_ABBR.get((table.unit if table else None) or "", (table.unit if table else None) or "")
    d = _decimals(c.raw)
    num = fmt_number(abs(c.value), d)
    return f"{'-' if c.value < 0 else ''}{sym}{num}{(' ' + unit) if unit else ''}"


@dataclass
class Result:
    receipt: dict[str, Any]
    value: Val
    numbers: list[float]  # every number the verifier may treat as supported


def to_receipt(rid: str, title: str, v: Val, tables: dict[str, Table], op: str) -> Result:
    ops = []
    for c in v.operands:
        tb = tables.get(c.table_id)
        ops.append({
            "label": f"{c.label}" + (f" · {c.col}" if c.col and c.col != c.label else ""), "value": c.value,
            "display": _cell_display(c, tb), "doc_id": c.doc_id, "filename": c.filename, "page": c.page,
            "printed_page": c.printed, "bbox": c.bbox, "confidence": c.conf, "exact_cell": c.exact_cell,
            "period": c.period,
        })
    res = display(v)
    parts = []
    for o, c in zip(ops, v.operands):
        pg = f"p.{c.printed or c.page}"
        parts.append(f"{c.label} {o['display']} ({c.filename} {pg})")
    shown = parts if len(parts) <= 4 else parts[:3] + [f"{len(parts) - 3} more rows (listed below)"]
    formula = f"{res} = " + (" + ".join(shown) if op in ("sum", "aggregate", "filter") else
                             (" ; ".join(shown) if len(shown) > 1 else shown[0] if shown else v.expr))
    if op in ("difference", "ratio", "percent_change", "cagr") and len(parts) == 2:
        sep = {"difference": " − ", "ratio": " ÷ ", "percent_change": " vs ", "cagr": " → "}[op]
        formula = f"{res} = {parts[1]}{sep}{parts[0]}" if op in ("percent_change", "cagr") else f"{res} = {parts[0]}{sep}{parts[1]}"
    confs = [c.conf for c in v.operands if c.conf is not None]
    warnings = list(v.warnings)
    if any(c.conf is not None and c.conf < 0.85 for c in v.operands):
        warnings.append("a source value has low extraction confidence")
    unit = " ".join(x for x in (v.currency, v.unit_label) if x)
    nums = [abs(v.value), abs(v.value) if v.kind != "pct" else abs(v.value)]
    nums += [abs(c.value) * (100 if c.pct else 1) for c in v.operands] + [abs(c.value) for c in v.operands]
    nums += [float(m) for o in ops for m in re.findall(r"\d[\d,]*\.?\d*", o["display"].replace(",", "")) if m]
    nums += [float(m) for m in re.findall(r"\d[\d,]*\.?\d*", res.replace(",", "")) if m]
    receipt = {
        "id": rid, "op": op, "title": title, "result_display": res, "result": v.value, "unit": unit,
        "period": v.period, "formula": formula, "operands": ops, "min_confidence": min(confs) if confs else None,
        "warnings": sorted(set(warnings)),
    }
    return Result(receipt, v, nums)


def execute(plan: dict[str, Any], tables: dict[str, Table]) -> Result:
    """Run a validated plan; return the receipt of its output step. Raises PlanError."""
    plan = validate_plan(plan)
    env: dict[str, Val] = {}
    last_op = "lookup"
    for step in plan["steps"]:
        env[step["id"]] = _apply(step, env, tables)
        if step["id"] == plan["output"]:
            last_op = step["op"] if step["op"] != "filter" else "aggregate"
    out = env[plan["output"]]
    if not math.isfinite(out.value):
        raise PlanError("The result is not a finite number.")
    title = str(plan.get("title") or "Result")[:140]
    return to_receipt("r1", title, out, tables, last_op)
