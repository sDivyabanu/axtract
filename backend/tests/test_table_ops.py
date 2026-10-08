"""The table-operation DSL: exact arithmetic, unit carry-through, refusals, whitelist."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.table_ops import PlanError, Table, execute, validate_plan  # noqa: E402


def make_table(tid, rows, cols, *, unit="crore", scale=1e7, currency="INR", periods=None, doc="d1", fname="Debt Schedule.pdf",
               page=7, conf=None, total=True):
    """rows: [(label, [raw,...])]; cells whose raw parses as a number get a value."""
    from services.table_service import parse_financial_number

    grid_rows = []
    for i, (label, raws) in enumerate(rows):
        cells = [{"raw": label, "v": None, "pct": False, "page": page, "bbox": [0.1, 0.2 + i * .05, 0.4, 0.24 + i * .05], "exact_cell": True}]
        for raw in raws:
            p = parse_financial_number(raw) if raw else raw
            cells.append({"raw": raw, "v": float(p) if isinstance(p, float) else None, "pct": str(raw).endswith("%"),
                          "page": page, "bbox": [0.5, 0.2 + i * .05, 0.7, 0.24 + i * .05], "exact_cell": True})
        grid_rows.append({"ridx": i + 1, "label": label, "is_total": label.lower().startswith("total"), "cells": cells})
    grid = {"header_rows": 1, "col_paths": ["Facility"] + cols, "col_periods": [None] + (periods or [None] * len(cols)),
            "rows": grid_rows, "merged": [], "n_cols": len(cols) + 1}
    return Table(tid, doc, fname, "Debt schedule", unit, scale, currency, grid, {str(page): "F-7"}, conf)


DEBT = make_table("t1", [("Term Loan A", ["50.0", "2026"]), ("Revolver", ["32.0", "2026"]), ("Term Loan B", ["120.5", "2028"]),
                         ("Total", ["202.5", ""])], ["Amount", "Maturity"])


def run(plan, *tables):
    return execute(plan, {t.table_id: t for t in tables})


class TestOperations:
    def test_lookup_with_provenance(self):
        r = run({"title": "Term Loan A", "steps": [{"id": "a", "op": "lookup", "table": "t1", "row": "Term Loan A", "col": "Amount"}]}, DEBT)
        assert r.receipt["result"] == 50.0 and r.receipt["result_display"] == "₹50.0 Cr"
        o = r.receipt["operands"][0]
        assert o["page"] == 7 and o["printed_page"] == "F-7" and o["bbox"] and o["exact_cell"] and o["filename"] == "Debt Schedule.pdf"

    def test_aggregate_where_excludes_total_row(self):
        plan = {"title": "Debt maturing in 2026", "steps": [{"id": "s", "op": "aggregate", "fn": "sum", "table": "t1", "col": "Amount",
                                                            "where": {"col": "Maturity", "eq": 2026}}]}
        r = run(plan, DEBT)
        assert r.receipt["result"] == 82.0 and r.receipt["result_display"] == "₹82.0 Cr"
        assert [o["label"].split(" ·")[0] for o in r.receipt["operands"]] == ["Term Loan A", "Revolver"]
        assert "Term Loan A ₹50.0 Cr (Debt Schedule.pdf p.F-7)" in r.receipt["formula"]

    def test_sum_difference_ratio_percent_change_cagr_average_min_max_count(self):
        t = make_table("t2", [("Revenue", ["100", "121"]), ("Cost", ["60", "66"])], ["FY2023", "FY2025"], unit="million", scale=1e6,
                       currency="USD", periods=["FY2023", "FY2025"])
        L = lambda i, r, c: {"id": i, "op": "lookup", "table": "t2", "row": r, "col": c}
        base = [L("a", "Revenue", "FY2023"), L("b", "Revenue", "FY2025"), L("c", "Cost", "FY2025")]
        cases = {
            "sum": ({"id": "x", "op": "sum", "args": ["a", "b"]}, 221.0),
            "difference": ({"id": "x", "op": "difference", "args": ["b", "c"]}, 55.0),
            "ratio": ({"id": "x", "op": "ratio", "args": ["b", "c"]}, 121 / 66),
            "percent_change": ({"id": "x", "op": "percent_change", "args": ["a", "b"]}, 21.0),
            "cagr": ({"id": "x", "op": "cagr", "args": ["a", "b"], "years": 2}, 10.0),
            "average": ({"id": "x", "op": "average", "args": ["a", "b"]}, 110.5),
            "min": ({"id": "x", "op": "min", "args": ["a", "c"]}, 66.0),
            "max": ({"id": "x", "op": "max", "args": ["a", "b"]}, 121.0),
            "count": ({"id": "x", "op": "count", "args": ["a", "b", "c"]}, 3.0),
        }
        for name, (step, expected) in cases.items():
            r = run({"title": name, "steps": base + [step], "output": "x"}, t)
            assert r.receipt["result"] == pytest.approx(expected, rel=1e-9), name
        pc = run({"title": "g", "steps": base + [cases["percent_change"][0]]}, t).receipt
        assert pc["result_display"] == "+21.0%" and len(pc["operands"]) == 2

    def test_scale_conversion_between_tables(self):
        cr = make_table("c", [("Loan", ["1.0"])], ["Amount"], unit="crore", scale=1e7, currency="INR")
        lakh = make_table("l", [("Loan", ["50"])], ["Amount"], unit="lakh", scale=1e5, currency="INR")
        plan = {"title": "sum", "steps": [{"id": "a", "op": "lookup", "table": "c", "row": "Loan", "col": "Amount"},
                                          {"id": "b", "op": "lookup", "table": "l", "row": "Loan", "col": "Amount"},
                                          {"id": "s", "op": "sum", "args": ["a", "b"]}], "output": "s"}
        r = run(plan, cr, lakh)
        assert r.receipt["result"] == pytest.approx(1.5)  # 1 crore + 50 lakh = 1.5 crore (first operand's unit)


class TestRefusals:
    def test_currency_mismatch_is_refused_with_explanation(self):
        usd = make_table("u", [("Loan", ["10.0"])], ["Amount"], unit="million", scale=1e6, currency="USD")
        plan = {"title": "s", "steps": [{"id": "a", "op": "lookup", "table": "t1", "row": "Revolver", "col": "Amount"},
                                        {"id": "b", "op": "lookup", "table": "u", "row": "Loan", "col": "Amount"},
                                        {"id": "s", "op": "sum", "args": ["a", "b"]}], "output": "s"}
        with pytest.raises(PlanError, match="different currencies"):
            run(plan, DEBT, usd)

    def test_percent_and_amount_cannot_be_added(self):
        t = make_table("p", [("Loan", ["10.0", "5%"])], ["Amount", "Rate"])
        plan = {"title": "s", "steps": [{"id": "a", "op": "lookup", "table": "p", "row": "Loan", "col": "Amount"},
                                        {"id": "b", "op": "lookup", "table": "p", "row": "Loan", "col": "Rate"},
                                        {"id": "s", "op": "sum", "args": ["a", "b"]}], "output": "s"}
        with pytest.raises(PlanError, match="different kinds"):
            run(plan, t)

    def test_unknown_row_column_table_and_non_numeric_cell(self):
        for step, msg in [({"op": "lookup", "table": "t1", "row": "Nonexistent", "col": "Amount"}, "No row matching"),
                          ({"op": "lookup", "table": "t1", "row": "Revolver", "col": "Nope"}, "No column matching"),
                          ({"op": "lookup", "table": "zz", "row": "Revolver", "col": "Amount"}, "Unknown table"),
                          ({"op": "lookup", "table": "t1", "row": "Revolver", "col": "Facility"}, "not a number")]:
            with pytest.raises(PlanError, match=msg):
                run({"steps": [{"id": "a", **step}]}, DEBT)

    def test_division_by_zero_and_cagr_domain(self):
        t = make_table("z", [("A", ["0"]), ("B", ["5"])], ["V"])
        L = lambda i, r: {"id": i, "op": "lookup", "table": "z", "row": r, "col": "V"}
        with pytest.raises(PlanError, match="zero"):
            run({"steps": [L("a", "A"), L("b", "B"), {"id": "x", "op": "ratio", "args": ["b", "a"]}]}, t)
        with pytest.raises(PlanError, match="CAGR"):
            run({"steps": [L("a", "A"), L("b", "B"), {"id": "x", "op": "cagr", "args": ["a", "b"], "years": 3}]}, t)

    def test_ambiguous_row_is_refused(self):
        t = make_table("m", [("Term Loan A", ["1"]), ("Term Loan B", ["2"])], ["V"])
        with pytest.raises(PlanError, match="several rows"):
            run({"steps": [{"id": "a", "op": "lookup", "table": "m", "row": "Term Loan", "col": "V"}]}, t)

    def test_estimated_and_low_confidence_values_are_warned(self):
        t = make_table("e", [("Q1", ["120"])], ["Revenue"], unit="million", scale=1e6, currency="USD", conf=0.5)
        t.estimated = True
        r = run({"steps": [{"id": "a", "op": "lookup", "table": "e", "row": "Q1", "col": "Revenue"}]}, t)
        assert any("estimated" in w for w in r.receipt["warnings"]) and any("low extraction confidence" in w for w in r.receipt["warnings"])
        assert r.receipt["min_confidence"] == 0.5


class TestWhitelist:
    @pytest.mark.parametrize("bad", [
        {"steps": [{"id": "a", "op": "eval", "expr": "__import__('os').system('x')"}]},
        {"steps": [{"id": "a", "op": "exec"}]}, {"steps": []}, {"steps": "sum(1,2)"}, "1+1", None,
        {"steps": [{"id": "a", "op": "sum", "args": ["b"]}]},
        {"steps": [{"id": "a", "op": "lookup"}, {"id": "a", "op": "lookup"}]},
    ])
    def test_non_whitelisted_or_malformed_plans_are_rejected(self, bad):
        with pytest.raises(PlanError):
            validate_plan(bad)
