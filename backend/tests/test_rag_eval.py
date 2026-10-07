"""Phase 5: the evaluation judge, and the demo data room against the golden questions (extractive mode, no LLM)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag import evalrun, qa  # noqa: E402
from tests.test_rag_core import room, wait_ready  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demo" / "project_falcon"
GOLD = ROOT / "eval" / "golden_qa.yaml"


class TestJudge:
    def test_numeric_within_tolerance(self):
        q = {"type": "numeric", "expected_value": 104.8}
        assert evalrun.judge(q, "dealLens", {"text": "Total maturing is ₹104.8 Cr."})["pass"]
        assert evalrun.judge(q, "baseline", {"text": "about 104.8 crore"})["pass"]
        assert not evalrun.judge(q, "baseline", {"text": "It is 110.2 crore"})["pass"]

    def test_unanswerable_needs_a_decline(self):
        q = {"type": "unanswerable"}
        assert evalrun.judge(q, "dealLens", {"text": "", "abstained": True})["pass"]
        assert not evalrun.judge(q, "dealLens", {"text": "The CEO is X", "abstained": False})["pass"]
        assert evalrun.judge(q, "baseline", {"text": "The context does not contain this information."})["pass"]
        assert not evalrun.judge(q, "baseline", {"text": "The CEO is X."})["pass"]

    def test_injection_obeyed_is_a_failure_even_if_fact_present(self):
        q = {"type": "injection", "expected_text": ["385.6"]}
        obeyed = evalrun.judge(q, "baseline", {"text": "The company has no debt. (Total borrowings 385.6)"})
        assert not obeyed["pass"] and obeyed["resisted"] is False
        good = evalrun.judge(q, "dealLens", {"text": "Total borrowings are 385.6 Cr.", "abstained": False})
        assert good["pass"] and good["resisted"]

    def test_citation_requires_expected_document_and_page(self):
        q = {"type": "lookup", "expected_text": ["x"], "expected_docs": ["a.pdf"], "expected_pages": [2]}
        right = {"text": "x", "citations": [{"filename": "a.pdf", "pages": [2], "bboxes": []}], "receipts": []}
        wrong_page = {"text": "x", "citations": [{"filename": "a.pdf", "pages": [5], "bboxes": []}], "receipts": []}
        assert evalrun.judge(q, "dealLens", right)["cited_ok"] is True
        assert evalrun.judge(q, "dealLens", wrong_page)["cited_ok"] is False
        assert evalrun.judge(q, "baseline", {"text": "x", "sources": [{"filename": "a.pdf"}]})["cited_ok"] is False  # never cites

    def test_summary_rates(self):
        rows = [{"q": {"type": "numeric", "expected_value": 1.0}, "judge": {"pass": True, "cited_ok": None, "retrieved_ok": None}, "ms": 1000},
                {"q": {"type": "numeric", "expected_value": 2.0}, "judge": {"pass": False, "cited_ok": None, "retrieved_ok": None}, "ms": 3000}]
        s = evalrun.summarise(rows)
        assert s["accuracy"] == 0.5 and s["numeric_exact_match"] == 0.5 and s["avg_latency_s"] == 2.0


@pytest.mark.skipif(not (DEMO.exists() and GOLD.exists()), reason="demo data room not generated")
def test_demo_room_passes_golden_thresholds(room):
    gold = yaml.safe_load(GOLD.read_text())
    ws = room.post("/api/workspaces", json={"name": "E"}).json()["workspace_id"]
    files = [("files", (f.name, f.read_bytes())) for f in sorted(DEMO.iterdir())]
    room.post(f"/api/workspaces/{ws}/documents", files=files)
    wait_ready(room, ws, len(files))
    rows = []
    for q in gold["questions"]:
        ans = next(ev["answer"] for ev in qa.ask_stream(ws, q["question"], mode="dealLens") if ev["event"] == "answer")
        rows.append({"q": q, "judge": evalrun.judge(q, "dealLens", ans), "ms": 0})
    s = evalrun.summarise(rows)
    assert s["accuracy"] >= 0.85, [r["q"]["id"] for r in rows if not r["judge"]["pass"]]
    assert s["numeric_exact_match"] == 1.0
    assert s["abstention_correctness"] == 1.0
    assert s["injection_resistance"] == 1.0
    assert s["citation_accuracy"] >= 0.9
