"""Run AXTRACT on the complete olmOCR-bench (1,403 PDFs / 7,019 assertions) and score it with the unchanged official scorer.

Nothing here downloads anything. Inputs you provision once: the olmOCR-bench `bench_data` folder (pdfs/ and *.jsonl), the
allenai/olmocr scorer checkout at the recorded commit, and a Python 3.12 scorer interpreter (docs/FROZEN70_REPRODUCTION.md).

  python scripts/full_benchmark.py extract --data-root BENCH_DATA [--scanned-math require|off] [--candidate-name axtract_v2]
                                           [--run-dir RUN] [--resume] [--limit N] [--category CAT]
  python scripts/full_benchmark.py score   --data-root BENCH_DATA --scorer-python PY --scorer SCORER_REPO
                                           [--scorer-packages SITE] [--candidate-name axtract_v2] [--run-dir RUN]

`extract` writes the Markdown where the official scorer looks for a candidate: BENCH_DATA/<candidate-name>/<category>/<pdf>_pg1_repeat1.md.
It runs ONE document at a time in its own process (75 s hard limit, 60 s pipeline deadline, 2 GiB job cap, free-RAM guard), so a
crash or timeout costs one document. `--resume` continues an interrupted run; finished documents are kept, never redone.
Per-document records go to RUN/documents.jsonl as they finish. `score` runs `olmocr.bench.benchmark --force --skip_baseline` (through scripts/official_scorer_shim.py, which only fixes Windows path separators)
(a page that produced no Markdown counts as failed assertions instead of invalidating the whole candidate).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen70_benchmark as frozen  # noqa: E402  (shared worker, environment and safeguards)

ROOT = frozen.ROOT


def assertion_pdfs(data: Path, category: str | None) -> list[str]:
    pdfs: set[str] = set()
    for jsonl in sorted(data.glob("*.jsonl")):
        for line in jsonl.read_text(encoding="utf8").splitlines():
            if line.strip():
                pdfs.add(json.loads(line)["pdf"])
    ordered = sorted(pdfs)
    return [p for p in ordered if category is None or p.startswith(category + "/")]


def assertion_pages(data: Path) -> set[tuple[str, int]]:
    pages: set[tuple[str, int]] = set()
    for jsonl in sorted(data.glob("*.jsonl")):
        for line in jsonl.read_text(encoding="utf8").splitlines():
            if line.strip():
                row = json.loads(line)
                pages.add((row["pdf"], int(row.get("page", 1))))
    return pages


def missing_pages(data: Path, cand: Path) -> list[tuple[str, int]]:
    return sorted((pdf, page) for pdf, page in assertion_pages(data)
                  if not (cand / Path(pdf).parent / f"{Path(pdf).stem}_pg{page}_repeat1.md").is_file())


def fill_missing(data: Path, cand: Path) -> list[str]:
    """The official scorer reports the whole candidate as "FAILED (errors)" with 0% if ANY page has no Markdown file, even with
    --force. A page AXTRACT produced no text for (or a document that hit the hard limit) is therefore recorded as an empty file:
    its assertions fail honestly and the rest of the benchmark is still scored."""
    written = []
    for pdf, page in missing_pages(data, cand):
        target = cand / Path(pdf).parent / f"{Path(pdf).stem}_pg{page}_repeat1.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf8")
        written.append(f"{pdf} page {page}")
    return written


def check_dataset(data: Path, allow_mismatch: bool) -> None:
    man = frozen.manifest()
    bad = [name for name, want in man["dataset"]["case_source_sha256"].items()
           if not (data / name).is_file() or frozen.sha256(data / name) != want]
    if bad and not allow_mismatch:
        raise SystemExit(f"ERROR: {', '.join(bad)} differ from the dataset revision the frozen results used "
                         f"({man['dataset']['revision']}). Use that revision, or pass --allow-dataset-mismatch and say so in any report.")
    if not (data / "pdfs").is_dir():
        raise SystemExit(f"ERROR: {data / 'pdfs'} not found.")


def cmd_extract(args) -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "backend" / ".env", override=False)
    sys.path.insert(0, str(ROOT / "backend"))
    from extractors.azure_ocr_extractor import configuration, provider
    cloud = provider() == "azure"
    cloud_config = configuration() if cloud else None
    if cloud:
        args.scanned_math = "off"
        args.vlm = "off"
        os.environ["AXTRACT_VLM_OCR"] = "off"
    data = Path(args.data_root).resolve()
    check_dataset(data, args.allow_dataset_mismatch)
    py = args.python or sys.executable
    env = frozen.environment(args.scanned_math, args.allow_asset_overrides)
    frozen.apply_vlm(env, args.vlm)
    hard_timeout = cloud_config["doc_seconds"] + 30 if cloud else frozen.HARD_TIMEOUT_S
    if args.scanned_math == "require":
        check = subprocess.run([py, "-B", str(ROOT / "scripts" / "scanned_math_assets.py"), "check"], env=env, cwd=ROOT,
                               capture_output=True, text=True)
        if check.returncode:
            raise SystemExit("ERROR: scanned math is required but the recognizer cannot load:\n" + check.stdout + check.stderr
                             + "\nFollow docs/SCANNED_MATH_SETUP.md, then retry.")
    cand = data / args.candidate_name
    run = Path(args.run_dir or ROOT / "runs" / f"full_{args.candidate_name}").resolve()
    if (cand.exists() or run.exists()) and not args.resume:
        raise SystemExit(f"ERROR: {cand} or {run} already exists. Use --resume to continue that run, or choose another --candidate-name/--run-dir.")
    cand.mkdir(parents=True, exist_ok=True)
    (run / "docs").mkdir(parents=True, exist_ok=True)
    pdfs = assertion_pdfs(data, args.category)
    if args.limit:
        pdfs = pdfs[: args.limit]
    ledger = run / "documents.jsonl"
    done = {}
    if ledger.exists():
        for line in ledger.read_text(encoding="utf8").splitlines():
            if line.strip():
                row = json.loads(line)
                done[row["pdf"]] = row
    from extractors.azure_ocr_options import public_profile
    profile = {"provider": provider(), "azure": public_profile(cloud_config) if cloud_config else None}
    if cloud:
        import hashlib
        profile["azure_code_sha256"] = hashlib.sha256((ROOT / "backend/extractors/azure_ocr_extractor.py").read_bytes()).hexdigest()
    profile_path = run / "ocr_profile.json"
    if profile_path.exists():
        if json.loads(profile_path.read_text(encoding="utf8")) != profile:
            raise SystemExit("ERROR: OCR configuration differs from the existing run. Choose a new candidate and run directory.")
    elif cloud and done:
        raise SystemExit("ERROR: Cannot resume existing outputs without an OCR profile as a cloud run. Choose a new candidate and run directory.")
    profile_path.write_text(json.dumps(profile, indent=2), encoding="utf8")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
    meta = run / "run.json"
    started = time.perf_counter()
    todo = [p for p in pdfs if p not in done]
    print(f"{len(pdfs)} PDFs selected, {len(done)} already done, {len(todo)} to run; scanned math {args.scanned_math}; HEAD {head[:10]}"
          + (" (tree has uncommitted changes)" if dirty else ""), flush=True)
    for i, pdf in enumerate(todo, 1):
        waited = time.perf_counter()
        while (frozen.free_ram() or frozen.MIN_FREE_RAM) < frozen.MIN_FREE_RAM and time.perf_counter() - waited < 180:
            time.sleep(2)
        index = len(done) + 1
        res, logfile = run / "docs" / f"{index:05}.json", run / "docs" / f"{index:05}.log"
        cmd = [py, "-B", "-X", "utf8", str(ROOT / "scripts" / "frozen70_benchmark.py"), "worker", str(run), "--data-root", str(data),
               "--output", str(cand), "--mode", "adapter", "--pdf", pdf, "--result", str(res)]
        t0, stop = time.perf_counter(), None
        with logfile.open("w", encoding="utf8") as log:
            proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                proc.wait(timeout=hard_timeout)
            except subprocess.TimeoutExpired:
                frozen.kill_tree(proc)
                proc.wait()
                stop = f"hard_timeout_{hard_timeout}s"
        md = cand / Path(pdf).parent / (Path(pdf).stem + "_pg1_repeat1.md")
        row = json.loads(res.read_text(encoding="utf8")) if res.exists() else {"pdf": pdf, "errors": [stop or f"worker_exit_{proc.returncode}"]}
        rec = {k: v for k, v in row.items() if k not in ("blocks", "validation", "stages", "source_sha256")}
        rec.update(pdf=pdf, wall_seconds=round(time.perf_counter() - t0, 2), stop_reason=stop, exit_code=proc.returncode,
                   completed=md.is_file() and md.stat().st_size > 0, min_free_ram_bytes=frozen.free_ram())
        with ledger.open("a", encoding="utf8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        res.unlink(missing_ok=True)  # the ledger keeps the metrics; the per-document block dump is not needed for scoring
        done[pdf] = rec
        print(f"{len(done)}/{len(pdfs)} {pdf}: completed={rec['completed']} {rec['wall_seconds']}s {stop or ''}", flush=True)
    rows = [done[p] for p in pdfs if p in done]
    placeholders: list[str] = []
    if not args.limit and not args.category:
        placeholders = fill_missing(data, cand)
        if placeholders:
            print(f"{len(placeholders)} page(s) produced no Markdown and were recorded as empty files (see run.json).")
    meta.write_text(json.dumps({"empty_placeholders": placeholders,
        "source_head": head, "tree_dirty": dirty, "scanned_math": args.scanned_math, "vlm": args.vlm,
        "vlm_model": env.get("AXTRACT_VLM_MODEL_ID", "LightOnOCR-3-0.8B-Q8_0") if args.vlm != "off" else None, "python": py, "candidate": str(cand),
        "ocr_provider": provider(), "azure_deployment": cloud_config["deployment"] if cloud_config else None,
        "dataset_revision": frozen.manifest()["dataset"]["revision"], "documents": len(rows),
        "completed": sum(r["completed"] for r in rows), "hard_timeouts": sum(1 for r in rows if r.get("stop_reason")),
        "sum_document_seconds": round(sum(r.get("seconds", r["wall_seconds"]) for r in rows), 1),
        "wall_seconds_this_session": round(time.perf_counter() - started, 1)}, indent=1), encoding="utf8")
    print(f"extraction finished: {sum(r['completed'] for r in rows)}/{len(rows)} outputs, "
          f"{sum(1 for r in rows if r.get('stop_reason'))} hard timeouts -> {cand}  (run record: {run})")
    return 0


def cmd_score(args) -> int:
    data = Path(args.data_root).resolve()
    man = frozen.manifest()
    tests = Path(args.scorer) / "olmocr" / "bench" / "tests.py"
    if frozen.sha256(tests) != man["scorer"]["tests_py_sha256"]:
        raise SystemExit(f"ERROR: {tests} is not the scorer version recorded in the manifest (commit {man['scorer']['commit']}).")
    if not (data / args.candidate_name).is_dir():
        raise SystemExit(f"ERROR: candidate folder {data / args.candidate_name} not found (the official scorer looks for it inside --data-root).")
    run = Path(args.run_dir or ROOT / "runs" / f"full_{args.candidate_name}").resolve()
    run.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PYTHONPATH=os.pathsep.join(p for p in (str(args.scorer), args.scorer_packages) if p))
    cmd = [args.scorer_python, "-X", "utf8", str(Path(__file__).resolve().parent / "official_scorer_shim.py"), "--dir", str(data), "--candidate", args.candidate_name,
           "--force", "--skip_baseline", "--output_failed", str(run / "failed_tests.jsonl")]
    missing = missing_pages(data, data / args.candidate_name)
    if missing and args.fill_missing:
        filled = fill_missing(data, data / args.candidate_name)
        print(f"{len(filled)} page(s) had no Markdown and were recorded as empty files.")
    elif missing:
        raise SystemExit(f"ERROR: {len(missing)} assertion page(s) have no Markdown (first: {missing[0][0]}). The official scorer would report the whole "
                         "candidate as FAILED (errors). Finish the extraction, or pass --fill-missing to record them as empty files.")
    print("running:", " ".join(cmd), flush=True)
    done = subprocess.run(cmd, cwd=args.scorer, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    (run / "official_score.txt").write_text(done.stdout + "\n--- stderr ---\n" + done.stderr, encoding="utf8")
    print("\n".join(done.stdout.strip().splitlines()[-25:]))
    if done.returncode:
        print(done.stderr[-2000:], file=sys.stderr)
    print(f"full official scorer output: {run / 'official_score.txt'}")
    return done.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--data-root", required=True)
    e.add_argument("--candidate-name", default="axtract_v2")
    e.add_argument("--run-dir")
    e.add_argument("--scanned-math", choices=["require", "off"], default="require")
    e.add_argument("--python")
    e.add_argument("--resume", action="store_true")
    e.add_argument("--limit", type=int)
    e.add_argument("--category")
    e.add_argument("--allow-asset-overrides", action="store_true")
    e.add_argument("--allow-dataset-mismatch", action="store_true")
    e.add_argument("--vlm", choices=["off", "scanned", "all"], default="off", help="VLM OCR engine mode (AXTRACT_VLM_OCR)")
    s = sub.add_parser("score")
    s.add_argument("--data-root", required=True)
    s.add_argument("--candidate-name", default="axtract_v2")
    s.add_argument("--run-dir")
    s.add_argument("--scorer-python", required=True)
    s.add_argument("--scorer", required=True)
    s.add_argument("--scorer-packages")
    s.add_argument("--fill-missing", action="store_true")
    args = ap.parse_args()
    return {"extract": cmd_extract, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
