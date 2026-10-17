from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import replace

import pytest

from benchmark import store
from benchmark.dataset import dataset_fingerprint, discover_categories, resolve_categories
from benchmark.runner import BenchmarkRunner, RunError, git_info
from benchmark.sampling import sample_documents
from benchmark.tests.conftest import FakeExtract, FakeScore, fake_git


# ---- discovery, categories, sampling ---------------------------------------------------------
def test_categories_are_discovered_from_dataset(dataset):
    cats = discover_categories(dataset)
    assert list(cats) == ["alpha", "beta", "tables"]
    assert cats["tables"].jsonl == "table_tests.jsonl"
    assert cats["alpha"].pdf_count == 8  # orphan test row with no PDF on disk is ignored
    assert cats["alpha"].test_count == 16


def test_category_filtering(dataset):
    cats = discover_categories(dataset)
    assert resolve_categories("all", cats) == ["alpha", "beta", "tables"]
    assert resolve_categories(["all"], cats) == ["alpha", "beta", "tables"]
    assert resolve_categories(["tables", "alpha", "alpha"], cats) == ["alpha", "tables"]
    with pytest.raises(ValueError):
        resolve_categories(["alpha", "nope"], cats)


def test_sampling_is_deterministic_and_carries_test_ids(dataset):
    cats = discover_categories(dataset)
    a = sample_documents(cats, list(cats), 3, seed=7)
    b = sample_documents(cats, list(cats), 3, seed=7)
    c = sample_documents(cats, list(cats), 3, seed=8)
    assert a == b and a != c
    assert len(a) == 9 and len({d["pdf"] for d in a}) == 9  # no duplicates
    assert all(len(d["test_ids"]) == 2 for d in a)  # every sampled doc has official test cases


def test_sampling_is_independent_per_category_and_capped_at_available(dataset):
    cats = discover_categories(dataset)
    alone = sample_documents(cats, ["beta"], 3, 1)
    together = [d for d in sample_documents(cats, list(cats), 3, 1) if d["category"] == "beta"]
    assert alone == together
    assert len(sample_documents(cats, ["tables"], 50, 1)) == 3
    assert len(sample_documents(cats, list(cats), None, 1)) == 8 + 6 + 3  # full mode: everything


def test_dataset_fingerprint_changes_with_test_files(dataset):
    before = dataset_fingerprint(dataset)
    assert before == dataset_fingerprint(dataset)
    (dataset / "beta.jsonl").write_text((dataset / "beta.jsonl").read_text() + " ", encoding="utf-8")
    assert dataset_fingerprint(dataset) != before


# ---- run creation, git, isolation ------------------------------------------------------------
def test_create_run_records_config_manifest_identity_and_git(make_runner):
    r = make_runner()
    run_id = r.create_run(per_category=2, seed=5, timeout_s=60)
    st = r.status(run_id)
    assert run_id.startswith("qk-")
    assert st["status"] == "created" and st["is_official"] is False and st["score_kind"] == "quick_sample"
    assert st["git"]["sha"] == "a" * 40
    assert st["config"] == {"categories": ["alpha", "beta", "tables"], "per_category": 2, "seed": 5, "workers": 2,
                            "timeout_s": 60, "html_report": True}
    assert st["scoring_config"]["skip_baseline"] is True and st["sample_id"] and st["dataset_id"]
    docs = r.manifest(run_id)["documents"]
    assert len(docs) == 6 and st["totals"] == {"documents": 6, "test_cases": 12}
    rdir = store.run_dir(r.settings.workspace, run_id)
    kept = {json.loads(l)["pdf"] for l in (rdir / "scoring" / "alpha.jsonl").read_text().splitlines()}
    assert kept == {d["pdf"] for d in docs if d["category"] == "alpha"}
    # the same sample, seed and data always give the same sample identity
    assert r.status(r.create_run(per_category=2, seed=5))["sample_id"] == st["sample_id"]


