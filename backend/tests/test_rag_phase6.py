"""Phase 6: evidence pack, correction ripple, audit log, suggestions, confidence blocks."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag import qa  # noqa: E402
from tests.test_rag_core import room, wait_ready  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demo" / "project_falcon"


@pytest.mark.skipif(not DEMO.exists(), reason="demo data room not generated")
class TestPhase6:
    @pytest.fixture()
    def falcon(self, room):
        ws = room.post("/api/workspaces", json={"name": "F"}).json()["workspace_id"]
        files = [("files", (f.name, f.read_bytes())) for f in sorted(DEMO.iterdir())]
        room.post(f"/api/workspaces/{ws}/documents", files=files)
        wait_ready(room, ws, len(files))
        return room, ws

    def _answer(self, ws, question):
        return next(ev["answer"] for ev in qa.ask_stream(ws, question, mode="dealLens") if ev["event"] == "answer")

    def test_evidence_pack_is_a_pdf_with_hashes(self, falcon):
        room, ws = falcon
        a = self._answer(ws, "What is the total debt maturing in 2026?")
        r = room.post(f"/api/answers/{a['answer_id']}/evidence-pack")
        assert r.status_code == 200 and r.content.startswith(b"%PDF")
        import hashlib

        assert r.headers["X-Evidence-Pack-SHA256"] == hashlib.sha256(r.content).hexdigest()
        assert len(r.headers["X-Evidence-Pack-Manifest-SHA256"]) == 64
        assert room.post("/api/answers/nope/evidence-pack").status_code == 404

    def test_audit_log_has_ids_not_text(self, falcon):
        room, ws = falcon
        a = self._answer(ws, "Who is the lender under the facility agreement?")
        room.post(f"/api/answers/{a['answer_id']}/evidence-pack")
        events = room.get(f"/api/workspaces/{ws}/audit").json()
        kinds = {e["event"] for e in events}
        assert "evidence_pack" in kinds
        blob = str(events)
        assert "Meridian" not in blob and "Ignore previous" not in blob

    def test_suggestions_per_document(self, falcon):
        room, ws = falcon
        s = room.get(f"/api/workspaces/{ws}/suggestions").json()
        assert s and all(d["suggestions"] for d in s)
        assert any("maturing" in q["question"] for d in s for q in d["suggestions"])

    def test_confidence_blocks_have_boxes(self, falcon):
        room, ws = falcon
        docs = room.get(f"/api/workspaces/{ws}").json()["documents"]
        pdf = next(d for d in docs if d["filename"].endswith("Debt_Schedule.pdf"))
        blocks = room.get(f"/api/workspaces/{ws}/documents/{pdf['doc_id']}/blocks?page=1").json()
        assert blocks and all(len(b["bbox"]) == 4 and b["page"] == 1 for b in blocks)

    def test_correction_ripple_resolves_total_mismatch_and_audits(self, falcon):
        room, ws = falcon
        from rag import diligence

        issue = diligence.total_checks(ws)[0]
        target = None
        for t in room.get(f"/api/workspaces/{ws}/tables").json():
            if t["doc_id"] != issue["doc_id"]:
                continue
            for r in t["rows"]:
                if r["is_total"]:
                    for i, c in enumerate(r["cells"]):
                        if i and c["raw"] and abs(float(str(c["raw"]).replace(",", "")) - issue["stated"]) < 1e-9:
                            target = (t["table_id"], r["ridx"], i, c["raw"])
        assert target, "expected the mismatching total cell"
        res = room.post(f"/api/workspaces/{ws}/corrections", json={"table_id": target[0], "row": target[1], "col": target[2], "value": "452.0"}).json()
        assert res["old"] == target[3] and res["new"] == "452.0"
        assert len(res["ripple"]["total_checks_resolved"]) == 1
        # persisted + audited, source untouched
        after = room.get(f"/api/workspaces/{ws}/tables").json()
        assert any(c["corrected"] for t in after for r in t["rows"] for c in r["cells"])
        assert any(e["event"] == "correction" for e in room.get(f"/api/workspaces/{ws}/audit").json())

    def test_correction_rejects_non_numbers_and_unknown_cells(self, falcon):
        room, ws = falcon
        t = room.get(f"/api/workspaces/{ws}/tables").json()[0]
        assert room.post(f"/api/workspaces/{ws}/corrections", json={"table_id": t["table_id"], "row": 0, "col": 1, "value": "abc"}).status_code == 422
        assert room.post(f"/api/workspaces/{ws}/corrections", json={"table_id": t["table_id"], "row": 999, "col": 1, "value": "1"}).status_code == 404
        assert room.post(f"/api/workspaces/{ws}/corrections", json={"table_id": "x", "row": 0, "col": 1, "value": "1"}).status_code == 404
