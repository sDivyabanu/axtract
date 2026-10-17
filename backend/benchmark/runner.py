"""The Smart Benchmark Runner: create, execute, resume and report a benchmark run.

Extraction runs in warm worker processes (`extract_pool`) so a stuck PDF can be killed; scoring runs the
unmodified official olmOCR scorer through `score_wrapper.py`. Both are injectable so tests use mocks.

Run folder layout (all inside the dedicated workspace, never inside the official dataset or reports):
  run.json        configuration, git revision, status, final verdict
  manifest.json   exactly which PDFs / test cases were selected
  docs.jsonl      append-only per-document checkpoint (source of truth for resume)
  progress.json   throttled snapshot for the dashboard
  results.json    scores, validity, per-category results, timing
  outcomes.jsonl  one compact line per test case (pass/fail + scorer feedback)
  run.log, scorer_output.txt, report.html (quick mode), scoring/ (private PDFs, filtered tests, candidate output)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import statistics
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from glob import escape as glob_escape
from pathlib import Path
from typing import Any, Callable

from . import config, procutil, store
from .dataset import Category, dataset_fingerprint, discover_categories, resolve_categories
from .extract_pool import ExtractorPool
from .sampling import sample_documents
from .scoring import ScoreFn, diagnostic_from_outcomes, make_default_scorer, parse_scorer_output

CANDIDATE = "axtract"  # candidate folder name the official scorer scans
SCORING_CONFIG = {"scorer": "olmocr.bench.benchmark", "force": True, "skip_baseline": True,
                  "formula": "mean of per-JSONL pass rates"}  # mirrors the official full-run command
LABELS = {
    "quick_sample": "Quick sample score (not a full official score)",
    "full_pending": "Full benchmark run in progress",
    "full_official": "Full official score",
    "full_incomplete": "Incomplete full run: diagnostic only, not an official score",
    "imported_invalid": "Imported official run: FAILED, diagnostic only, not a valid score",
    "imported_official": "Imported official score",
}

ExtractFn = Callable[[Path, Path, Path], dict]  # (pdf_path, rel_path, candidate_dir) -> result meta


class RunError(Exception):
    """A problem the caller can show to the user (bad input, conflict, missing run)."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


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


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.link(src, dst)  # hardlink: no extra disk, source untouched
    except OSError:
        shutil.copy2(src, dst)