@pytest.mark.parametrize("kwargs,code", [
    ({"mode": "nightly"}, "INVALID_MODE"),
    ({"per_category": 0}, "INVALID_SAMPLE_SIZE"),
    ({"per_category": 9999}, "INVALID_SAMPLE_SIZE"),
    ({"workers": 99}, "INVALID_WORKERS"),
    ({"timeout_s": 1}, "INVALID_TIMEOUT"),
    ({"categories": ["../etc"]}, "INVALID_CATEGORY"),
    ({"mode": "full"}, "FULL_NOT_CONFIRMED"),
    ({"mode": "full", "confirm_full": True, "categories": ["alpha"]}, "INVALID_CATEGORY"),
])
def test_create_run_validates_input(make_runner, kwargs, code):
    with pytest.raises(RunError) as err:
        make_runner().create_run(**kwargs)
    assert err.value.code == code


def test_full_mode_covers_everything_and_is_not_official_until_proven(make_runner):
    r = make_runner(extract=FakeExtract(fail_substr=None), score=FakeScore(valid=True))
    run_id = r.create_run(mode="full", confirm_full=True, categories="all")
    assert run_id.startswith("fl-")
    st = r.status(run_id)
    assert st["score_kind"] == "full_pending" and st["is_official"] is False and st["totals"]["documents"] == 17
    assert st["config"]["per_category"] is None and st["config"]["html_report"] is False  # no huge HTML by default
    final = r.execute(run_id)
    assert final["status"] == "completed" and final["score_kind"] == "full_official" and final["is_official"] is True
    assert r.results(run_id)["overall_score_pct"] == 62.5


def test_full_mode_with_a_missing_output_is_never_official(make_runner):
    r = make_runner(extract=FakeExtract(fail_substr="doc03"))
    run_id = r.create_run(mode="full", confirm_full=True)
    final = r.execute(run_id)
    res = r.results(run_id)
    assert final["is_official"] is False and res["score_kind"] == "full_incomplete" and res["overall_score_pct"] is None
    assert "not an official score" in res["score_label"]


def test_workspace_cannot_overlap_official_folders(settings):
    for bad in (settings.data_dir / "runs", settings.data_dir.parent, settings.resolved_import_root() / "mine",
                settings.resolved_import_root().parent):
        with pytest.raises(ValueError):
            BenchmarkRunner(replace(settings, workspace=bad))


def test_run_id_cannot_escape_workspace(make_runner):
    for bad in ["../x", "..", "qk-1", "/etc/passwd", "qk-20260101T000000Z-zzzzzzzz", "xx-20260101T000000Z-aaaaaaaa"]:
        with pytest.raises(RunError):
            make_runner().status(bad)


def test_git_info_reads_real_revision(tmp_path):
    repo = tmp_path / "g"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    run("init", "-q")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    info = git_info(repo)
    assert info["sha"] == sha and info["dirty"] is False
    (repo / "f.txt").write_text("dirty")
    assert git_info(repo)["dirty"] is True


def test_checkpoint_log_tolerates_a_torn_last_line(tmp_path):
    path = tmp_path / "docs.jsonl"
    store.append_jsonl(path, {"pdf": "a", "status": "done"})
    store.append_jsonl(path, {"pdf": "a", "status": "failed"})  # later record wins
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"pdf": "b", "stat')  # crash mid-write
    assert store.read_jsonl_last(path, "pdf") == {"a": {"pdf": "a", "status": "failed"}}


# ---- execution, validity, progress, failures, reports ----------------------------------------
def test_clean_run_gets_an_official_formula_sample_score(make_runner):
    r = make_runner(extract=FakeExtract(fail_substr=None))
    run_id = r.create_run(per_category=2, seed=1)
    final = r.execute(run_id)
    assert final["status"] == "completed"
    res = r.results(run_id)
    assert res["validity"] == "valid" and res["completeness"] == "complete"
    assert res["overall_score_pct"] == 62.5 and res["is_official"] is False and res["score_kind"] == "quick_sample"
    assert "not a full official score" in res["score_label"]
    assert res["categories"]["alpha"] == {"pass_rate_pct": 50.0, "passed": 5, "total": 10, "basis": "official"}
    assert res["timing"]["per_page_ms"]["median"] == 100.0  # 200 ms / 2 pages
    assert res["has_report"] is True and r.report_path(run_id).read_text() == "<html>report</html>"
    assert (store.run_dir(r.settings.workspace, run_id) / "outcomes.jsonl").exists()


