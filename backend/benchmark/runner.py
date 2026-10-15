"""The Smart Benchmark Runner: create, execute, resume and report a sampled benchmark run.

Extraction and scoring are injectable so tests run with mocks. The defaults reuse the existing
AXTRACT adapter (`scripts/benchmark_axtract_olmocr.py`) and the unmodified official scorer
(`olmocr.bench.benchmark`, stdout parsed by `scoring.py`).
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import config, store
from .dataset import Category, discover_categories, resolve_categories
from .sampling import sample_documents
from .scoring import parse_scorer_output

CANDIDATE = "axtract"  # candidate folder name the official scorer scans
SCORE_LABEL = "Quick sample score (not a full official score)"

ExtractFn = Callable[[Path, Path, Path], dict]  # (pdf_path, rel_path, candidate_dir) -> result meta
ScoreFn = Callable[[Path, Path, Path], dict]  # (scoring_dir, report_html, failed_jsonl) -> {returncode, stdout, stderr}


class RunError(Exception):
    """A problem the caller can show to the user (bad input, conflict, missing run)."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_script(name: str):
    path = config.REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_axtract_script_{name}", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.path.insert(0, str(config.REPO_ROOT / "backend"))
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def default_extract(pdf: Path, rel: Path, candidate_dir: Path) -> dict:
    return _load_script("benchmark_axtract_olmocr").process_single_pdf(pdf, rel, candidate_dir, True)


def make_default_scorer(settings: config.BenchmarkSettings) -> ScoreFn:
    def score(scoring_dir: Path, report_html: Path, failed_jsonl: Path) -> dict:
        cmd = [
            str(settings.scorer_python), "-m", "olmocr.bench.benchmark",
            "--dir", str(scoring_dir), "--candidate", CANDIDATE, "--force",
            "--output_failed", str(failed_jsonl), "--test_report", str(report_html),
        ]  # fixed argv, no shell: nothing user-supplied reaches the command line
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        proc = subprocess.run(
            cmd, cwd=str(settings.repo_root), capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env, timeout=config.SCORER_TIMEOUT_SECONDS,
        )
        return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}

    return score


def git_info(repo_root: Path) -> dict:
    def run(*args: str) -> str | None:
        try:
            out = subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True, timeout=15)
            return out.stdout.strip() if out.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    sha = run("rev-parse", "HEAD")
    status = run("status", "--porcelain")
    return {"sha": sha, "branch": run("rev-parse", "--abbrev-ref", "HEAD"), "dirty": bool(status) if status is not None else None}


