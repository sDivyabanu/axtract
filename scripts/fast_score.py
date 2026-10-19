"""Score a candidate folder with the official olmOCR-Bench test classes, one assertion at a time, categories in parallel.

Each assertion is evaluated by the scorer's own `olmocr.bench.tests.load_single_test(case).run(markdown)` (the code the
official CLI runs), in the pinned scorer checkout and interpreter; nothing about tests, thresholds or data is changed.
What differs from the official CLI is only scheduling: no per-batch process and browser start-up, and the seven category
files run as separate processes. Validated against the official CLI on the full 7,010-assertion 50.09 % run: 0 of 7,010
assertions disagree and every category total matches. The official CLI remains the source of the headline score.

    python scripts/fast_score.py --data-root <bench_data> --candidate <bench_data>/<candidate> --out runs/score_x \
        --scorer-python <py312> --scorer <olmocr checkout> --scorer-packages <site-packages> --jobs 4
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "scripts" / "frozen70_benchmark.py"
CATEGORIES = ["arxiv_math", "headers_footers", "long_tiny_text", "multi_column", "old_scans", "old_scans_math", "tables"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", required=True, help="folder with the assertion *.jsonl files")
    ap.add_argument("--candidate", required=True, help="folder with <category>/<pdf stem>_pg<page>_repeat1.md")
    ap.add_argument("--out", required=True)
    ap.add_argument("--scorer-python", required=True)
    ap.add_argument("--scorer", required=True)
    ap.add_argument("--scorer-packages")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()

    data, out = Path(args.data_root).resolve(), Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    cases = [json.loads(line) for f in sorted(data.glob("*.jsonl")) for line in f.read_text(encoding="utf8").splitlines()
             if line.strip()]
    cases = [c for c in cases if c.get("type") != "baseline"]  # the official scorer skips baseline sanity rows too
    (out / "selected_original_cases.json").write_text(json.dumps(cases), encoding="utf8")
    (out / "performance.json").write_text(json.dumps({"documents": [{"pdf": p} for p in sorted({c["pdf"] for c in cases})]}),
                                          encoding="utf8")
    (out / "candidate").symlink_to(Path(args.candidate).resolve(), target_is_directory=True) if sys.platform != "win32" else \
        subprocess.run(["cmd", "/c", "mklink", "/J", str(out / "candidate"), str(Path(args.candidate).resolve())],
                       check=True, capture_output=True)
    present = {c["pdf"].split("/")[0] for c in cases}

    def one(cat: str) -> str:
        cmd = [args.scorer_python, "-B", "-X", "utf8", str(FROZEN), "score-worker", str(out), cat, "--scorer", args.scorer]
        if args.scorer_packages:
            cmd += ["--packages", args.scorer_packages]
        with (out / f"score_{cat}.log").open("w", encoding="utf8") as log:
            done = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8", errors="replace")
        if done.returncode:
            raise RuntimeError(f"scoring {cat} failed, see {out / f'score_{cat}.log'}")
        return done.stdout.strip().splitlines()[-1]

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max(1, args.jobs)) as pool:
        for line in pool.map(one, [c for c in CATEGORIES if c in present]):
            print(line, flush=True)
    totals: dict[str, list[int]] = {}
    errors = 0
    for cat in CATEGORIES:
        f = out / f"score_{cat}.jsonl"
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf8").splitlines():
            r = json.loads(line)
            t = totals.setdefault(cat, [0, 0])
            t[0] += r["passed"] is True
            t[1] += 1
            errors += bool(r.get("scorer_error"))
    passed, total = sum(p for p, _ in totals.values()), sum(t for _, t in totals.values())
    summary = {"categories": {c: {"passed": p, "total": t, "percentage": 100 * p / t} for c, (p, t) in totals.items()},
               "macro_percentage": 100 * sum(p / t for p, t in totals.values()) / len(totals),
               "micro_percentage": 100 * passed / total, "passed": passed, "total": total, "scorer_exceptions": errors,
               "cases_expected": len(cases), "wall_seconds": round(time.perf_counter() - t0, 1)}
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf8")
    print(json.dumps(summary, indent=1))
    return 0 if total == len(cases) else 2


if __name__ == "__main__":
    raise SystemExit(main())
