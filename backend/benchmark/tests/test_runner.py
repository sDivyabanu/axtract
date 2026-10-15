from __future__ import annotations

import json
import subprocess
import threading
import time

import pytest

from benchmark import store
from benchmark.dataset import discover_categories, resolve_categories
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
    alone = [d for d in sample_documents(cats, ["beta"], 3, 1)]
    together = [d for d in sample_documents(cats, list(cats), 3, 1) if d["category"] == "beta"]
    assert alone == together
    assert len([d for d in sample_documents(cats, ["tables"], 50, 1)]) == 3


# ---- run creation, git, isolation ------------------------------------------------------------
def test_create_run_records_config_manifest_and_git(make_runner):
    r = make_runner()
    run_id = r.create_run(per_category=2, seed=5)
    st = r.status(run_id)
    assert st["status"] == "created" and st["is_official"] is False and st["score_kind"] == "quick_sample"
    assert st["git"]["sha"] == "a" * 40
    assert st["config"] == {"categories": ["alpha", "beta", "tables"], "per_category": 2, "seed": 5, "workers": 2}
    docs = r.manifest(run_id)["documents"]
    assert len(docs) == 6 and st["totals"] == {"documents": 6, "test_cases": 12}
    # scoring tree holds only the sampled documents and only their test rows
    rdir = store.run_dir(r.settings.workspace, run_id)
    kept = {json.loads(l)["pdf"] for l in (rdir / "scoring" / "alpha.jsonl").read_text().splitlines()}
    assert kept == {d["pdf"] for d in docs if d["category"] == "alpha"}


@pytest.mark.parametrize("kwargs,code", [
    ({"mode": "full"}, "INVALID_MODE"),
    ({"per_category": 0}, "INVALID_SAMPLE_SIZE"),
    ({"per_category": 9999}, "INVALID_SAMPLE_SIZE"),
    ({"workers": 99}, "INVALID_WORKERS"),
    ({"categories": ["../etc"]}, "INVALID_CATEGORY"),
])
def test_create_run_validates_input(make_runner, kwargs, code):
    with pytest.raises(RunError) as err:
        make_runner().create_run(**kwargs)
    assert err.value.code == code


def test_workspace_cannot_overlap_official_data(settings):
    from dataclasses import replace

    with pytest.raises(ValueError):
        BenchmarkRunner(replace(settings, workspace=settings.data_dir / "runs"))
    with pytest.raises(ValueError):
        BenchmarkRunner(replace(settings, workspace=settings.data_dir.parent))


def test_run_id_cannot_escape_workspace(make_runner):
    for bad in ["../x", "..", "qk-1", "/etc/passwd", "qk-20260101T000000Z-zzzzzzzz"]:
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


# ---- execution, progress, failures, reports --------------------------------------------------
def test_execute_tracks_progress_failures_timing_and_reports(make_runner):
    ex = FakeExtract(fail_substr="doc03")
    r = make_runner(extract=ex)
    run_id = r.create_run(per_category=8, seed=1, categories=["alpha"])  # all 8 alpha docs, includes doc03
    final = r.execute(run_id)
    assert final["status"] == "completed"
    st = r.status(run_id)
    assert st["counts"] == {"total": 8, "done": 7, "failed": 1, "pending": 0}
    assert st["failures"] == [{"pdf": "alpha/doc03.pdf", "category": "alpha", "error": "RuntimeError: boom"}]
    res = r.results(run_id)
    assert res["is_official"] is False and "not a full official score" in res["score_label"]
    assert res["overall_score_pct"] == 62.5
    assert list(res["categories"]) == ["alpha"] and res["categories"]["alpha"]["passed"] == 5  # only selected categories are reported
    assert res["baseline"]["total"] == 3
    assert res["documents"] == {"total": 8, "succeeded": 7, "failed": 1}
    assert res["timing"]["per_page_ms"]["median"] == 100.0  # 200 ms / 2 pages
    assert res["timing"]["documents"]["alpha/doc00.pdf"]["per_page_ms"] == 100.0
    assert res["failed_tests"][0]["category"] == "alpha" and res["has_report"] is True
    assert r.report_path(run_id).read_text() == "<html>report</html>"
    assert "FAILED alpha/doc03.pdf" in r.log_tail(run_id)
    assert (store.run_dir(r.settings.workspace, run_id) / "scoring" / "axtract" / "alpha" / "doc00_pg1_repeat1.md").exists()


def test_extraction_exception_is_recorded_not_raised(make_runner):
    def explode(*_):
        raise MemoryError("oom")

    r = make_runner(extract=explode)
    run_id = r.create_run(per_category=2, categories=["beta"])
    assert r.execute(run_id)["status"] == "completed"
    assert r.status(run_id)["counts"]["failed"] == 2


def test_scorer_failure_marks_run_failed_but_keeps_extraction(make_runner):
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
    # a completed run cannot be re-run, but a failed one resumes and only redoes what is not done
    r._set_status(store.run_dir(r.settings.workspace, run_id), "failed", error="x")
    ex.fail_substr = None
    assert r.execute(run_id)["status"] == "completed"
    assert ex.calls[first:] == ["alpha/doc03.pdf"]
    assert r.status(run_id)["counts"]["failed"] == 0
    with pytest.raises(RunError) as err:
        r.execute(run_id)
    assert err.value.code == "RUN_COMPLETED"


def test_resume_after_crash_reports_interrupted_and_picks_up(make_runner):
    r = make_runner()
    run_id = r.create_run(per_category=3, categories=["alpha"])
    rdir = store.run_dir(r.settings.workspace, run_id)
    # simulate a crash mid-run: one doc done, status stuck on "running", no live lock
    prog = store.read_json(rdir / "progress.json")
    first = next(iter(prog["docs"]))
    prog["docs"][first].update(status="done", pages=2, total_ms=10.0, per_page_ms=5.0)
    store.atomic_write_json(rdir / "progress.json", prog)
    r._set_status(rdir, "running")
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
    t.join(10)
    assert out["status"] == "cancelled"
    counts = r.status(run_id)["counts"]
    assert counts["done"] < 8 and counts["pending"] > 0 and r.status(run_id)["resumable"]


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
    t.join(10)
    assert r.execute(b)["status"] == "completed"  # lock released after the first finished


def test_stale_lock_is_taken_over(make_runner):
    r = make_runner()
    store.atomic_write_json(r.lock.path, {"run_id": "qk-20260101T000000Z-deadbeef", "heartbeat": time.time() - 10_000})
    run_id = r.create_run(per_category=1, categories=["beta"])
    assert r.execute(run_id)["status"] == "completed"


def test_history_and_compare(make_runner):
    r = make_runner()
    a = r.create_run(per_category=2, seed=1)
    r.execute(a)
    b = r.create_run(per_category=2, seed=1)
    r.execute(b)
    hist = r.history()
    assert {h["run_id"] for h in hist} == {a, b} and all(h["is_official"] is False for h in hist)
    cmp = r.compare(a, b)
    assert cmp["same_documents"] is True and cmp["overall_delta_pct"] == 0.0 and cmp["is_official"] is False
    assert {row["category"] for row in cmp["categories"]} == {"alpha", "beta", "tables"}
    c = r.create_run(per_category=2, seed=2)
    r.execute(c)
    assert r.compare(a, c)["note"]


def test_logs_and_errors_do_not_leak_paths(make_runner):
    r = make_runner()
    run_id = r.create_run(per_category=1, categories=["beta"])
    r.execute(run_id)
    log = r.log_tail(run_id)
    assert str(r.settings.workspace) not in log and str(r.settings.data_dir) not in log
