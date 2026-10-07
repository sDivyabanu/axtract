"""DealLens core: chunking, fusion, verification, isolation, ingestion and grounded answers."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app  # noqa: E402
from models.document import BlockType, DocumentBlock  # noqa: E402
from rag import chunker, config, db, index, llm, qa, verifier  # noqa: E402
from services import preview_service  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[2] / "sample_files"


@pytest.fixture()
def room(tmp_path, monkeypatch):
    """An isolated data directory + extractive mode (no LLM) for deterministic tests."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "FILES_DIR", tmp_path / "files")
    monkeypatch.setattr(preview_service, "PERSISTENT_PREVIEW_ROOT", tmp_path / "previews")
    monkeypatch.setattr(db, "_initialised", set())
    monkeypatch.setattr(llm, "status", lambda force=False: {"available": False, "model": "x", "reason": "test"})
    index._cache.clear()
    return TestClient(app, raise_server_exceptions=False)


def wait_ready(client: TestClient, ws: str, n: int, timeout: float = 240) -> list[dict]:
    t = time.time()
    while time.time() - t < timeout:
        docs = client.get(f"/api/workspaces/{ws}").json()["documents"]
        if len(docs) >= n and all(d["status"] in ("ready", "failed") for d in docs):
            return docs
        time.sleep(1)
    raise AssertionError("indexing timed out")


def upload(client, ws, *names):
    files = [("files", (n, (SAMPLES / n).read_bytes())) for n in names]
    return client.post(f"/api/workspaces/{ws}/documents", files=files).json()


def ask(client, ws, question, **kw):
    ans = None
    with client.stream("POST", f"/api/workspaces/{ws}/ask", json={"question": question, **kw}) as r:
        ev = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:") and ev == "answer":
                ans = json.loads(line[5:])["answer"]
    assert ans is not None
    return ans


def block(i, type_, content, **kw):
    return DocumentBlock(id=f"b{i}", type=type_, content=content, page=kw.pop("page", 1), extractor="t",
                         reading_order=i, bbox=kw.pop("bbox", (0.1, 0.1, 0.9, 0.2)), **kw)


class TestChunker:
    def test_tables_are_never_split_and_sections_respect_headings(self):
        rows = [["Item", "FY2025"]] + [[f"Line {i}", f"{i * 10}"] for i in range(120)]
        md = "\n".join("| " + " | ".join(r) + " |" for r in rows)
        blocks = [
            block(0, BlockType.HEADING, "Borrowings", metadata={"heading_level": 1}),
            block(1, BlockType.PARAGRAPH, "Amounts are in ₹ crore. " + "word " * 60),
            block(2, BlockType.TABLE, md, metadata={"rows": rows, "markdown": md}),
            block(3, BlockType.HEADING, "Risks", metadata={"heading_level": 1}),
            block(4, BlockType.PARAGRAPH, "Risk text. " * 20),
        ]
        built = chunker.build_chunks(blocks, "financial_statement", "f.pdf")
        tables = [c for c in built.chunks if c.kind == "table"]
        assert len(tables) == 1 and tables[0].text.count("\n") >= 119          # one chunk, all rows
        assert [c for c in built.chunks if c.kind == "table_summary"]
        sections = [c for c in built.chunks if c.kind == "section"]
        assert sections[0].heading_path == ["Borrowings"] and sections[-1].heading_path == ["Risks"]
        assert built.tables[0].unit == "crore" and built.tables[0].scale == 1e7 and built.tables[0].currency == "INR"
        assert len(built.tables[0].grid["rows"]) == 120

    def test_long_paragraph_splits_on_sentences_only(self):
        text = " ".join(f"This is sentence number {i} about the facility." for i in range(200))
        built = chunker.build_chunks([block(0, BlockType.PARAGRAPH, text)], "other", "f.pdf")
        assert len(built.chunks) > 1
        assert all(c.text.rstrip().endswith(".") for c in built.chunks)

    def test_header_footer_not_indexed_and_printed_page_recorded(self):
        blocks = [block(0, BlockType.PARAGRAPH, "Body text here."),
                  block(1, BlockType.FOOTER, "F-3", bbox=(0.4, 0.95, 0.6, 0.98))]
        built = chunker.build_chunks(blocks, "other", "f.pdf")
        assert all("F-3" not in c.text for c in built.chunks)
        assert built.chunks[0].printed_pages == ["F-3"]

    def test_flags_and_confidence_propagate(self):
        b = block(0, BlockType.PARAGRAPH, "Scanned line", confidence=0.41, requires_review=True)
        b.extractor = "rapidocr"
        c = chunker.build_chunks([b], "other", "f.pdf").chunks[0]
        assert c.min_confidence == 0.41 and {"needs_review", "low_ocr_confidence"} <= set(c.flags)