def test_failed_documents_make_the_result_invalid_and_diagnostic(make_runner):
    r = make_runner(extract=FakeExtract(fail_substr="doc03"))
    run_id = r.create_run(per_category=8, seed=1, categories=["alpha"])  # includes doc03
    assert r.execute(run_id)["status"] == "completed"
    st = r.status(run_id)
    assert st["counts"] == {"total": 8, "done": 7, "failed": 1, "timed_out": 0, "empty": 0, "pending": 0}
    assert st["failures"][0]["pdf"] == "alpha/doc03.pdf" and st["failures"][0]["error"] == "RuntimeError: boom"
    res = r.results(run_id)
    assert res["validity"] == "invalid" and res["completeness"] == "incomplete"
    assert res["overall_score_pct"] is None and res["diagnostic_score_pct"] is not None
    assert res["categories"]["alpha"]["basis"] == "diagnostic"
    assert res["cases"]["missing_output"] == 2 and any("diagnostic" in n for n in res["notes"])
    assert res["scorer_error_count"] == 2 and res["is_official"] is False
    assert "FAILED alpha/doc03.pdf" in r.log_tail(run_id)


def test_empty_outputs_are_counted_but_still_scored(make_runner):
    r = make_runner(extract=FakeExtract(fail_substr=None, empty_substr="doc00"))
    run_id = r.create_run(per_category=8, categories=["alpha"])
    r.execute(run_id)
    assert r.status(run_id)["counts"]["empty"] == 1
    res = r.results(run_id)
    assert res["documents"]["empty"] == 1 and any("empty" in n for n in res["notes"])
    assert res["timing"]["documents"]["alpha/doc00.pdf"]["empty"] is True


def test_extraction_exception_is_recorded_not_raised(make_runner):
    def explode(*_):
        raise MemoryError("oom")

    r = make_runner(extract=explode)
    run_id = r.create_run(per_category=2, categories=["beta"])
    assert r.execute(run_id)["status"] == "completed"
    assert r.status(run_id)["counts"]["failed"] == 2


def test_scorer_crash_marks_run_failed_but_keeps_extraction(make_runner):
    r = make_runner(extract=FakeExtract(fail_substr=None), score=FakeScore(returncode=2))
    run_id = r.create_run(per_category=2, categories=["beta"])
    assert r.execute(run_id)["status"] == "failed"
    st = r.status(run_id)
    assert "scorer exited" in st["error"] and st["counts"]["done"] == 2 and st["resumable"]


def test_resume_skips_finished_documents_and_retries_failed(make_runner):
    ex = FakeExtract(fail_substr="doc03")
    r = make_runner(extract=ex)
    run_id = r.create_run(per_category=8, categories=["alpha"])
    r.execute(run_id)
    first = len(ex.calls)
    assert first == 8
    r._set_status(store.run_dir(r.settings.workspace, run_id), "failed", error="x")
    ex.fail_substr = None
    assert r.execute(run_id)["status"] == "completed"
    assert ex.calls[first:] == ["alpha/doc03.pdf"]
    assert r.status(run_id)["counts"]["failed"] == 0 and r.results(run_id)["validity"] == "valid"
    with pytest.raises(RunError) as err:
        r.execute(run_id)
    assert err.value.code == "RUN_COMPLETED"


