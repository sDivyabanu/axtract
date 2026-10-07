"""Rule planner + engine on realistic tables (no LLM)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag import llm, planner  # noqa: E402
from tests.test_table_ops import make_table  # noqa: E402


def no_llm(monkeypatch):
    monkeypatch.setattr(llm, "status", lambda force=False: {"available": False, "model": "x", "reason": "t"})


DEBT = make_table("T1", [("Term Loan A", ["50.0", "2026"]), ("Revolver", ["32.0", "2026"]), ("Term Loan B", ["120.5", "2028"]),
                         ("Total", ["202.5", ""])], ["Amount", "Maturity"])
FIN = make_table("T2", [("Revenue", ["100.0", "121.0"]), ("EBITDA", ["20.0", "30.0"]), ("Net debt", ["50.0", "40.0"])],
                 ["FY2023", "FY2025"], periods=["FY2023", "FY2025"], unit="million", scale=1e6, currency="USD", fname="Audited FS.pdf")


def run(q, *tables):
    tabs = {t.table_id: t for t in tables}
    for t in tabs.values():
        t.real_id = f"d1:{t.table_id}"
    return planner.plan_and_run(q, tabs)


def test_total_debt_maturing_in_year(monkeypatch):
    no_llm(monkeypatch)
    r = run("What is the total debt maturing in 2026?", DEBT)
    assert r["planner"] == "rules" and r["result"].receipt["result_display"] == "₹82.0 Cr"
    assert [o["label"].split(" ·")[0] for o in r["result"].receipt["operands"]] == ["Term Loan A", "Revolver"]


def test_single_lookup_with_period(monkeypatch):
    no_llm(monkeypatch)
    r = run("What was EBITDA in FY2025?", FIN)
    assert r["result"].receipt["result"] == 30.0 and r["result"].receipt["result_display"] == "$30.0 M"


def test_growth_between_periods(monkeypatch):
    no_llm(monkeypatch)
    r = run("What was the revenue growth from FY2023 to FY2025?", FIN)
    assert r["result"].receipt["result_display"] == "+21.0%" and r["result"].receipt["op"] == "percent_change"


def test_ratio_and_difference_of_rows(monkeypatch):
    no_llm(monkeypatch)
    rt = run("What is the ratio of net debt to EBITDA in FY2025?", FIN)["result"].receipt
    assert rt["op"] == "ratio" and abs(rt["result"] - 40 / 30) < 1e-9
    df = run("What is the difference between revenue and EBITDA in FY2023?", FIN)["result"].receipt
    assert df["result"] == 80.0


def test_total_uses_the_total_row(monkeypatch):
    no_llm(monkeypatch)
    r = run("What is the total amount outstanding?", DEBT)
    assert r["result"].receipt["result"] == 202.5 and r["result"].receipt["op"] == "lookup"


def test_two_rows_sum(monkeypatch):
    no_llm(monkeypatch)
    r = run("What is the total of Term Loan A and Revolver?", DEBT)
    assert r["result"].receipt["result"] == 82.0


def test_unit_mismatch_is_refused_not_computed(monkeypatch):
    no_llm(monkeypatch)
    # same question over two currencies: summing across them must be refused
    t = make_table("T3", [("Term Loan A", ["10.0", "2026"])], ["Amount", "Maturity"], unit="million", scale=1e6, currency="USD")
    r = planner.plan_and_run("x", {"T1": DEBT, "T3": t}) if False else None
    from rag.table_ops import PlanError, execute
    import pytest
    plan = {"title": "s", "steps": [{"id": "a", "op": "lookup", "table": "T1", "row": "Revolver", "col": "Amount"},
                                    {"id": "b", "op": "lookup", "table": "T3", "row": "Term Loan A", "col": "Amount"},
                                    {"id": "s", "op": "sum", "args": ["a", "b"]}], "output": "s"}
    with pytest.raises(PlanError, match="different currencies"):
        execute(plan, {"T1": DEBT, "T3": t})


def test_unanswerable_returns_nothing(monkeypatch):
    no_llm(monkeypatch)
    r = run("Who is the CEO?", DEBT)
    assert r["result"] is None and not r["refused"]