class TestFusionAndVerifier:
    def test_rrf_prefers_items_ranked_by_both_lists(self):
        fused = index.rrf_fuse([["a", "b", "c"], ["c", "b", "d"]])
        order = [k for k, _ in fused]
        assert set(order[:2]) == {"b", "c"} and set(order[2:]) == {"a", "d"}   # in both lists beats in one

    def test_verifier_catches_an_invented_number(self):
        src = {1: "Term Loan A: Rs 50.0 crore matures in 2026."}
        assert verifier.check_sentence("Term Loan A of 50.0 crore matures in 2026.", [1], src).verified
        bad = verifier.check_sentence("Term Loan A of 55.0 crore matures in 2026.", [1], src)
        assert not bad.verified and "55.0" in bad.reason
        assert not verifier.check_sentence("The company has no debt.", [], src).verified

    def test_route_classifier(self):
        assert qa.classify_route("What is the total debt maturing in 2026?") == "numeric"
        assert qa.classify_route("Compare revenue in the CIM versus the audited accounts") == "compare"
        assert qa.classify_route("List the parties to the loan agreement") == "list"
        assert qa.classify_route("Who signed the agreement?") == "lookup"


class TestIngestAndAnswer:
    def test_end_to_end_with_citations_and_abstention(self, room):
        ws = room.post("/api/workspaces", json={"name": "T"}).json()["workspace_id"]
        up = upload(room, ws, "01_cross_page_table.pdf", "04_charts.pdf")
        assert len(up["documents"]) == 2 and not up["rejected"]
        docs = wait_ready(room, ws, 2)
        assert all(d["status"] == "ready" and d["chunk_count"] > 0 for d in docs)

        a = ask(room, ws, "What is the TOTAL for FY2024 in the revenue table?")
        assert not a["abstained"] and a["citations"] and a["mode"] == "extractive"
        cite = a["citations"][0]
        assert cite["doc_id"] in {d["doc_id"] for d in docs} and cite["bboxes"] and cite["bboxes"][0]["bbox"]
        assert "27,725,540" in a["text"] or any("27,725,540" in c["snippet"] for c in a["citations"])
        assert a["grounding"]["total"] > 0 and a["glass_box"]["retrieved"] and a["stages"]

        n = ask(room, ws, "Who is the chief executive officer and where is the head office?")
        assert n["abstained"] and n["text"].startswith("Not found") and n["searched"]["documents"]

    def test_duplicate_upload_is_reused(self, room):
        ws = room.post("/api/workspaces", json={"name": "T"}).json()["workspace_id"]
        upload(room, ws, "03_financial_number_formats.pdf")
        wait_ready(room, ws, 1)
        again = upload(room, ws, "03_financial_number_formats.pdf")
        assert again["documents"][0]["duplicate"] is True
        assert len(room.get(f"/api/workspaces/{ws}").json()["documents"]) == 1

    def test_workspace_isolation(self, room):
        a = room.post("/api/workspaces", json={"name": "A"}).json()["workspace_id"]
        b = room.post("/api/workspaces", json={"name": "B"}).json()["workspace_id"]
        upload(room, a, "01_cross_page_table.pdf")
        upload(room, b, "05_equations.pdf")
        da, db_ = wait_ready(room, a, 1), wait_ready(room, b, 1)
        ids_a = {d["doc_id"] for d in da}
        ids_b = {d["doc_id"] for d in db_}
        for term in ("revenue table total", "equation extraction test", "quadratic formula"):
            for ws, own in ((a, ids_a), (b, ids_b)):
                hits = index.search(ws, term)
                assert all(index.chunk_row(ws, h.chunk_id)["doc_id"] in own for h in hits)
        # a document of A is not addressable through B
        r = room.get(f"/api/workspaces/{b}/documents/{next(iter(ids_a))}")
        assert r.status_code == 404

    def test_rejected_files_are_reported_not_fatal(self, room):
        ws = room.post("/api/workspaces", json={"name": "T"}).json()["workspace_id"]
        files = [("files", ("notes.txt", b"hello")), ("files", ("fake.pdf", b"not a pdf")),
                 ("files", ("03_financial_number_formats.pdf", (SAMPLES / "03_financial_number_formats.pdf").read_bytes()))]
        up = room.post(f"/api/workspaces/{ws}/documents", files=files).json()
        assert len(up["documents"]) == 1
        assert {r["code"] for r in up["rejected"]} == {"UNSUPPORTED_FORMAT", "INVALID_FILE"}

    def test_unknown_workspace_is_404(self, room):
        assert room.get("/api/workspaces/" + "0" * 32).status_code == 404
