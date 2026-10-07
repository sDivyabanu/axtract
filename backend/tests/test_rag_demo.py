"""End-to-end on the generated Project Falcon data room (demo/project_falcon): numbers, receipts, provenance."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_rag_core import ask, room, wait_ready  # noqa: E402,F401  (fixtures + helpers)

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demo" / "project_falcon"
pytestmark = pytest.mark.skipif(not DEMO.exists(), reason="run scripts/make_demo_dataroom.py first")
FACTS = yaml.safe_load((ROOT / "eval" / "golden_qa.yaml").read_text())["facts"] if (ROOT / "eval" / "golden_qa.yaml").exists() else {}


@pytest.fixture()
def falcon(room):
    ws = room.post("/api/workspaces", json={"name": "Falcon"}).json()["workspace_id"]
    files = [("files", (f.name, f.read_bytes())) for f in sorted(DEMO.iterdir())]
    room.post(f"/api/workspaces/{ws}/documents", files=files)
    docs = wait_ready(room, ws, len(files))
    assert all(d["status"] == "ready" for d in docs), [d["error"] for d in docs]
    return room, ws, {d["filename"]: d for d in docs}


def test_total_debt_maturing_has_a_receipt_with_exact_cells(falcon):
    room, ws, docs = falcon
    a = ask(room, ws, "What is the total debt maturing in 2026?")
    assert a["mode"] == "computed" and a["abstained"] is False
    r = a["receipts"][0]
    assert r["result"] == pytest.approx(FACTS["maturing_2026"], abs=0.051)
    assert r["result_display"].startswith("₹") and r["result_display"].endswith("Cr")
    assert {o["filename"] for o in r["operands"]} == {"Falcon_Debt_Schedule.pdf"}
    assert all(o["bbox"] and o["exact_cell"] for o in r["operands"])
    assert {o["page"] for o in r["operands"]} == {1, 2}                      # operands come from both pages of the table
    assert all(o["printed_page"] in ("F-7", "F-8") for o in r["operands"])   # printed page labels survive
    assert a["grounding"]["verified"] == a["grounding"]["total"] > 0
    assert any(o["label"].startswith("Term Loan A") and o["display"] == "₹50.0 Cr" for o in r["operands"])


def test_cross_page_total_and_second_page_row(falcon):
    room, ws, _ = falcon
    t = ask(room, ws, "What is the total amount of the debt schedule?")["receipts"][0]
    assert t["result"] == pytest.approx(FACTS["total_debt"], abs=0.051)
    e = ask(room, ws, "What is the amount of Equipment Loan 45 in the debt schedule?")["receipts"][0]
    assert e["operands"][0]["page"] == 2 and e["operands"][0]["label"].startswith("Equipment Loan 45")


def test_growth_ratio_and_chart_values(falcon):
    room, ws, _ = falcon
    g = ask(room, ws, "What was the revenue growth from FY2024 to FY2025?")["receipts"][0]
    assert g["op"] == "percent_change" and g["result"] == pytest.approx((546.0 / 452.0 - 1) * 100, abs=0.06)
    ratio = ask(room, ws, "What is the ratio of net debt to EBITDA?")["receipts"][0]
    assert ratio["op"] == "ratio" and ratio["result"] == pytest.approx(FACTS["net_debt"] / FACTS["cim_ebitda_fy2024"], abs=0.01)
    chart = ask(room, ws, "What was FY2023 revenue in the CIM revenue chart?")
    assert chart["receipts"][0]["result"] == 385.0
    assert any("estimated" in b["label"] or "confidence" in b["label"] for b in chart["badges"]) or chart["receipts"][0]["warnings"] is not None


def test_unanswerable_and_missing_schedule_abstain(falcon):
    room, ws, _ = falcon
    for q in ("Who is the chief executive officer of Falcon Industries?", "What are the instalment dates in Schedule 3 of the facility agreement?"):
        a = ask(room, ws, q)
        assert a["abstained"], q
