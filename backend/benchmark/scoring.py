"""Run the official scorer (through score_wrapper.py) and interpret what it prints and records."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterator

from . import config, procutil

WRAPPER = Path(__file__).with_name("score_wrapper.py")

_OVERALL = re.compile(r"Average Score:\s*([\d.]+)%\s*(?:±\s*([\d.]+)%)?")
_TYPE = re.compile(r"^\s*(\w+)\s*:\s*([\d.]+)%\s*average pass rate over\s*(\d+)\s*tests")
_JSONL = re.compile(r"^\s*([^\s:]+)\s*:\s*([\d.]+)%\s*\((\d+)/(\d+)\s*tests\)")

# score_fn(scoring_dir, outcomes_path, html_path_or_None, skip_baseline, cancel) -> {returncode, stdout, stderr, cancelled}
ScoreFn = Callable[[Path, Path, "Path | None", bool, threading.Event], dict]


def parse_scorer_output(stdout: str) -> dict:
    """Interpret the official scorer's final summary.

    `status` is "valid" when the scorer produced a numeric average, "invalid" when it printed
    `FAILED (errors)` (what the official scorer does for a candidate with missing outputs), and
    "unknown" when neither summary line was found.
    """
    out: dict = {"status": "unknown", "overall_score": None, "confidence_interval": None,
                 "test_types": {}, "jsonl_results": {}, "error_count": 0, "errors": []}
    in_jsonl = False
    seen_errors: set[str] = set()
    for line in stdout.splitlines():
        if "Results by JSONL file" in line:
            in_jsonl = True
            continue
        if "[ERROR]" in line:
            out["error_count"] += 1
            msg = line.strip()
            if msg not in seen_errors and len(out["errors"]) < 10:
                seen_errors.add(msg)
                out["errors"].append(msg)
            continue
        if "average of per-JSONL" in line:  # the final-summary line is authoritative
            if "FAILED" in line:
                out["status"] = "invalid"
                continue
            m = _OVERALL.search(line)
            if m:
                out["status"] = "valid"
                out["overall_score"] = float(m.group(1))
                out["confidence_interval"] = f"± {m.group(2)}%" if m.group(2) else None
            continue
        if in_jsonl:
            m = _JSONL.match(line)
            if m:
                out["jsonl_results"][m.group(1)] = {
                    "pass_rate_pct": float(m.group(2)), "passed": int(m.group(3)), "total": int(m.group(4)),
                }
            continue
        m = _TYPE.match(line)
        if m:
            out["test_types"][m.group(1)] = {"pass_rate_pct": float(m.group(2)), "test_count": int(m.group(3))}
    return out


def iter_outcomes(path: Path) -> Iterator[dict]:
    """Stream outcome records one at a time (never loads the whole file)."""
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
    except FileNotFoundError:
        return


def diagnostic_from_outcomes(path: Path, source_to_category: dict[str, str]) -> dict:
    """Per-category / per-type pass counts straight from the per-case record. Needs no scorer verdict, so it is
    available even when the official score is invalid. Cases without a candidate output count as failed."""
    cats: dict[str, dict] = defaultdict(lambda: {"passed": 0, "total": 0})
    types: dict[str, dict] = defaultdict(lambda: {"passed": 0, "total": 0})
    missing = 0
    for rec in iter_outcomes(path):
        cat = source_to_category.get(rec.get("source") or "")
        if cat is None:
            continue
        for bucket, key in ((cats, cat), (types, rec.get("type") or "unknown")):
            bucket[key]["total"] += 1
            bucket[key]["passed"] += 1 if rec.get("passed") else 0
        if "Missing MD files" in (rec.get("explanation") or ""):
            missing += 1
    for bucket in (cats, types):
        for v in bucket.values():
            v["pass_rate_pct"] = round(100.0 * v["passed"] / v["total"], 2) if v["total"] else 0.0
    macro = round(sum(v["pass_rate_pct"] for v in cats.values()) / len(cats), 2) if cats else None
    return {"categories": dict(cats), "test_types": dict(types), "macro_average_pct": macro,
            "cases": sum(v["total"] for v in cats.values()), "missing_output_cases": missing}


def make_default_scorer(settings: config.BenchmarkSettings) -> ScoreFn:
    def score(scoring_dir: Path, outcomes: Path, html: Path | None, skip_baseline: bool, cancel: threading.Event) -> dict:
        cmd = [str(settings.scorer_python), "-I", "-B", "-X", "utf8", str(WRAPPER),  # -B: never write .pyc into the scorer checkout
               
               "--dir", str(scoring_dir), "--candidate", "axtract", "--outcomes", str(outcomes)]
        if html:
            cmd += ["--html", str(html)]
        if skip_baseline:
            cmd.append("--skip-baseline")
        # fixed argv, no shell: nothing user-supplied reaches the command line
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "TQDM_DISABLE": "1"}
        proc = subprocess.Popen(cmd, cwd=str(settings.repo_root), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", **procutil.popen_flags_low_priority())
        out_chunks: list[str] = []
        err_chunks: list[str] = []
        readers = [threading.Thread(target=lambda s, c: c.extend(iter(s.readline, "")), args=(proc.stdout, out_chunks), daemon=True),
                   threading.Thread(target=lambda s, c: c.extend(iter(s.readline, "")), args=(proc.stderr, err_chunks), daemon=True)]
        for r in readers:
            r.start()
        deadline = time.monotonic() + config.SCORER_TIMEOUT_SECONDS
        cancelled = False
        while proc.poll() is None:
            if cancel.is_set():
                cancelled = True
                procutil.kill_tree(proc)
                break
            if time.monotonic() > deadline:
                procutil.kill_tree(proc)
                err_chunks.append("scorer exceeded its time limit\n")
                break
            time.sleep(0.5)
        proc.wait(timeout=30)
        for r in readers:
            r.join(timeout=10)
        return {"returncode": proc.returncode, "stdout": "".join(out_chunks), "stderr": "".join(err_chunks)[-20000:],
                "cancelled": cancelled}

    return score
