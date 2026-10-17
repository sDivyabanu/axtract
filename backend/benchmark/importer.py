"""Import a finished official-run artifact folder as a read-only run, without re-running anything.

The importer only *reads* the source folder (confined to the configured import root) and writes a new run
folder in the benchmark workspace. It preserves the original verdict: a run the official scorer rejected
("FAILED (errors)") stays invalid, its score stays null, and any average computed from per-case results is
labelled diagnostic. The (possibly gigabyte-sized) official HTML report is never opened.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from . import config, store
from .dataset import dataset_fingerprint
from .runner import LABELS, BenchmarkRunner, RunError, now_iso

SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,99}$")
CANDIDATE_RE = re.compile(r"--candidate\s+([A-Za-z0-9._\-]{1,100})")
REQUIRED = "official_status.json"
OPTIONAL_JSON = ("diagnostic_scores.json", "extraction_summary.json", "git_snapshot.json", "final_extraction_verification.json",
                 "scorer_process.json")


def _confine(runner: BenchmarkRunner, source: str) -> Path:
    parts = [p for p in source.replace("\\", "/").split("/") if p]
    if not 1 <= len(parts) <= 3 or not all(SEGMENT_RE.match(p) and p not in (".", "..") for p in parts):
        raise RunError("INVALID_SOURCE", "The source must be a folder name inside the benchmark reports folder.")
    root = runner.settings.resolved_import_root()
    path = (root.joinpath(*parts)).resolve()
    if root not in path.parents or not path.is_dir():
        raise RunError("SOURCE_NOT_FOUND", "That benchmark folder was not found.", 404)
    return path


def _read_json(path: Path) -> dict | None:
    try:
        if path.stat().st_size > config.IMPORT_MAX_BYTES:
            raise RunError("ARTIFACT_TOO_LARGE", f"{path.name} is too large to import.", 413)
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError:
        raise RunError("ARTIFACT_INVALID", f"{path.name} is not valid JSON.", 422)


def list_importable(runner: BenchmarkRunner) -> list[dict]:
    root = runner.settings.resolved_import_root()
    found = []
    if root.is_dir():
        for status in sorted(root.glob(f"*/{REQUIRED}")) + sorted(root.glob(f"*/*/{REQUIRED}")):
            rel = status.parent.relative_to(root).as_posix()
            info = _read_json(status) or {}
            found.append({"source": rel, "official_status": info.get("official_status"),
                          "source_commit": (info.get("source_commit") or "")[:40],
                          "cases": info.get("official_cases_evaluated")})
    return found


def import_official(runner: BenchmarkRunner, source: str) -> dict:
    folder = _confine(runner, source)
    status_path = folder / REQUIRED
    if not status_path.is_file():
        raise RunError("ARTIFACT_MISSING", f"{REQUIRED} was not found in that folder.", 404)
    fingerprint = hashlib.sha256(status_path.read_bytes()).hexdigest()[:16]
    for h in runner.history(200):  # importing the same artifact twice returns the existing run
        if h.get("imported") and h["imported"].get("fingerprint") == fingerprint:
            return {"run_id": h["run_id"], "already_imported": True}

    status = _read_json(status_path) or {}
    files = {name: _read_json(folder / name) or {} for name in OPTIONAL_JSON}
    diag, summ, gitsnap, verif = (files["diagnostic_scores.json"], files["extraction_summary.json"],
                                  files["git_snapshot.json"], files["final_extraction_verification.json"])
    official_status = str(status.get("official_status") or "unknown")
    overall = status.get("official_overall_accuracy_pct")
    valid = overall is not None and not official_status.upper().startswith("FAILED")
    command = (folder / "command.txt").read_text(encoding="utf-8", errors="replace") if (folder / "command.txt").is_file() else ""
    m = CANDIDATE_RE.search(command)
    candidate = m.group(1) if m else None
    try:
        available = runner.categories()
    except (FileNotFoundError, ValueError):
        available = {}
    jsonl_to_cat = {c.jsonl: n for n, c in available.items()}

    run_id = store.new_run_id("import")
    rdir = runner._dir(run_id)
    rdir.mkdir(parents=True, exist_ok=True)

    # per-case record: stream, convert, and group per document for the manifest
    outcomes_src = folder / "case_outcomes_diagnostic.jsonl"
    docs_cases: dict[str, list[str]] = defaultdict(list)
    doc_cat: dict[str, str] = {}
    doc_missing: dict[str, int] = defaultdict(int)
    n_cases = 0
    if outcomes_src.is_file():
        if outcomes_src.stat().st_size > config.IMPORT_MAX_BYTES:
            raise RunError("ARTIFACT_TOO_LARGE", "case_outcomes_diagnostic.jsonl is too large to import.", 413)
        with outcomes_src.open(encoding="utf-8") as src, (rdir / "outcomes.jsonl").open("w", encoding="utf-8") as dst:
            for line in src:
                try:
                    r = json.loads(line)
                    t = r["test"]
                except (ValueError, KeyError):
                    continue
                expl = r.get("explanation_first_line") or ""
                fields = {k: (v[:600] if isinstance(v, str) else v) for k, v in t.items() if k not in ("pdf", "page", "id", "type") and v is not None}
                dst.write(json.dumps({"id": t["id"], "pdf": t["pdf"], "page": t.get("page"), "type": t.get("type"),
                                      "source": r.get("source_jsonl"), "passed": bool(r.get("passed")),
                                      "explanation": expl[:600], "test": fields}) + "\n")
                docs_cases[t["pdf"]].append(t["id"])
                doc_cat[t["pdf"]] = jsonl_to_cat.get(r.get("source_jsonl") or "", (r.get("source_jsonl") or "?").removesuffix(".jsonl"))
                if "Missing MD files" in expl or "missing" in expl.lower() and "output" in expl.lower():
                    doc_missing[t["pdf"]] += 1
                n_cases += 1
    manifest = [{"pdf": p, "category": doc_cat[p], "test_ids": sorted(ids)} for p, ids in sorted(docs_cases.items())]
    empties = {x.replace("\\", "/") for x in (verif.get("empty_markdown_files") or [])}
    timeouts_only = bool(verif.get("all_recorded_failures_are_timeouts"))
    doc_records, n_failed, n_empty = [], 0, 0
    for d in manifest:
        stem = d["pdf"][:-4]
        no_output = doc_missing[d["pdf"]] >= len(d["test_ids"]) > 0
        is_empty = f"{stem}_pg1_repeat1.md" in empties
        n_failed += no_output
        n_empty += is_empty
        doc_records.append({"pdf": d["pdf"], "category": d["category"], "status": "failed" if no_output else "done",
                            "timed_out": bool(no_output and timeouts_only), "empty": is_empty,
                            "error": "No output was produced (recorded as a timeout in the official run)" if no_output else None})
    for rec in doc_records:
        store.append_jsonl(rdir / "docs.jsonl", rec)

    cats: dict[str, dict] = {}
    for jsonl, v in (diag.get("categories") or {}).items():
        name = jsonl_to_cat.get(jsonl, jsonl.removesuffix(".jsonl"))
        cats[name] = {"pass_rate_pct": round(float(v.get("diagnostic_accuracy_pct", 0.0)), 2), "passed": v.get("passed"),
                      "total": v.get("total"), "basis": "diagnostic"}
    kind = "imported_official" if valid else "imported_invalid"
    created = verif.get("verified_at") or now_iso()
    all_ids = sorted(i for d in manifest for i in d["test_ids"])
    flags = {"scorer": "olmocr.bench.benchmark", "force": "--force" in command, "skip_baseline": "--skip_baseline" in command,
             "formula": "mean of per-JSONL pass rates"}
    sha = status.get("source_commit") or gitsnap.get("head")
    notes = [x for x in (status.get("reason"), status.get("diagnostic_warning"),
                         "The official HTML report is not imported (too large); open it from the reports folder.",
                         "Full-corpus latency was not recorded." if summ.get("full_corpus_median_processing_ms_per_page") is None else None) if x]
    run = {
        "run_id": run_id, "mode": "full", "created_at": created, "status": "imported", "error": None,
        "score_kind": kind, "is_official": bool(valid), "score_label": LABELS[kind],
        "config": {"categories": list(cats), "per_category": None, "seed": None, "workers": None, "timeout_s": None, "html_report": False},
        "scoring_config": flags, "sample_id": hashlib.sha256("\n".join(all_ids).encode()).hexdigest()[:16],
        "dataset_id": dataset_fingerprint(runner.settings.data_dir) if available else None,
        "git": {"sha": sha, "branch": gitsnap.get("branch"), "dirty": None},
        "totals": {"documents": len(manifest), "test_cases": n_cases},
        "category_jsonl": {n: c.jsonl for n, c in available.items() if n in cats},
        "imported": {"source": source.replace("\\", "/"), "imported_at": now_iso(), "fingerprint": fingerprint,
                     "official_status": official_status, "candidate_dir": candidate,
                     "report_note": "Official HTML report not imported"},
    }
    store.atomic_write_json(rdir / "run.json", run)
    store.atomic_write_json(rdir / "manifest.json", {"run_id": run_id, "documents": manifest})
    counts = {"total": len(manifest), "done": len(manifest) - n_failed, "failed": n_failed,
              "timed_out": sum(1 for r in doc_records if r["timed_out"]), "empty": n_empty, "pending": 0}
    store.atomic_write_json(rdir / "progress.json", {
        "run_id": run_id, "phase": "imported", "updated_at": now_iso(), "counts": counts, "elapsed_s": 0,
        "failures": [{"pdf": r["pdf"], "category": r["category"], "error": r["error"], "timed_out": r["timed_out"]}
                     for r in doc_records if r["status"] == "failed"][:100], "failure_count": n_failed})
    results = {
        "run_id": run_id, "mode": "full", "score_kind": kind, "is_official": bool(valid), "score_label": LABELS[kind],
        "validity": "valid" if valid else "invalid", "completeness": "complete" if n_failed == 0 else "incomplete",
        "scoring_error": None if valid else f"The official scorer reported: {official_status}", "notes": notes,
        "official_status": official_status,
        "overall_score_pct": float(overall) if valid else None, "confidence_interval": None,
        "diagnostic_score_pct": round(float(diag["diagnostic_macro_average_pct"]), 2) if diag.get("diagnostic_macro_average_pct") is not None else None,
        "categories": cats,
        "test_types": {k: {"pass_rate_pct": round(float(v.get("diagnostic_accuracy_pct", 0.0)), 2), "passed": v.get("passed"), "total": v.get("total")}
                       for k, v in (diag.get("test_types") or {}).items()},
        "documents": {"total": counts["total"], "succeeded": counts["done"], "failed": n_failed, "timed_out": counts["timed_out"], "empty": n_empty},
        "cases": {"evaluated": status.get("official_cases_evaluated") or n_cases, "missing_output": diag.get("missing_output_cases"),
                  "failed": diag.get("failed")},
        "timing": {"documents": {}, "total_extraction_ms": None,
                   "per_page_ms": {"mean": None, "median": summ.get("full_corpus_median_processing_ms_per_page"), "max": None}},
        "has_report": False, "scorer_error_count": 0, "scorer_errors": [],
        "sample_id": run["sample_id"], "dataset_id": run["dataset_id"], "scoring_config": flags,
    }
    store.atomic_write_json(rdir / "results.json", results)
    with (rdir / "run.log").open("w", encoding="utf-8") as fh:
        fh.write(f"{now_iso()} Imported {source} (official status: {official_status}). Nothing was re-run.\n")
    return {"run_id": run_id, "already_imported": False}
