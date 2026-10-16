"""Worker pool (with a stub worker), scorer-output parsing against real official output, process helpers."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from benchmark import procutil
from benchmark.extract_pool import ExtractorPool
from benchmark.scoring import diagnostic_from_outcomes, parse_scorer_output

STUB = r'''
import json, sys, time, os
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    name = req["rel"]
    if "crash" in name:
        os._exit(7)
    if "slow" in name:
        time.sleep(60)
    print("noise on stdout is ignored", flush=True) if "noisy" in name else None
    print(json.dumps({"id": req["id"], "meta": {"success": True, "pages": 1, "total_elapsed_ms": 1.0, "pid": os.getpid()}}), flush=True)
'''


@pytest.fixture
def stub(tmp_path):
    path = tmp_path / "stub_worker.py"
    path.write_text(STUB, encoding="utf-8")
    return [sys.executable, str(path)]


def make_pool(tmp_path, stub, workers=1, timeout=5):
    (tmp_path / "backend").mkdir(exist_ok=True)
    return ExtractorPool(Path(sys.executable), tmp_path, workers, timeout, tmp_path / "logs", command=stub, startup_timeout_s=20)


def test_worker_process_stays_warm_between_documents(tmp_path, stub):
    pool = make_pool(tmp_path, stub)
    try:
        pids = {pool.extract(Path("a.pdf"), Path(f"cat/doc{i}.pdf"), tmp_path)["pid"] for i in range(3)}
        assert len(pids) == 1  # one model/process warm-up, many documents
        assert pool.extract(Path("a.pdf"), Path("cat/noisy.pdf"), tmp_path)["success"] is True
    finally:
        pool.close()


def test_timeout_kills_the_worker_and_the_next_document_still_runs(tmp_path, stub):
    pool = make_pool(tmp_path, stub, timeout=1)
    try:
        t0 = time.monotonic()
        slow = pool.extract(Path("a.pdf"), Path("cat/slow.pdf"), tmp_path)
        assert slow["success"] is False and slow["timed_out"] is True and "TimeoutError" in slow["error"]
        assert time.monotonic() - t0 < 10  # killed at the deadline, not after the 60 s sleep
        assert pool.extract(Path("a.pdf"), Path("cat/ok.pdf"), tmp_path)["success"] is True  # replacement worker
    finally:
        pool.close()


def test_crashing_worker_is_reported_and_replaced(tmp_path, stub):
    pool = make_pool(tmp_path, stub)
    try:
        crashed = pool.extract(Path("a.pdf"), Path("cat/crash.pdf"), tmp_path)
        assert crashed["success"] is False and "WorkerCrashed" in crashed["error"]
        assert pool.extract(Path("a.pdf"), Path("cat/ok.pdf"), tmp_path)["success"] is True
    finally:
        pool.close()


def test_cancel_interrupts_a_running_document_immediately(tmp_path, stub):
    pool = make_pool(tmp_path, stub, timeout=30)
    result = {}
    t = threading.Thread(target=lambda: result.update(pool.extract(Path("a.pdf"), Path("cat/slow.pdf"), tmp_path)))
    t.start()
    time.sleep(1.5)
    t0 = time.monotonic()
    pool.cancel()
    t.join(10)
    assert result == {"cancelled": True} and time.monotonic() - t0 < 8
    assert pool.extract(Path("a.pdf"), Path("cat/ok.pdf"), tmp_path) == {"cancelled": True}
    pool.close()


def test_worker_that_never_starts_is_a_clean_failure(tmp_path):
    (tmp_path / "backend").mkdir(exist_ok=True)
    pool = ExtractorPool(Path(sys.executable), tmp_path, 1, 5, tmp_path / "logs",
                         command=[sys.executable, "-c", "import sys; sys.exit(3)"], startup_timeout_s=10)
    try:
        res = pool.extract(Path("a.pdf"), Path("cat/x.pdf"), tmp_path)
        assert res["success"] is False and "WorkerStartError" in res["error"]
    finally:
        pool.close()


def test_pool_bounds_concurrency(tmp_path, stub):
    pool = make_pool(tmp_path, stub, workers=2)
    pids = set()
    lock = threading.Lock()

    def go(i):
        pid = pool.extract(Path("a.pdf"), Path(f"cat/doc{i}.pdf"), tmp_path)["pid"]
        with lock:
            pids.add(pid)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    pool.close()
    assert 1 <= len(pids) <= 2


# ---- scorer output (the invalid sample is the real shape printed for the 05deead run) ------------
REAL_INVALID = """
Evaluating candidate: axtract_current_05deead