class BenchmarkRunner:
    def __init__(
        self,
        settings: config.BenchmarkSettings | None = None,
        extract_fn: ExtractFn | None = None,
        score_fn: ScoreFn | None = None,
        git_fn: Callable[[Path], dict] | None = None,
        memory_fn: Callable[[], int | None] = procutil.free_memory_mb,
    ):
        self.settings = settings or config.BenchmarkSettings()
        self.settings.validate_isolation()
        self.extract_fn = extract_fn  # None => real worker-process pool
        self.score_fn = score_fn or make_default_scorer(self.settings)
        self.git_fn = git_fn or git_info
        self.memory_fn = memory_fn
        self.lock = store.ActiveRunLock(self.settings.workspace)
        self._log_lock = threading.Lock()
        self._progress_at: dict[str, float] = {}

    # ---- categories / paths -----------------------------------------------------------------
    def categories(self) -> dict[str, Category]:
        return discover_categories(self.settings.data_dir)

    def _dir(self, run_id: str) -> Path:
        try:
            return store.run_dir(self.settings.workspace, run_id)
        except ValueError as exc:
            raise RunError("INVALID_RUN_ID", str(exc), 400)

    def scrub(self, text: str) -> str:
        roots = {self.settings.workspace, self.settings.data_dir, self.settings.repo_root,
                 self.settings.scorer_python.parent.parent.parent, Path.home()}
        for p in sorted(roots, key=lambda x: -len(str(x))):
            s = str(p)
            if len(s) > 3:
                text = text.replace(s, "<…>").replace(s.replace("\\", "/"), "<…>")
        return text

    def _log(self, rdir: Path, message: str) -> None:
        with self._log_lock, (rdir / "run.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{now_iso()} {self.scrub(message)}\n")

    # ---- create -----------------------------------------------------------------------------
    def create_run(
        self, *, mode: str = config.DEFAULT_MODE, categories: list[str] | str | None = "all",
        per_category: int | None = config.DEFAULT_PER_CATEGORY, seed: int = config.DEFAULT_SEED,
        workers: int = config.DEFAULT_WORKERS, timeout_s: int = config.DEFAULT_TIMEOUT_S,
        html_report: bool | None = None, confirm_full: bool = False,
    ) -> str:
        if mode not in config.MODES:
            raise RunError("INVALID_MODE", f"Mode must be one of: {', '.join(config.MODES)}.")
        if not 1 <= workers <= config.MAX_WORKERS:
            raise RunError("INVALID_WORKERS", f"Workers must be 1-{config.MAX_WORKERS}.")
        if not config.MIN_TIMEOUT_S <= timeout_s <= config.MAX_TIMEOUT_S:
            raise RunError("INVALID_TIMEOUT", f"Timeout must be {config.MIN_TIMEOUT_S}-{config.MAX_TIMEOUT_S} seconds per document.")
        if mode == "quick":
            if per_category is None or not 1 <= per_category <= config.MAX_PER_CATEGORY:
                raise RunError("INVALID_SAMPLE_SIZE", f"PDFs per category must be 1-{config.MAX_PER_CATEGORY}.")
        else:
            if not confirm_full:
                raise RunError("FULL_NOT_CONFIRMED", "A full benchmark runs every PDF and must be confirmed explicitly.")
            if categories not in ("all", ["all"], None, []):
                raise RunError("INVALID_CATEGORY", "A full benchmark always covers every category.")
            per_category = None
        try:
            available = self.categories()
        except (FileNotFoundError, ValueError):
            raise RunError("DATASET_UNAVAILABLE", "The benchmark dataset is not available on this server.", 503)
        try:
            selected = resolve_categories("all" if mode == "full" else categories, available)
        except ValueError as exc:
            raise RunError("INVALID_CATEGORY", str(exc))
        if not selected:
            raise RunError("INVALID_CATEGORY", "No categories selected.")

        manifest = sample_documents(available, selected, per_category, seed)
        run_id = store.new_run_id(mode)
        rdir = self._dir(run_id)
        scoring = rdir / "scoring"
        (scoring / CANDIDATE).mkdir(parents=True, exist_ok=True)

        # Private scoring tree: linked PDFs + jsonl filtered to the sampled documents only.
        by_cat: dict[str, set[str]] = {}
        for doc in manifest:
            by_cat.setdefault(doc["category"], set()).add(doc["pdf"])
            _link_or_copy(self.settings.data_dir / "pdfs" / doc["pdf"], scoring / "pdfs" / doc["pdf"])
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

        all_ids = sorted(t for d in manifest for t in d["test_ids"])
        git = self.git_fn(self.settings.repo_root)
        run = {
            "run_id": run_id, "mode": mode, "created_at": now_iso(), "status": "created", "error": None,
            "score_kind": "quick_sample" if mode == "quick" else "full_pending", "is_official": False,
            "score_label": LABELS["quick_sample" if mode == "quick" else "full_pending"],
            "config": {"categories": selected, "per_category": per_category, "seed": seed, "workers": workers,
                       "timeout_s": timeout_s, "html_report": mode == "quick" if html_report is None else bool(html_report)},
            "scoring_config": dict(SCORING_CONFIG),
            "sample_id": hashlib.sha256("\n".join(all_ids).encode()).hexdigest()[:16],
            "dataset_id": dataset_fingerprint(self.settings.data_dir),
            "git": git, "totals": {"documents": len(manifest), "test_cases": len(all_ids)},
            "category_jsonl": {n: available[n].jsonl for n in selected},
        }
        store.atomic_write_json(rdir / "run.json", run)
        store.atomic_write_json(rdir / "manifest.json", {"run_id": run_id, "documents": manifest})
        self._write_progress(rdir, run, "created", {}, force=True)
        self._log(rdir, f"Created {mode} run {run_id}: {len(manifest)} documents, {len(all_ids)} test cases, git {git.get('sha')}")
        return run_id

    # ---- progress ---------------------------------------------------------------------------
    @staticmethod
    def _counts(manifest: list[dict], docs: dict) -> dict:
        """done = every page has non-empty output; partial = some pages have none; failed = no usable output.
        `degraded` (extraction reported errors but every page was written) is informational only."""
        c = {"total": len(manifest), "done": 0, "partial": 0, "failed": 0, "timed_out": 0, "empty": 0, "pending": 0,
             "degraded": 0}
        for d in manifest:
            rec = docs.get(d["pdf"])
            if not rec:
                c["pending"] += 1
                continue
            c["empty"] += 1 if rec.get("empty") else 0
            if rec["status"] == "done":
                c["done"] += 1
                c["degraded"] += 1 if rec.get("degraded") else 0
            elif rec["status"] == "partial":
                c["partial"] += 1
            else:
                c["failed"] += 1
                c["timed_out"] += 1 if rec.get("timed_out") else 0
        return c

    def _write_progress(self, rdir: Path, run: dict, phase: str, docs: dict, force: bool = False,
                        elapsed_base: float = 0.0, session_start: float | None = None) -> None:
        key = run["run_id"]
        if not force and time.monotonic() - self._progress_at.get(key, 0.0) < 1.0:
            return
        self._progress_at[key] = time.monotonic()
        manifest = store.read_json(rdir / "manifest.json", {"documents": []})["documents"]
        failures = [{"pdf": p, "category": d["category"], "error": d.get("error"), "timed_out": bool(d.get("timed_out"))}
                    for p, d in docs.items() if d["status"] in ("failed", "partial")]
        elapsed = elapsed_base + (time.time() - session_start if session_start else 0.0)
        store.atomic_write_json(rdir / "progress.json", {
            "run_id": key, "phase": phase, "updated_at": now_iso(), "counts": self._counts(manifest, docs),
            "elapsed_s": round(elapsed, 1), "failures": failures[:100], "failure_count": len(failures),
        })

    def _set_status(self, rdir: Path, status: str, error: str | None = None, **fields: Any) -> dict:
        run = store.read_json(rdir / "run.json", {})
        run.update({"status": status, "error": error, **fields})
        store.atomic_write_json(rdir / "run.json", run)
        return run

    # ---- execute / resume -------------------------------------------------------------------
    def execute(self, run_id: str, cancel: threading.Event | None = None) -> dict:
        """Run (or resume) a run to completion. Safe to call again after a crash, timeout or cancel."""
        rdir = self._dir(run_id)
        run = store.read_json(rdir / "run.json")
        if not run:
            raise RunError("RUN_NOT_FOUND", "Run not found.", 404)
        if run["status"] == "imported" or (run["status"] == "completed" and run.get("completeness") != "incomplete"):
            raise RunError("RUN_COMPLETED", "This run is already complete.", 409)
        if not self.lock.acquire(run_id):
            raise RunError("RUN_CONFLICT", "Another benchmark run is already active.", 409)

        cancel = cancel or threading.Event()
        stop_beat = threading.Event()
        pool: ExtractorPool | None = None

        def heartbeat() -> None:
            while not stop_beat.wait(15):
                self.lock.beat(run_id)

        threading.Thread(target=heartbeat, daemon=True).start()
        session_start = time.time()
        prev = store.read_json(rdir / "progress.json", {})
        elapsed_base = float(prev.get("elapsed_s", 0.0))
        try:
            free = self.memory_fn()
            if free is not None and free < config.MIN_FREE_MEMORY_MB and self.extract_fn is None:
                raise RunError("LOW_MEMORY", f"Only {free} MB of memory is free; at least {config.MIN_FREE_MEMORY_MB} MB is needed.", 503)
            git = self.git_fn(self.settings.repo_root)
            changed = bool(run["git"].get("sha") and git.get("sha") and git["sha"] != run["git"]["sha"])
            resumed = run["status"] != "created"
            self._set_status(rdir, "running", resumed_at=now_iso() if resumed else None, cancel_requested=False,
                             git_changed_since_creation=changed or run.get("git_changed_since_creation", False))
            if changed:
                self._log(rdir, "WARNING: code revision changed since the run was created; results mix revisions.")
            self._log(rdir, "Resuming run" if resumed else "Starting run")

            manifest = store.read_json(rdir / "manifest.json")["documents"]
            docs = store.read_jsonl_last(rdir / "docs.jsonl", "pdf")
            self._reverify_outputs(rdir, manifest, docs)
            pending = [d for d in manifest if docs.get(d["pdf"], {}).get("status") != "done"]
            workers = procutil.worker_budget(run["config"]["workers"], config.MAX_WORKERS)
            if workers != run["config"]["workers"]:
                self._log(rdir, f"Using {workers} worker(s) (requested {run['config']['workers']}): limited by available CPUs")
            if self.extract_fn is None and pending:
                pool = ExtractorPool(self.settings.extract_python, self.settings.repo_root, workers,
                                     float(run["config"]["timeout_s"]), rdir / "workers",
                                     on_event=lambda m: self._log(rdir, m))
            def watch_cancel() -> None:
                while not stop_beat.is_set():
                    if cancel.wait(0.5):
                        if pool:
                            pool.cancel()  # kill workers now instead of waiting out the current documents
                        return

            threading.Thread(target=watch_cancel, daemon=True).start()
            self._extract(rdir, run, manifest, pending, docs, workers, pool, cancel, elapsed_base, session_start)

            if cancel.is_set():
                self._write_progress(rdir, run, "cancelled", docs, True, elapsed_base, session_start)
                self._log(rdir, "Cancelled")
                return self._set_status(rdir, "cancelled")

            self._write_progress(rdir, run, "scoring", docs, True, elapsed_base, session_start)
            self._log(rdir, "Scoring with the official olmOCR scorer")
            results = self._score(rdir, run, manifest, docs, cancel)
            if results is None:  # cancelled while scoring
                self._write_progress(rdir, run, "cancelled", docs, True, elapsed_base, session_start)
                self._log(rdir, "Cancelled during scoring")
                return self._set_status(rdir, "cancelled")
            failed = results["validity"] == "error"
            self._write_progress(rdir, run, "failed" if failed else "completed", docs, True, elapsed_base, session_start)
            if failed:
                return self._set_status(rdir, "failed", error=results["scoring_error"])
            return self._set_status(rdir, "completed", finished_at=now_iso(), score_kind=results["score_kind"],
                                    is_official=results["is_official"], score_label=results["score_label"],
                                    completeness=results["completeness"])
        except RunError:
            self._set_status(rdir, run["status"])
            raise
        except Exception as exc:  # noqa: BLE001 - recorded, run stays resumable
            self._log(rdir, f"Run failed: {type(exc).__name__}: {exc}")
            return self._set_status(rdir, "failed", error=self.scrub(f"{type(exc).__name__}: {exc}"))
        finally:
            stop_beat.set()
            if pool:
                pool.close()
            self.lock.release(run_id)

    def _extract(self, rdir: Path, run: dict, manifest: list[dict], pending: list[dict], docs: dict, workers: int,
                 pool: ExtractorPool | None, cancel: threading.Event, elapsed_base: float, session_start: float) -> None:
        scoring = rdir / "scoring"
        candidate = scoring / CANDIDATE

        def work(doc: dict) -> dict | None:
            if cancel.is_set():
                return None
            rel = Path(doc["pdf"])
            t0 = time.perf_counter()
            self._clear_outputs(candidate, rel)  # a retry must not inherit pages written by an earlier attempt
            try:
                if pool:
                    meta = pool.extract(scoring / "pdfs" / rel, rel, candidate)
                else:
                    meta = self.extract_fn(scoring / "pdfs" / rel, rel, candidate)  # type: ignore[misc]
            except Exception as exc:  # noqa: BLE001 - one bad document never stops the run
                meta = {"success": False, "error": f"{type(exc).__name__}: {exc}", "pages": 0}
            if meta.get("cancelled") or (cancel.is_set() and not meta.get("success")):
                return None  # interrupted by cancel: not a real failure, stays pending for resume
            meta.setdefault("total_elapsed_ms", (time.perf_counter() - t0) * 1000.0)
            return meta

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(work, d): d for d in pending}
            for fut in as_completed(futures):
                doc, meta = futures[fut], fut.result()
                if meta is None:
                    continue  # cancelled: stays pending
                pages = int(meta.get("pages") or 0)
                total_ms = float(meta.get("total_elapsed_ms") or 0.0)
                ok = bool(meta.get("success"))
                written, blank = self._output_pages(candidate, Path(doc["pdf"]))
                if ok and written <= 0:
                    ok, meta["error"] = False, meta.get("error") or "Extraction reported success but wrote no output."
                if not ok:
                    status = "failed"
                elif pages and written < pages:
                    status = "partial"  # the scorer would find these pages missing
                    meta["error"] = f"{pages - written} of {pages} page(s) have no output" + (
                        f" ({meta['error']})" if meta.get("error") else "")
                else:
                    status = "done"
                err_text = str(meta.get("error") or "")
                rec = {
                    "pdf": doc["pdf"], "category": doc["category"], "status": status,
                    "pages": pages, "total_ms": round(total_ms, 1),
                    "per_page_ms": round(total_ms / pages, 1) if status != "failed" and pages else None,
                    "written_pages": written, "missing_pages": max(0, pages - written) if pages else 0,
                    "empty": bool(not ok and written <= 0 and (err_text.startswith("EMPTY_OUTPUT") or "no output" in err_text)),
                    "empty_pages": blank, "degraded": bool(status == "done" and meta.get("partial")),
                    "timed_out": bool(meta.get("timed_out")), "verify_ms": meta.get("verify_ms"),
                    "error": self.scrub(err_text) if err_text else None, "ts": now_iso(),
                }
                store.append_jsonl(rdir / "docs.jsonl", rec)
                docs[doc["pdf"]] = rec
                self._write_progress(rdir, run, "extracting", docs, False, elapsed_base, session_start)
                self.lock.beat(run["run_id"])
                note = "EMPTY OUTPUT" if rec["empty"] else {"done": "OK", "partial": "PARTIAL", "failed": "FAILED"}[status]
                self._log(rdir, f"{note} {doc['pdf']} ({pages} pages, {total_ms:.0f} ms)" + (f": {rec['error']}" if status != "done" else ""))
        self._write_progress(rdir, run, "extracting", docs, True, elapsed_base, session_start)

    @staticmethod
    def _output_files(candidate: Path, rel: Path) -> list[Path]:
        folder = candidate / rel.parent
        return list(folder.glob(f"{glob_escape(rel.stem)}_pg*_repeat*.md")) if folder.is_dir() else []

    @classmethod
    def _output_pages(cls, candidate: Path, rel: Path) -> tuple[int, int]:
        """(pages with non-empty output, pages with empty output) found on disk for one document."""
        files = cls._output_files(candidate, rel)
        empty = sum(1 for f in files if f.stat().st_size == 0)
        return len(files) - empty, empty

    @classmethod
    def _clear_outputs(cls, candidate: Path, rel: Path) -> None:
        for f in cls._output_files(candidate, rel):  # run-owned folder only: never an official output tree
            try:
                f.unlink()
            except OSError:
                pass

    def _reverify_outputs(self, rdir: Path, manifest: list[dict], docs: dict) -> None:
        """A checkpoint says "done", the disk decides: demote documents whose output files went missing."""
        candidate = rdir / "scoring" / CANDIDATE
        for d in manifest:
            rec = docs.get(d["pdf"])
            if not rec or rec.get("status") != "done":
                continue
            written, _ = self._output_pages(candidate, Path(d["pdf"]))
            if written < max(1, int(rec.get("pages") or 1)):
                demoted = {**rec, "status": "failed", "written_pages": written, "ts": now_iso(),
                           "error": f"Output files missing on resume ({written} of {rec.get('pages')} page(s) found); will re-extract."}
                store.append_jsonl(rdir / "docs.jsonl", demoted)
                docs[d["pdf"]] = demoted
                self._log(rdir, f"Output missing for {d['pdf']}: re-extracting")

    def _score(self, rdir: Path, run: dict, manifest: list[dict], docs: dict, cancel: threading.Event) -> dict | None:
        scoring = rdir / "scoring"
        outcomes = rdir / "outcomes.jsonl"
        html = rdir / "report.html" if run["config"].get("html_report") else None
        for stale in (outcomes, rdir / "report.html"):
            stale.unlink(missing_ok=True)
        error = None
        parsed: dict = {"status": "unknown", "jsonl_results": {}, "test_types": {}, "errors": [], "error_count": 0,
                        "overall_score": None, "confidence_interval": None}
        try:
            raw = self.score_fn(scoring, outcomes, html, bool(run["scoring_config"]["skip_baseline"]), cancel)
            if raw.get("cancelled"):
                return None
            (rdir / "scorer_output.txt").write_text(
                self.scrub((raw.get("stdout") or "")[-2_000_000:] + "\n--- stderr ---\n" + (raw.get("stderr") or "")), encoding="utf-8")
            if raw.get("returncode") != 0:
                error = f"Official scorer exited with code {raw.get('returncode')}. See the run log."
            else:
                parsed = parse_scorer_output(raw.get("stdout") or "")
        except Exception as exc:  # noqa: BLE001
            error = self.scrub(f"Scoring failed: {type(exc).__name__}: {exc}")
        if error:
            self._log(rdir, error)

        jsonl_to_cat = {j: c for c, j in run["category_jsonl"].items()}
        diag = diagnostic_from_outcomes(outcomes, jsonl_to_cat)
        counts = self._counts(manifest, docs)
        complete = counts["failed"] == 0 and counts["pending"] == 0 and counts["partial"] == 0
        validity = "error" if error else ("valid" if parsed["status"] == "valid" else "invalid" if parsed["status"] == "invalid" else "error")
        if validity == "error" and not error:
            error = "The official scorer produced no recognisable summary."
        is_full = run["mode"] == "full"
        if is_full:
            official = validity == "valid" and complete
            kind = "full_official" if official else "full_incomplete"
        else:
            official, kind = False, "quick_sample"

        cats: dict[str, dict] = {}
        for name, jsonl in run["category_jsonl"].items():
            if validity == "valid" and jsonl in parsed["jsonl_results"]:
                r = parsed["jsonl_results"][jsonl]
                cats[name] = {**r, "basis": "official"}
            elif name in diag["categories"]:
                d = diag["categories"][name]
                cats[name] = {"pass_rate_pct": d["pass_rate_pct"], "passed": d["passed"], "total": d["total"], "basis": "diagnostic"}

        done = [d for d in docs.values() if d["status"] in ("done", "partial")]
        per_page = [d["per_page_ms"] for d in done if d.get("per_page_ms") is not None]
        timing = {
            "documents": {pdf: {k: d.get(k) for k in ("category", "status", "pages", "written_pages", "missing_pages", "total_ms", "per_page_ms", "empty", "degraded", "timed_out", "error")}
                          for pdf, d in docs.items()},
            "total_extraction_ms": round(sum(d.get("total_ms") or 0 for d in docs.values()), 1),
            "per_page_ms": {"mean": round(statistics.mean(per_page), 1) if per_page else None,
                            "median": round(statistics.median(per_page), 1) if per_page else None,
                            "max": round(max(per_page), 1) if per_page else None},
        }
        notes = []
        if counts["failed"]:
            notes.append(f"{counts['failed']} document(s) produced no output ({counts['timed_out']} timed out), so the official scorer rejects the candidate; scores below are diagnostic.")
        if counts["partial"]:
            notes.append(f"{counts['partial']} document(s) are missing output for some pages, so the run is incomplete.")
        if counts["pending"]:
            notes.append(f"{counts['pending']} document(s) have not been extracted yet.")
        if counts["empty"]:
            notes.append(f"{counts['empty']} document(s) produced empty output.")
        if counts["degraded"]:
            notes.append(f"{counts['degraded']} document(s) wrote every page but reported extraction errors or timeouts.")
        results = {
            "run_id": run["run_id"], "mode": run["mode"], "score_kind": kind, "is_official": official,
            "score_label": LABELS[kind], "validity": validity, "completeness": "complete" if complete else "incomplete",
            "scoring_error": error, "notes": notes,
            "overall_score_pct": parsed["overall_score"] if validity == "valid" else None,
            "confidence_interval": parsed["confidence_interval"] if validity == "valid" else None,
            "diagnostic_score_pct": diag["macro_average_pct"],
            "categories": cats, "test_types": diag["test_types"],
            "documents": {"total": counts["total"], "succeeded": counts["done"], "failed": counts["failed"],
                          "partial": counts["partial"], "pending": counts["pending"], "degraded": counts["degraded"],
                          "timed_out": counts["timed_out"], "empty": counts["empty"]},
            "cases": {"evaluated": diag["cases"], "missing_output": diag["missing_output_cases"],
                      "failed": sum(v["total"] - v["passed"] for v in diag["categories"].values())},
            "timing": timing, "has_report": bool(html and html.exists()),
            "scorer_error_count": parsed["error_count"], "scorer_errors": [self.scrub(e)[:300] for e in parsed["errors"]][:10],
            "sample_id": run["sample_id"], "dataset_id": run["dataset_id"], "scoring_config": run["scoring_config"],
        }
        store.atomic_write_json(rdir / "results.json", results)
        return results

    def request_cancel(self, run_id: str) -> None:
        rdir = self._dir(run_id)
        run = store.read_json(rdir / "run.json", {})
        run["cancel_requested"] = True
        store.atomic_write_json(rdir / "run.json", run)

    # ---- reading ----------------------------------------------------------------------------
    def load_run(self, run_id: str) -> dict:
        run = store.read_json(self._dir(run_id) / "run.json")
        if not run:
            raise RunError("RUN_NOT_FOUND", "Run not found.", 404)
        return run

    def status(self, run_id: str) -> dict:
        rdir = self._dir(run_id)
        run = self.load_run(run_id)
        progress = store.read_json(rdir / "progress.json", {})
        holder = self.lock.holder()
        active = bool(holder and holder.get("run_id") == run_id)
        state = run["status"]
        if state == "running" and not active:
            state = "interrupted"  # process died mid-run; resumable
        elif state == "created" and not active and self._age_s(run.get("created_at")) > 30:
            state = "interrupted"  # created but never picked up (e.g. refused for low memory): resumable, not "starting" forever
        keys = ("run_id", "mode", "score_kind", "is_official", "score_label", "created_at", "config", "git", "totals",
                "error", "sample_id", "dataset_id", "scoring_config", "git_changed_since_creation", "imported",
                "cancel_requested", "completeness")
        return {**{k: run.get(k) for k in keys},
                "status": state, "phase": progress.get("phase"), "counts": progress.get("counts"),
                "elapsed_s": progress.get("elapsed_s", 0), "updated_at": progress.get("updated_at"),
                "resumable": (state in ("interrupted", "cancelled", "failed", "created")
                              or (state == "completed" and run.get("completeness") == "incomplete"))
                             and run["status"] != "imported",
                "failures": progress.get("failures", []), "failure_count": progress.get("failure_count", 0)}

    @staticmethod
    def _age_s(iso: str | None) -> float:
        try:
            return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0

    def results(self, run_id: str) -> dict:
        res = store.read_json(self._dir(run_id) / "results.json")
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
        with path.open("rb") as fh:  # read only the tail: logs of full runs can be large
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 256_000))
            data = fh.read().decode("utf-8", errors="replace")
        return "\n".join(data.splitlines()[-lines:])

    def active_run_id(self) -> str | None:
        holder = self.lock.holder()
        return holder.get("run_id") if holder else None

    def history(self, limit: int = 50) -> list[dict]:
        out = []
        for rid in store.list_run_ids(self.settings.workspace)[:limit]:
            try:
                st = self.status(rid)
            except RunError:
                continue
            res = store.read_json(self._dir(rid) / "results.json", {})
            out.append({
                "run_id": rid, "mode": st["mode"], "score_kind": st["score_kind"], "is_official": st["is_official"],
                "score_label": st["score_label"], "status": st["status"], "created_at": st["created_at"],
                "counts": st["counts"], "elapsed_s": st["elapsed_s"], "config": st["config"], "git": st["git"],
                "sample_id": st["sample_id"], "dataset_id": st["dataset_id"], "imported": st.get("imported"),
                "validity": res.get("validity"), "completeness": res.get("completeness"),
                "overall_score_pct": res.get("overall_score_pct"), "diagnostic_score_pct": res.get("diagnostic_score_pct"),
                "category_scores": {c: v["pass_rate_pct"] for c, v in (res.get("categories") or {}).items()},
                "median_ms_per_page": ((res.get("timing") or {}).get("per_page_ms") or {}).get("median"),
            })
        return out
