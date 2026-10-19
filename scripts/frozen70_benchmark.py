"""Reproduce the frozen 70-PDF / 430-assertion olmOCR-bench DIAGNOSTIC for AXTRACT (not full-corpus accuracy).

The sample, its PDF hashes, assertion ids, scorer commit and reference results are in evaluation/frozen70/manifest.json.
Inputs you provision once (this script never downloads anything): the olmOCR-bench `bench_data` folder, the allenai/olmocr
scorer checkout at the recorded commit, and a Python 3.12 scorer interpreter (see docs/FROZEN70_REPRODUCTION.md).

  python scripts/frozen70_benchmark.py extract --data-root BENCH_DATA --out OUT [--scanned-math require|off]
  python scripts/frozen70_benchmark.py score   --data-root BENCH_DATA --out OUT --scorer-python PY --scorer SCORER_REPO [--scorer-packages SITE]
  python scripts/frozen70_benchmark.py report  --out OUT [--scanned-math require|off]

Extraction runs ONE document at a time in its own process (75 s hard limit, 60 s pipeline deadline) and waits for free RAM
instead of failing. With `--scanned-math require` a missing recognizer package or model file is an error before the first
document, never a silent fallback. The unchanged official scorer (olmocr.bench.tests.load_single_test) judges every assertion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "evaluation" / "frozen70" / "manifest.json"
CATEGORIES = ["arxiv_math", "headers_footers", "long_tiny_text", "multi_column", "old_scans", "old_scans_math", "tables"]
HARD_TIMEOUT_S = 75
MIN_FREE_RAM = 1400 * 1024 ** 2
OVERRIDES_REMOVED = ("AXTRACT_MFR_DIR", "AXTRACT_FORMULA_SITE", "AXTRACT_MODEL_DIR")


def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf8"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def free_ram() -> int | None:
    if os.name != "nt":
        return None
    import ctypes

    class Memory(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (n, ctypes.c_ulonglong) for n in ("total", "available", "total_page", "avail_page", "total_virtual", "avail_virtual", "extended")]

    m = Memory(); m.length = ctypes.sizeof(m)
    return m.available if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)) else None


def environment(scanned_math: str, allow_overrides: bool) -> dict:
    env = dict(os.environ)
    if not allow_overrides:
        for key in OVERRIDES_REMOVED:  # a reproduction must use the repository's own default asset locations
            env.pop(key, None)
    env.update({"AXTRACT_SCANNED_MATH": "require" if scanned_math == "require" else "0", "AXTRACT_FORMULA_SCANNED": "0",
                "AXTRACT_VERIFY": "1", "AXTRACT_ORT_THREADS": "2", "AXTRACT_OCR_ARENA": "0", "OMP_NUM_THREADS": "2",
                "OPENBLAS_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"})
    return env


def apply_vlm(env: dict, mode: str) -> None:
    """Opt into the VLM OCR engine for a benchmark run: set its mode, give each document a longer hard limit, and stop
    before the first document if the model server is not answering (otherwise every page would silently fall back)."""
    global HARD_TIMEOUT_S
    env["AXTRACT_VLM_OCR"] = mode
    if mode == "off":
        return
    HARD_TIMEOUT_S = int(env.get("AXTRACT_VLM_HARD_SECONDS", "900"))
    url = env.get("AXTRACT_VLM_URL", "http://127.0.0.1:8090").rstrip("/")
    import urllib.request
    try:
        with urllib.request.urlopen(url + "/health", timeout=10) as r:
            ok = r.status == 200
    except OSError:
        ok = False
    if not ok:
        raise SystemExit(f"ERROR: --vlm {mode} but no llama-server answers at {url}/health. Start it first (docs/VLM_OCR.md).")


def load_cases(data_root: Path, man: dict) -> list[dict]:
    for name, want in man["dataset"]["case_source_sha256"].items():
        got = sha256(data_root / name)
        if got != want:
            raise SystemExit(f"ERROR: {data_root / name} differs from the frozen sample's assertion file (sha256 {got[:12]} != {want[:12]}).")
    wanted = {i for ids in man["cases"].values() for i in ids}
    cases = []
    for name in man["dataset"]["case_source_sha256"]:
        for line in (data_root / name).read_text(encoding="utf8").splitlines():
            row = json.loads(line) if line.strip() else None
            if row and row.get("id") in wanted:
                cases.append(row)
    if len(cases) != man["assertion_count"]:
        raise SystemExit(f"ERROR: found {len(cases)} of {man['assertion_count']} frozen assertions in {data_root}.")
    return cases


def kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


def cmd_extract(args) -> int:
    man, data, out = manifest(), Path(args.data_root).resolve(), Path(args.out).resolve()
    py = args.python or sys.executable
    for pdf, want in man["pdfs"].items():
        if not (data / "pdfs" / pdf).is_file() or sha256(data / "pdfs" / pdf) != want:
            raise SystemExit(f"ERROR: {pdf} is missing or differs from the frozen sample (check the bench_data revision "
                             f"{man['dataset']['revision']}).")
    cases = load_cases(data, man)
    env = environment(args.scanned_math, args.allow_asset_overrides)
    apply_vlm(env, args.vlm)
    if args.scanned_math == "require":  # fail loudly before any document if the recognizer cannot load
        check = subprocess.run([py, "-B", str(ROOT / "scripts" / "scanned_math_assets.py"), "check"], env=env, cwd=ROOT,
                               capture_output=True, text=True)
        if check.returncode:
            raise SystemExit("ERROR: scanned math is required but the recognizer cannot load:\n" + check.stdout + check.stderr
                             + "\nFollow docs/SCANNED_MATH_SETUP.md, then retry.")
    out.mkdir(parents=True, exist_ok=False)
    (out / "selected_original_cases.json").write_text(json.dumps(cases), encoding="utf8")
    cand = out / "candidate"; cand.mkdir()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
    docs, started = [], time.perf_counter()
    for i, pdf in enumerate(man["pdfs"]):
        waited = time.perf_counter()
        while (free_ram() or MIN_FREE_RAM) < MIN_FREE_RAM and time.perf_counter() - waited < 180:
            time.sleep(2)
        res = out / f"document_{i:02}.json"
        cmd = [py, "-B", "-X", "utf8", str(Path(__file__)), "worker", str(out), "--data-root", str(data), "--output", str(cand),
               "--mode", "adapter", "--pdf", pdf, "--result", str(res)]
        t0, stop = time.perf_counter(), None
        with (out / f"document_{i:02}.log").open("w", encoding="utf8") as log:
            proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                proc.wait(timeout=HARD_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                kill_tree(proc); proc.wait(); stop = f"hard_timeout_{HARD_TIMEOUT_S}s"
        md = cand / Path(pdf).parent / (Path(pdf).stem + "_pg1_repeat1.md")
        row = json.loads(res.read_text(encoding="utf8")) if res.exists() else {"pdf": pdf, "errors": [stop or f"worker_exit_{proc.returncode}"]}
        rec = {k: v for k, v in row.items() if k not in ("blocks", "validation")}
        rec.update(pdf=pdf, wall_seconds=round(time.perf_counter() - t0, 2), stop_reason=stop, exit_code=proc.returncode,
                   completed=md.is_file() and md.stat().st_size > 0, min_free_ram_bytes=free_ram())
        docs.append(rec)
        print(f"{i + 1}/{len(man['pdfs'])} {pdf}: completed={rec['completed']} {rec['wall_seconds']}s {stop or ''}", flush=True)
    (out / "performance.json").write_text(json.dumps({
        "source_head": head, "tree_dirty": dirty, "scanned_math": args.scanned_math, "vlm": args.vlm, "python": py,
        "documents": docs, "wall_seconds": round(time.perf_counter() - started, 1)}, indent=1), encoding="utf8")
    print(f"extraction finished: {sum(d['completed'] for d in docs)}/{len(docs)} outputs, "
          f"{sum(1 for d in docs if d['stop_reason'])} hard timeouts -> {out}")
    return 0


def cmd_worker(args, rest) -> int:  # runs inside the app interpreter, one PDF
    sys.path.insert(0, str(ROOT / "backend"))
    from services import preview_service

    out = Path(args.out)
    preview_service.PREVIEW_ROOT = out / "preview-artifacts"
    preview_service.PERSISTENT_PREVIEW_ROOT = out / "persistent-preview-artifacts"
    import runpy
    sys.argv = [str(ROOT / "scripts" / "integration_accuracy.py"), "worker", *rest]
    runpy.run_path(str(ROOT / "scripts" / "integration_accuracy.py"), run_name="__main__")
    return 0


def cmd_score(args) -> int:
    out = Path(args.out).resolve()
    man = manifest()
    tests = Path(args.scorer) / "olmocr" / "bench" / "tests.py"
    if sha256(tests) != man["scorer"]["tests_py_sha256"]:
        raise SystemExit(f"ERROR: {tests} is not the scorer version used for the frozen results (commit {man['scorer']['commit']}).")
    for cat in CATEGORIES:
        cmd = [args.scorer_python, "-B", "-X", "utf8", str(Path(__file__)), "score-worker", str(out), cat, "--scorer", args.scorer]
        if args.scorer_packages:
            cmd += ["--packages", args.scorer_packages]
        done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
        print(done.stdout.strip().splitlines()[-1] if done.stdout.strip() else f"{cat}: no output", flush=True)
        if done.returncode:
            raise SystemExit(f"ERROR: scoring {cat} failed:\n{done.stderr[-2000:]}")
    return 0


def cmd_score_worker(args) -> int:  # runs inside the scorer interpreter
    sys.path[:0] = [p for p in (args.scorer, args.packages) if p]  # the pinned checkout always wins
    from olmocr.bench.tests import load_single_test

    out = Path(args.out)
    cases = json.loads((out / "selected_original_cases.json").read_text(encoding="utf8"))
    attempted = {d["pdf"] for d in json.loads((out / "performance.json").read_text())["documents"]}
    rows = []
    with (out / f"score_{args.category}.jsonl").open("x", encoding="utf8") as ledger:
        for case in cases:
            if not case["pdf"].startswith(args.category + "/") or case["pdf"] not in attempted:
                continue
            md = out / "candidate" / Path(case["pdf"]).parent / (Path(case["pdf"]).stem + f'_pg{case["page"]}_repeat1.md')
            error = None
            try:
                passed, why = load_single_test(case).run(md.read_text(encoding="utf8")) if md.exists() else (False, "Missing output")
            except Exception as exc:  # noqa: BLE001 - recorded like any other failure
                passed, why, error = None, str(exc), type(exc).__name__
            row = {"id": case["id"], "pdf": case["pdf"], "type": case["type"], "passed": passed, "explanation": why, "scorer_error": error}
            rows.append(row)
            ledger.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{args.category}: scored {len(rows)} passed {sum(r['passed'] is True for r in rows)}")
    return 0


def cmd_report(args) -> int:
    out, man = Path(args.out), manifest()
    ref = man["reference"]["scanned_math_" + ("on" if args.scanned_math == "require" else "off")]
    rows = {}
    for cat in CATEGORIES:
        for line in (out / f"score_{cat}.jsonl").read_text(encoding="utf8").splitlines():
            if line.strip():
                r = json.loads(line); rows[r["id"]] = r
    perf = json.loads((out / "performance.json").read_text())
    total = {c: [0, 0] for c in CATEGORIES}
    for r in rows.values():
        total[r["pdf"].split("/")[0]][1] += 1
        total[r["pdf"].split("/")[0]][0] += r["passed"] is True
    ok = len(rows) == man["assertion_count"]
    print(f"{'category':16s}{'passed/total':>14s}{'pct':>9s}  reference")
    for c in CATEGORIES:
        p, t = total[c]; lo, hi = ref["categories"][c]
        within = lo <= p <= hi; ok &= within
        print(f"{c:16s}{f'{p}/{t}':>14s}{100 * p / t:>8.2f}%  {lo}" + (f"-{hi}" if hi != lo else "") + ("" if within else "   <-- outside reference"))
    macro = sum(p / t for p, t in total.values()) / len(total) * 100
    print(f"total {sum(p for p, _ in total.values())}/{len(rows)}   macro {macro:.2f}%   micro {100 * sum(p for p, _ in total.values()) / len(rows):.2f}%")
    docs = perf["documents"]
    print(f"documents completed {sum(d['completed'] for d in docs)}/{len(docs)}; hard timeouts {sum(1 for d in docs if d.get('stop_reason'))}; "
          f"sum of document times {sum(d['wall_seconds'] for d in docs):.0f}s; slowest {max(d['wall_seconds'] for d in docs):.0f}s; "
          f"source {perf['source_head'][:10]}{' (dirty tree)' if perf['tree_dirty'] else ''}")
    print("RESULT:", "MATCHES the frozen reference" if ok else "DIFFERS from the frozen reference (see above)")
    return 0 if ok else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract"); e.add_argument("--data-root", required=True); e.add_argument("--out", required=True)
    e.add_argument("--scanned-math", choices=["require", "off"], default="require"); e.add_argument("--python")
    e.add_argument("--allow-asset-overrides", action="store_true")
    e.add_argument("--vlm", choices=["off", "scanned", "all"], default="off", help="VLM OCR engine mode (AXTRACT_VLM_OCR)")
    s = sub.add_parser("score"); s.add_argument("--data-root"); s.add_argument("--out", required=True)
    s.add_argument("--scorer-python", required=True); s.add_argument("--scorer", required=True); s.add_argument("--scorer-packages")
    r = sub.add_parser("report"); r.add_argument("--out", required=True); r.add_argument("--scanned-math", choices=["require", "off"], default="require")
    w = sub.add_parser("worker"); w.add_argument("out")
    sw = sub.add_parser("score-worker"); sw.add_argument("out"); sw.add_argument("category"); sw.add_argument("--scorer", required=True); sw.add_argument("--packages")
    args, rest = parser.parse_known_args()
    return {"extract": cmd_extract, "score": cmd_score, "report": cmd_report, "score-worker": cmd_score_worker,
            "worker": lambda a: cmd_worker(a, rest)}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
