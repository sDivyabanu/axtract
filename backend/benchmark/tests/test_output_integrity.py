"""Partial, missing and empty outputs must never be reported as complete (integration review blocker 5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from benchmark import store
from benchmark.runner import CANDIDATE, RunError
from benchmark.tests.conftest import FakeExtract


class PagesExtract(FakeExtract):
    """Writes only the pages listed per document substring: {"doc01": 1} writes page 1 of 2 and claims success."""

    def __init__(self, written: dict[str, int] | None = None, claim_success_without_files: str | None = None, **kw):
        super().__init__(fail_substr=None, **kw)
        self.written = written or {}
        self.claim = claim_success_without_files
        self.partial_flag = True

    def __call__(self, pdf: Path, rel: Path, candidate_dir: Path) -> dict:
        with self._lock:
            self.calls.append(str(rel).replace("\\", "/"))
        out = candidate_dir / rel.parent
        out.mkdir(parents=True, exist_ok=True)
        if self.claim and self.claim in rel.name:
            return {"success": True, "pages": 2, "total_elapsed_ms": 10.0}  # lies: nothing on disk
        n = next((v for k, v in self.written.items() if k in rel.name), 2)
        for pg in range(1, n + 1):
            (out / f"{rel.stem}_pg{pg}_repeat1.md").write_text("md", encoding="utf-8")
        meta = {"success": n > 0, "pages": 2, "total_elapsed_ms": 10.0}
        if 0 < n < 2 and self.partial_flag:
            meta["partial"] = True
        if n == 0:
            meta["error"] = "EMPTY_OUTPUT: No readable content extracted from page(s)"
        return meta


def stems(r, run_id):
    return [Path(d["pdf"]).stem for d in r.manifest(run_id)["documents"]]


def run_quick(make_runner, build):
    """`build(stems)` returns the extractor; document names come from the seeded sample, never hard-coded."""
    r = make_runner()
    run_id = r.create_run(per_category=3, categories=["alpha"])
    r.extract_fn = build(stems(r, run_id))
    return r, run_id, r.execute(run_id)


def doc_records(r, run_id):
    return store.read_jsonl_last(store.run_dir(r.settings.workspace, run_id) / "docs.jsonl", "pdf")


def test_missing_pages_make_a_document_partial_and_the_run_incomplete(make_runner):
    r, run_id, run = run_quick(make_runner, lambda n: PagesExtract({n[0]: 1}))
    recs = doc_records(r, run_id)
    partial = [v for v in recs.values() if v["status"] == "partial"]
    assert len(partial) == 1 and partial[0]["missing_pages"] == 1 and partial[0]["written_pages"] == 1
    first = stems(r, run_id)[0]
    assert "1 of 2 page(s) have no output" in partial[0]["error"]
    st = r.status(run_id)
    assert st["counts"]["partial"] == 1 and st["counts"]["done"] == 2 and st["counts"]["pending"] == 0
    assert st["failure_count"] == 1 and st["failures"][0]["pdf"].endswith(first + ".pdf")
    res = r.results(run_id)
    assert res["completeness"] == "incomplete" and res["documents"]["partial"] == 1
    assert any("missing output for some pages" in n for n in res["notes"])
    assert run["completeness"] == "incomplete" and st["completeness"] == "incomplete"


def test_incomplete_run_is_resumable_and_completes_after_the_retry(make_runner):
    r, run_id, _ = run_quick(make_runner, lambda n: PagesExtract({n[0]: 1}))
    first = stems(r, run_id)[0]
    assert r.status(run_id)["resumable"] is True
    retry = PagesExtract({})  # every page is written this time
    r.extract_fn = retry
    assert r.execute(run_id)["status"] == "completed"
    assert len(retry.calls) == 1 and retry.calls[0].endswith(first + ".pdf")  # only the partial document
    st = r.status(run_id)
    assert st["counts"]["partial"] == 0 and st["counts"]["done"] == 3
    assert r.results(run_id)["completeness"] == "complete" and st["resumable"] is False
    with pytest.raises(RunError) as err:
        r.execute(run_id)
    assert err.value.code == "RUN_COMPLETED"


def test_extractor_claiming_success_without_files_is_a_failure_not_done(make_runner):
    r, run_id, _ = run_quick(make_runner, lambda n: PagesExtract(claim_success_without_files=n[1]))
    rec = next(v for k, v in doc_records(r, run_id).items() if k.endswith(stems(r, run_id)[1] + ".pdf"))
    assert rec["status"] == "failed" and rec["empty"] is True and "no output" in rec["error"]
    c = r.status(run_id)["counts"]
    assert c["failed"] == 1 and c["empty"] == 1 and c["done"] == 2
    assert r.results(run_id)["completeness"] == "incomplete"


def test_empty_output_is_counted_and_never_done(make_runner):
    r, run_id, _ = run_quick(make_runner, lambda n: PagesExtract({n[0]: 0}))
    c = r.status(run_id)["counts"]
    assert c["empty"] == 1 and c["failed"] == 1 and c["done"] == 2
    assert r.results(run_id)["documents"]["empty"] == 1


def test_extraction_errors_with_every_page_written_are_degraded_not_incomplete(make_runner):
    class Degraded(PagesExtract):
        def __call__(self, pdf, rel, cand):
            meta = super().__call__(pdf, rel, cand)
            meta["partial"], meta["error"] = True, "REGION_ROUTING_TIMEOUT: skipped"
            return meta

    r, run_id, _ = run_quick(make_runner, lambda n: Degraded())
    c = r.status(run_id)["counts"]
    assert c["done"] == 3 and c["degraded"] == 3 and c["partial"] == 0
    assert r.results(run_id)["completeness"] == "complete"


def test_resume_reextracts_a_document_whose_output_files_vanished(make_runner):
    r, run_id, _ = run_quick(make_runner, lambda n: PagesExtract())
    rdir = store.run_dir(r.settings.workspace, run_id)
    victim = r.manifest(run_id)["documents"][0]["pdf"]
    for f in (rdir / "scoring" / CANDIDATE / Path(victim).parent).glob(f"{Path(victim).stem}_pg*"):
        f.unlink()
    r._set_status(rdir, "running")  # crash after the files were lost, checkpoint still says done
    retry = PagesExtract()
    r.extract_fn = retry
    assert r.execute(run_id)["status"] == "completed"
    assert retry.calls == [victim]
    assert (rdir / "scoring" / CANDIDATE / Path(victim).parent / f"{Path(victim).stem}_pg2_repeat1.md").is_file()
    assert "re-extracting" in r.log_tail(run_id)


def test_retry_does_not_inherit_pages_from_the_earlier_attempt(make_runner):
    r, run_id, _ = run_quick(make_runner, lambda n: PagesExtract())  # attempt 1 writes both pages
    rdir = store.run_dir(r.settings.workspace, run_id)
    victim = r.manifest(run_id)["documents"][1]["pdf"]
    records = store.read_jsonl_last(rdir / "docs.jsonl", "pdf")
    store.append_jsonl(rdir / "docs.jsonl", {**records[victim], "status": "failed", "error": "TimeoutError"})
    r._set_status(rdir, "running")
    retry = PagesExtract({Path(victim).stem: 1})  # the retry only manages page 1
    r.extract_fn = retry
    r.execute(run_id)
    rec = doc_records(r, run_id)[victim]
    assert rec["status"] == "partial" and rec["written_pages"] == 1  # page 2 from attempt 1 was cleared first
    assert r.results(run_id)["completeness"] == "incomplete"


def test_full_mode_with_partial_documents_is_never_an_official_score(make_runner):
    r = make_runner()
    run_id = r.create_run(mode="full", categories="all", confirm_full=True)
    r.extract_fn = PagesExtract({stems(r, run_id)[0]: 1})
    r.execute(run_id)
    res = r.results(run_id)
    assert res["is_official"] is False and res["score_kind"] == "full_incomplete"