class BenchmarkRunner:
    def __init__(
        self,
        settings: config.BenchmarkSettings | None = None,
        extract_fn: ExtractFn | None = None,
        score_fn: ScoreFn | None = None,
        parse_fn: Callable[[str], dict] | None = None,
        git_fn: Callable[[Path], dict] | None = None,
    ):
        self.settings = settings or config.BenchmarkSettings()
        self.settings.validate_isolation()
        self.extract_fn = extract_fn or default_extract
        self.score_fn = score_fn or make_default_scorer(self.settings)
        self.parse_fn = parse_fn or parse_scorer_output
        self.git_fn = git_fn or git_info
        self.lock = store.ActiveRunLock(self.settings.workspace)
        self._log_lock = threading.Lock()

    # ---- categories -------------------------------------------------------------------------
    def categories(self) -> dict[str, Category]:
        return discover_categories(self.settings.data_dir)

    # ---- paths ------------------------------------------------------------------------------
    def _dir(self, run_id: str) -> Path:
        try:
            return store.run_dir(self.settings.workspace, run_id)
        except ValueError as exc:
            raise RunError("INVALID_RUN_ID", str(exc), 400)

    def _scrub(self, text: str) -> str:
        for p in (self.settings.workspace, self.settings.data_dir, self.settings.repo_root):
            text = text.replace(str(p), "<…>").replace(str(p).replace("\\", "/"), "<…>")
        return text

    def _log(self, rdir: Path, message: str) -> None:
        with self._log_lock, (rdir / "run.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{_now()} {self._scrub(message)}\n")

    # ---- create -----------------------------------------------------------------------------
    def create_run(
        self, *, mode: str = config.DEFAULT_MODE, categories: list[str] | str | None = "all",
        per_category: int = config.DEFAULT_PER_CATEGORY, seed: int = config.DEFAULT_SEED,
        workers: int = config.DEFAULT_WORKERS,
    ) -> str:
        if mode not in config.MODES:
            raise RunError("INVALID_MODE", f"Mode must be one of: {', '.join(config.MODES)}.")
        if not 1 <= per_category <= config.MAX_PER_CATEGORY:
            raise RunError("INVALID_SAMPLE_SIZE", f"PDFs per category must be 1-{config.MAX_PER_CATEGORY}.")
        if not 1 <= workers <= config.MAX_WORKERS:
            raise RunError("INVALID_WORKERS", f"Workers must be 1-{config.MAX_WORKERS}.")
        try:
            available = self.categories()
        except (FileNotFoundError, ValueError):
            raise RunError("DATASET_UNAVAILABLE", "The benchmark dataset is not available on this server.", 503)
        try:
            selected = resolve_categories(categories, available)
        except ValueError as exc:
            raise RunError("INVALID_CATEGORY", str(exc))
        if not selected:
            raise RunError("INVALID_CATEGORY", "No categories selected.")

        manifest = sample_documents(available, selected, per_category, seed)
        run_id = store.new_run_id()
        rdir = self._dir(run_id)
        scoring = rdir / "scoring"
        (scoring / CANDIDATE).mkdir(parents=True, exist_ok=True)

        # Private scoring tree: copied PDFs + jsonl filtered to the sampled documents only.
        by_cat: dict[str, set[str]] = {}
        for doc in manifest:
            by_cat.setdefault(doc["category"], set()).add(doc["pdf"])
            dest = scoring / "pdfs" / doc["pdf"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.settings.data_dir / "pdfs" / doc["pdf"], dest)
        for name, pdfs in by_cat.items():
            src = self.settings.data_dir / available[name].jsonl
            kept = []
            with src.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        if json.loads(line)["pdf"].replace("\\", "/") in pdfs:
                            kept.append(line if line.endswith("\n") else line + "\n")
                    except (ValueError, KeyError, TypeError, AttributeError):
                        continue
            (scoring / available[name].jsonl).write_text("".join(kept), encoding="utf-8")

        git = self.git_fn(self.settings.repo_root)
        run = {
            "run_id": run_id, "mode": mode, "score_kind": "quick_sample", "is_official": False,
            "score_label": SCORE_LABEL, "created_at": _now(),
            "config": {"categories": selected, "per_category": per_category, "seed": seed, "workers": workers},
            "git": git, "status": "created", "error": None,
            "totals": {"documents": len(manifest), "test_cases": sum(len(d["test_ids"]) for d in manifest)},
            "category_jsonl": {n: available[n].jsonl for n in selected},
        }
        store.atomic_write_json(rdir / "run.json", run)
        store.atomic_write_json(rdir / "manifest.json", {"run_id": run_id, "documents": manifest})
        docs = {d["pdf"]: {"category": d["category"], "status": "pending"} for d in manifest}
        self._write_progress(rdir, run_id, "created", docs, started=None)
        self._log(rdir, f"Created run {run_id}: {len(manifest)} documents in {len(selected)} categories, git {git.get('sha')}")
        return run_id

    # ---- progress ---------------------------------------------------------------------------
    @staticmethod
    def _counts(docs: dict) -> dict:
        c = {"total": len(docs), "done": 0, "failed": 0, "pending": 0}
        for d in docs.values():
            c["done" if d["status"] == "done" else "failed" if d["status"] == "failed" else "pending"] += 1
        return c

    def _write_progress(self, rdir: Path, run_id: str, phase: str, docs: dict, started: float | None, **extra: Any) -> None:
        prev = store.read_json(rdir / "progress.json", {})
        store.atomic_write_json(rdir / "progress.json", {
            "run_id": run_id, "phase": phase, "updated_at": _now(), "docs": docs, "counts": self._counts(docs),
            "elapsed_s": round(time.time() - started, 1) if started else prev.get("elapsed_s", 0),
            "started_at": prev.get("started_at") or (_now() if started else None), **extra,
        })

    def _set_status(self, rdir: Path, status: str, error: str | None = None, **fields: Any) -> dict:
        run = store.read_json(rdir / "run.json", {})
        run.update({"status": status, "error": error, **fields})
        store.atomic_write_json(rdir / "run.json", run)
        return run

    # ---- execute / resume -------------------------------------------------------------------
    def execute(self, run_id: str, cancel: threading.Event | None = None) -> dict:
        """Run (or resume) a run to completion. Safe to call again after a crash or cancel."""
        rdir = self._dir(run_id)
        run = store.read_json(rdir / "run.json")
        if not run:
            raise RunError("RUN_NOT_FOUND", "Run not found.", 404)
        if run["status"] == "completed":
            raise RunError("RUN_COMPLETED", "This run is already complete.", 409)
        if not self.lock.acquire(run_id):
            raise RunError("RUN_CONFLICT", "Another benchmark run is already active.", 409)

        cancel = cancel or threading.Event()
        stop_beat = threading.Event()

        def heartbeat() -> None:
            while not stop_beat.wait(15):
                self.lock.beat(run_id)

        threading.Thread(target=heartbeat, daemon=True).start()
        started = time.time()
        try:
            git = self.git_fn(self.settings.repo_root)
            changed = bool(run["git"].get("sha") and git.get("sha") and git["sha"] != run["git"]["sha"])
            resumed = run["status"] != "created"
            self._set_status(rdir, "running", resumed_at=_now() if resumed else None,
                             git_changed_since_creation=changed or run.get("git_changed_since_creation", False))
            if changed:
                self._log(rdir, "WARNING: code revision changed since the run was created; results mix revisions.")
            self._log(rdir, "Resuming run" if resumed else "Starting run")

            manifest = store.read_json(rdir / "manifest.json")["documents"]
            progress = store.read_json(rdir / "progress.json")
            docs: dict = progress["docs"]
            pending = [d for d in manifest if docs[d["pdf"]]["status"] != "done"]
            self._extract(rdir, run_id, run, pending, docs, started, cancel)

            if cancel.is_set():
                self._write_progress(rdir, run_id, "cancelled", docs, started)
                self._log(rdir, "Cancelled")
                return self._set_status(rdir, "cancelled")

            self._write_progress(rdir, run_id, "scoring", docs, started)
            self._log(rdir, "Scoring with the official olmOCR scorer")
            results = self._score(rdir, run, docs)
            self._write_progress(rdir, run_id, "completed" if not results.get("scoring_error") else "failed", docs, started)
            if results.get("scoring_error"):
                return self._set_status(rdir, "failed", error=results["scoring_error"])
            return self._set_status(rdir, "completed", finished_at=_now())
        except RunError:
            raise
        except Exception as exc:  # noqa: BLE001 - recorded, run stays resumable
            self._log(rdir, f"Run failed: {type(exc).__name__}: {exc}")
            return self._set_status(rdir, "failed", error=self._scrub(f"{type(exc).__name__}: {exc}"))
        finally:
            stop_beat.set()
            self.lock.release(run_id)

    def _extract(self, rdir: Path, run_id: str, run: dict, pending: list[dict], docs: dict,
                 started: float, cancel: threading.Event) -> None:
        scoring = rdir / "scoring"
        candidate = scoring / CANDIDATE

        def work(doc: dict) -> dict | None:
            if cancel.is_set():
                return None
            rel = Path(doc["pdf"])
            t0 = time.perf_counter()
            try:
                meta = self.extract_fn(scoring / "pdfs" / rel, rel, candidate)
            except Exception as exc:  # noqa: BLE001 - one bad document never stops the run
                meta = {"success": False, "error": f"{type(exc).__name__}: {exc}", "pages": 0}
            meta.setdefault("total_elapsed_ms", (time.perf_counter() - t0) * 1000.0)
            return meta

        with ThreadPoolExecutor(max_workers=run["config"]["workers"]) as pool:
            futures = {pool.submit(work, d): d for d in pending}
            for fut in as_completed(futures):
                doc, meta = futures[fut], fut.result()
                if meta is None:
                    continue  # skipped by cancel: stays pending
                pages = int(meta.get("pages") or 0)
                total_ms = float(meta.get("total_elapsed_ms") or 0.0)
                ok = bool(meta.get("success"))
                docs[doc["pdf"]] = {
                    "category": doc["category"], "status": "done" if ok else "failed",
                    "pages": pages, "total_ms": round(total_ms, 1),
                    "per_page_ms": round(total_ms / pages, 1) if ok and pages else None,
                    "error": self._scrub(str(meta.get("error"))) if meta.get("error") else None,
                }
                self._write_progress(rdir, run_id, "extracting", docs, started)
                self.lock.beat(run_id)
                self._log(rdir, f"{'OK' if ok else 'FAILED'} {doc['pdf']} ({pages} pages, {total_ms:.0f} ms)"
                          + (f": {meta.get('error')}" if not ok else ""))

    def _score(self, rdir: Path, run: dict, docs: dict) -> dict:
        scoring = rdir / "scoring"
        report_html, failed_jsonl = rdir / "report.html", rdir / "failed_tests.jsonl"
        scored: dict[str, Any] = {}
        error = None
        try:
            raw = self.score_fn(scoring, report_html, failed_jsonl)
            (rdir / "scorer_output.txt").write_text(self._scrub((raw.get("stdout") or "") + "\n" + (raw.get("stderr") or "")), encoding="utf-8")
            if raw.get("returncode") != 0:
                error = f"Official scorer exited with code {raw.get('returncode')}."
            else:
                scored = self.parse_fn(raw.get("stdout") or "")
        except Exception as exc:  # noqa: BLE001
            error = self._scrub(f"Scoring failed: {type(exc).__name__}: {exc}")
        if error:
            self._log(rdir, error)

        jsonl_to_cat = {j: c for c, j in run["category_jsonl"].items()}
        by_cat = {}
        for jsonl, r in (scored.get("jsonl_results") or {}).items():
            if jsonl in jsonl_to_cat:
                by_cat[jsonl_to_cat[jsonl]] = {**r, "pass_rate_pct": r["pass_rate_pct"]}
        baseline = (scored.get("jsonl_results") or {}).get("baseline")

        failed_tests = []
        if failed_jsonl.exists():
            test_cat = {t: d["category"] for d in store.read_json(rdir / "manifest.json")["documents"] for t in d["test_ids"]}
            for line in failed_jsonl.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                failed_tests.append({"id": row.get("id"), "pdf": row.get("pdf"), "page": row.get("page"),
                                     "type": row.get("type"), "category": test_cat.get(row.get("id"))})

        done = [d for d in docs.values() if d["status"] == "done"]
        per_page = [d["per_page_ms"] for d in done if d.get("per_page_ms") is not None]
        timing = {
            "documents": {pdf: {k: d.get(k) for k in ("category", "status", "pages", "total_ms", "per_page_ms", "error")} for pdf, d in docs.items()},
            "total_extraction_ms": round(sum(d.get("total_ms") or 0 for d in docs.values()), 1),
            "per_page_ms": {
                "mean": round(statistics.mean(per_page), 1) if per_page else None,
                "median": round(statistics.median(per_page), 1) if per_page else None,
                "max": round(max(per_page), 1) if per_page else None,
            },
        }
        counts = self._counts(docs)
        results = {
            "run_id": run["run_id"], "mode": run["mode"], "score_kind": "quick_sample", "is_official": False,
            "score_label": SCORE_LABEL, "scoring_error": error,
            "overall_score_pct": scored.get("overall_score") if scored else None,
            "confidence_interval": scored.get("confidence_interval") if scored else None,
            "categories": by_cat, "baseline": baseline, "test_types": scored.get("test_types", {}),
            "documents": {"total": counts["total"], "succeeded": counts["done"], "failed": counts["failed"]},
            "timing": timing, "failed_tests": failed_tests[:500], "failed_test_count": len(failed_tests),
            "has_report": report_html.exists(), "scorer_errors": [self._scrub(e) for e in scored.get("errors", [])][:20],
        }
        store.atomic_write_json(rdir / "results.json", results)
        return results

    # ---- reading ----------------------------------------------------------------------------
    def status(self, run_id: str) -> dict:
        rdir = self._dir(run_id)
        run = store.read_json(rdir / "run.json")
        if not run:
            raise RunError("RUN_NOT_FOUND", "Run not found.", 404)
        progress = store.read_json(rdir / "progress.json", {})
        holder = self.lock.holder()
        active = bool(holder and holder.get("run_id") == run_id)
        state = run["status"]
        if state in ("running",) and not active:
            state = "interrupted"  # process died mid-run; resumable
        failures = [{"pdf": p, "category": d["category"], "error": d.get("error")}
                    for p, d in progress.get("docs", {}).items() if d["status"] == "failed"]
        return {**{k: run[k] for k in ("run_id", "mode", "score_kind", "is_official", "score_label", "created_at", "config", "git", "totals", "error")},
                "status": state, "phase": progress.get("phase"), "counts": progress.get("counts"),
                "elapsed_s": progress.get("elapsed_s", 0), "updated_at": progress.get("updated_at"),
                "resumable": state in ("interrupted", "cancelled", "failed", "created"), "failures": failures}

    def results(self, run_id: str) -> dict:
        rdir = self._dir(run_id)
        res = store.read_json(rdir / "results.json")
        if res is None:
            raise RunError("RESULTS_NOT_READY", "Results are not available yet.", 404)
        return res

    def manifest(self, run_id: str) -> dict:
        data = store.read_json(self._dir(run_id) / "manifest.json")
        if data is None:
            raise RunError("RUN_NOT_FOUND", "Run not found.", 404)
        return data

    def report_path(self, run_id: str) -> Path:
        path = self._dir(run_id) / "report.html"
        if not path.is_file():
            raise RunError("REPORT_NOT_FOUND", "No HTML report for this run.", 404)
        return path

    def log_tail(self, run_id: str, lines: int = 200) -> str:
        path = self._dir(run_id) / "run.log"
        if not path.is_file():
            return ""
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])

    def history(self, limit: int = 50) -> list[dict]:
        out = []
        for rid in store.list_run_ids(self.settings.workspace)[:limit]:
            try:
                st = self.status(rid)
            except RunError:
                continue
            res = store.read_json(self._dir(rid) / "results.json", {})
            out.append({k: st[k] for k in ("run_id", "mode", "score_kind", "is_official", "status", "created_at", "counts", "elapsed_s", "config", "git")}
                       | {"overall_score_pct": res.get("overall_score_pct"), "score_label": SCORE_LABEL})
        return out

    def compare(self, run_a: str, run_b: str) -> dict:
        """Category-wise deltas (b minus a) between two completed runs, with a sampling caveat."""
        ra, rb = self.results(run_a), self.results(run_b)
        cats = sorted(set(ra["categories"]) | set(rb["categories"]))
        rows = []
        for c in cats:
            a, b = ra["categories"].get(c), rb["categories"].get(c)
            rows.append({"category": c, "a_pct": a and a["pass_rate_pct"], "b_pct": b and b["pass_rate_pct"],
                         "delta_pct": round(b["pass_rate_pct"] - a["pass_rate_pct"], 1) if a and b else None})
        ma, mb = self.manifest(run_a)["documents"], self.manifest(run_b)["documents"]
        same_docs = {d["pdf"] for d in ma} == {d["pdf"] for d in mb}
        return {"a": run_a, "b": run_b, "same_documents": same_docs,
                "overall_a_pct": ra["overall_score_pct"], "overall_b_pct": rb["overall_score_pct"],
                "overall_delta_pct": round(rb["overall_score_pct"] - ra["overall_score_pct"], 1)
                if ra["overall_score_pct"] is not None and rb["overall_score_pct"] is not None else None,
                "categories": rows, "is_official": False,
                "note": None if same_docs else "The runs used different documents, so differences include sampling noise."}
