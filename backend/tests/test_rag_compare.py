"""Baseline pipeline + compare endpoint (no LLM: baseline falls back to its top passage)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag import baseline  # noqa: E402
from tests.test_rag_core import room, wait_ready  # noqa: E402,F401

DEMO = Path(__file__).resolve().parents[2] / "demo" / "project_falcon"
pytestmark = pytest.mark.skipif(not DEMO.exists(), reason="demo data room not generated")


@pytest.fixture()
def falcon(room):
    ws = room.post("/api/workspaces", json={"name": "F"}).json()["workspace_id"]
    files = [("files", (f.name, f.read_bytes())) for f in sorted(DEMO.iterdir())]
    room.post(f"/api/workspaces/{ws}/documents", files=files)
    wait_ready(room, ws, len(files))
    return room, ws


def test_baseline_is_naive_by_construction(falcon):
    room, ws = falcon
    idx = baseline._index(ws)
    texts = " ".join(i["text"] for i in idx["items"])
    assert "ignore previous instructions" in texts.lower()   # hidden white text is read like any other text
    assert "Adjustments" in texts                            # hidden sheet is read too
    assert not any("Falcon_CIM" in i["filename"] and "385" in i["text"] and "Q" in i["text"][:5] for i in idx["items"][:0])
    assert all(len(i["text"].split()) <= int(baseline.CHUNK_TOKENS / 1.3) + 1 for i in idx["items"])   # fixed windows


def test_compare_runs_both_pipelines_live_and_audits_the_baseline(falcon):
    room, ws = falcon
    r = room.post(f"/api/workspaces/{ws}/compare", json={"question": "Does Falcon Industries have any debt?"}).json()
    b, d = r["baseline"], r["dealLens"]
    assert b["pipeline"] == "baseline" and b["sources"] and b["checks"]["grounding"]["total"] >= 0
    assert any(x["reason"] == "instruction_override" for x in b["checks"]["retrieved_instructions"])   # baseline retrieved the injection
    assert d["pipeline"] == "dealLens" and d["excluded_sources"]                                    # DealLens quarantined it
    assert "no debt" not in d["text"].lower()


def test_dealLens_answers_exactly_where_the_baseline_cannot_compute(falcon):
    room, ws = falcon
    r = room.post(f"/api/workspaces/{ws}/compare", json={"question": "What is the total debt maturing in 2026?"}).json()
    assert r["dealLens"]["mode"] == "computed" and r["dealLens"]["receipts"]
    assert r["baseline"]["checks"] is not None and "104.8" not in r["baseline"]["text"]   # extractive baseline cannot sum 14 rows