Candidate: axtract_current_05deead
  [ERROR] Candidate 'axtract_current_05deead' is missing MD repeats for arxiv_math/2503.09528_pg16.pdf page 1 (expected files matching arxiv_math/2503.09528_pg16_pg1_repeat*.md).
  [ERROR] Candidate 'axtract_current_05deead' is missing MD repeats for arxiv_math/2503.09528_pg16.pdf page 1 (expected files matching arxiv_math/2503.09528_pg16_pg1_repeat*.md).

============================================================
Final Summary with 95% Confidence Intervals:
axtract_current_05deead : Average Score: FAILED (errors)  (average of per-JSONL scores)
    absent  : 49.6% average pass rate over 823 tests
    math    : 0.0% average pass rate over 3385 tests

    Results by JSONL file:
        arxiv_math.jsonl              : 0.0% (0/2927 tests)
        table_tests.jsonl             : 0.0% (0/1020 tests)
"""


def test_failed_errors_output_is_invalid_not_a_zero_score():
    p = parse_scorer_output(REAL_INVALID)
    assert p["status"] == "invalid" and p["overall_score"] is None
    assert p["error_count"] == 2 and len(p["errors"]) == 1  # identical errors are de-duplicated for display
    assert p["jsonl_results"]["arxiv_math.jsonl"]["total"] == 2927


def test_valid_output_is_parsed():
    p = parse_scorer_output("""
axtract              : Average Score: 71.3% ± 2.4% (average of per-JSONL scores)
    present : 80.0% average pass rate over 10 tests

    Results by JSONL file:
        alpha.jsonl                   : 75.0% (3/4 tests)
""")
    assert p["status"] == "valid" and p["overall_score"] == 71.3 and p["confidence_interval"] == "± 2.4%"
    assert p["test_types"]["present"] == {"pass_rate_pct": 80.0, "test_count": 10}
    assert p["jsonl_results"]["alpha.jsonl"] == {"pass_rate_pct": 75.0, "passed": 3, "total": 4}


def test_unrecognised_output_is_unknown():
    assert parse_scorer_output("Traceback (most recent call last): boom")["status"] == "unknown"


def test_diagnostic_scores_come_from_per_case_outcomes(tmp_path):
    f = tmp_path / "outcomes.jsonl"
    rows = [
        {"id": "1", "source": "a.jsonl", "type": "present", "passed": True, "explanation": "ok"},
        {"id": "2", "source": "a.jsonl", "type": "present", "passed": False, "explanation": "Missing MD files"},
        {"id": "3", "source": "b.jsonl", "type": "table", "passed": True, "explanation": "ok"},
        {"id": "4", "source": "unknown.jsonl", "type": "table", "passed": True, "explanation": "ok"},
    ]
    f.write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n", encoding="utf-8")
    d = diagnostic_from_outcomes(f, {"a.jsonl": "alpha", "b.jsonl": "beta"})
    assert d["categories"]["alpha"] == {"passed": 1, "total": 2, "pass_rate_pct": 50.0}
    assert d["macro_average_pct"] == 75.0 and d["missing_output_cases"] == 1 and d["cases"] == 3
    assert diagnostic_from_outcomes(tmp_path / "missing.jsonl", {})["cases"] == 0


# ---- the official-run detector ---------------------------------------------------------------
def test_detector_only_counts_python_processes_running_the_official_commands(monkeypatch):
    rows = [(10, r'"C:\py\python.exe" -u scripts\benchmark_axtract_olmocr.py --workers 6'),
            (11, r"python.exe -m olmocr.bench.benchmark --dir X")]
    monkeypatch.setattr(procutil, "_list_python_commands", lambda: rows)
    assert procutil.official_benchmark_active() is True
    monkeypatch.setattr(procutil, "_list_python_commands", lambda: [(12, "python.exe -m uvicorn main:app"), (13, "python.exe -m benchmark.extract_worker .")])
    assert procutil.official_benchmark_active() is False  # an editor/git mentioning the script never reaches this list
    monkeypatch.setattr(procutil, "_list_python_commands", lambda: [(__import__("os").getpid(), "python benchmark_axtract_olmocr.py")])
    assert procutil.official_benchmark_active() is False  # ignores itself


def test_detector_fails_open_when_processes_cannot_be_listed(monkeypatch):
    def boom():
        raise OSError("no powershell")

    monkeypatch.setattr(procutil, "_list_python_commands", boom)
    assert procutil.official_benchmark_active() is False


def test_worker_budget_is_bounded():
    assert procutil.worker_budget(99, 4) <= 4 and procutil.worker_budget(1, 4) == 1 and procutil.worker_budget(0, 4) == 1