def test_resume_after_crash_reports_interrupted_and_picks_up(make_runner):
    r = make_runner()
    run_id = r.create_run(per_category=3, categories=["alpha"])
    rdir = store.run_dir(r.settings.workspace, run_id)
    first = r.manifest(run_id)["documents"][0]["pdf"]
    store.append_jsonl(rdir / "docs.jsonl", {"pdf": first, "category": "alpha", "status": "done", "pages": 2,
                                             "total_ms": 10.0, "per_page_ms": 5.0})
    with (rdir / "docs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"pdf": "torn')  # the crash happened mid-write
    r._set_status(rdir, "running")  # simulate a crash: status stuck on running, no live lock
    assert r.status(run_id)["status"] == "interrupted" and r.status(run_id)["resumable"]
    ex = FakeExtract(fail_substr=None)
    r.extract_fn = ex
    assert r.execute(run_id)["status"] == "completed"
    assert first not in ex.calls and len(ex.calls) == 2


def test_cancel_stops_queued_work_and_run_is_resumable(make_runner):
    gate = threading.Event()
    ex = FakeExtract(fail_substr=None, gate=gate)
    r = make_runner(extract=ex)
    run_id = r.create_run(per_category=8, categories=["alpha"], workers=1)
    cancel = threading.Event()
    out = {}
    t = threading.Thread(target=lambda: out.update(r.execute(run_id, cancel)))
    t.start()
    while not ex.calls:
        time.sleep(0.01)
    cancel.set()
    gate.set()
    t.join(30)
    assert out["status"] == "cancelled"
    counts = r.status(run_id)["counts"]
    assert counts["done"] < 8 and counts["pending"] > 0 and r.status(run_id)["resumable"]
    assert r.execute(run_id)["status"] == "completed"  # resume finishes the rest
    assert r.status(run_id)["counts"]["done"] == 8


def test_low_memory_refuses_real_extraction_but_not_mocked(make_runner, settings):
    r = BenchmarkRunner(settings, score_fn=FakeScore(), git_fn=fake_git, memory_fn=lambda: 100)  # real pool path
    run_id = r.create_run(per_category=1, categories=["beta"])
    with pytest.raises(RunError) as err:
        r.execute(run_id)
    assert err.value.code == "LOW_MEMORY" and r.status(run_id)["status"] == "created"


# ---- concurrency -----------------------------------------------------------------------------
def test_concurrent_runs_are_blocked(make_runner):
    gate = threading.Event()
    ex = FakeExtract(fail_substr=None, gate=gate)
    r = make_runner(extract=ex)
    a = r.create_run(per_category=2, categories=["alpha"])
    b = r.create_run(per_category=2, categories=["beta"])
    t = threading.Thread(target=lambda: r.execute(a))
    t.start()
    while not ex.calls:
        time.sleep(0.01)
    with pytest.raises(RunError) as err:
        r.execute(b)
    assert err.value.code == "RUN_CONFLICT"
    gate.set()
    t.join(30)
    assert r.execute(b)["status"] == "completed"  # lock released after the first finished


def test_stale_lock_is_taken_over(make_runner):
    r = make_runner()
    store.atomic_write_json(r.lock.path, {"run_id": "qk-20260101T000000Z-deadbeef", "heartbeat": time.time() - 10_000})
    run_id = r.create_run(per_category=1, categories=["beta"])
    assert r.execute(run_id)["status"] == "completed"


def test_history_carries_identity_scores_and_timing(make_runner):
    r = make_runner(extract=FakeExtract(fail_substr=None))
    a = r.create_run(per_category=2, seed=1)
    r.execute(a)
    b = r.create_run(per_category=2, seed=1)
    r.execute(b)
    hist = {h["run_id"]: h for h in r.history()}
    assert set(hist) == {a, b} and all(h["is_official"] is False for h in hist.values())
    h = hist[a]
    assert h["git"]["sha"] == "a" * 40 and h["mode"] == "quick" and h["sample_id"] and h["dataset_id"]
    assert h["category_scores"]["alpha"] == 50.0 and h["median_ms_per_page"] == 100.0 and h["validity"] == "valid"
    assert h["elapsed_s"] >= 0 and h["created_at"]


def test_logs_and_errors_do_not_leak_paths(make_runner):
    r = make_runner()
    run_id = r.create_run(per_category=1, categories=["beta"])
    r.execute(run_id)
    log = r.log_tail(run_id)
    assert str(r.settings.workspace) not in log and str(r.settings.data_dir) not in log


def test_a_failure_caused_by_cancel_stays_pending_instead_of_failed(make_runner):
    """A killed in-flight document reports an error; because cancel is set it must not become a recorded failure."""
    cancel = threading.Event()

    def killed_by_cancel(pdf, rel, candidate):
        cancel.set()
        return {"success": False, "pages": 0, "error": "WorkerCrashed: extraction process exited (code 1)"}

    r = make_runner(extract=killed_by_cancel)
    run_id = r.create_run(per_category=2, categories=["beta"], workers=1)
    assert r.execute(run_id, cancel)["status"] == "cancelled"
    counts = r.status(run_id)["counts"]
    assert counts["failed"] == 0 and counts["pending"] == 2 and r.status(run_id)["resumable"]
