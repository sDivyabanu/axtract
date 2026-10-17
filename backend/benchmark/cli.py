"""Command-line access to the Smart Benchmark Runner (no web server or login needed).

    python -m benchmark.cli categories
    python -m benchmark.cli run --categories tables,multi_column --per-category 2 --workers 1
    python -m benchmark.cli resume qk-20261016T120000Z-ab12cd34
    python -m benchmark.cli recommend [--base main]
    python -m benchmark.cli compare RUN_A RUN_B
    python -m benchmark.cli import current_05deead/official_05deead_001
    python -m benchmark.cli list | status RUN | cases RUN [--limit 20]

Run it from the backend folder. Paths come from the same AXTRACT_BENCH_* environment variables as the API.
Ctrl-C during `run` or `resume` cancels cleanly: finished documents are kept and the run can be resumed.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading

from . import changes, compare, importer, procutil
from .cases import CaseBrowser
from .runner import BenchmarkRunner, RunError


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _execute(runner: BenchmarkRunner, run_id: str) -> int:
    if procutil.official_benchmark_active():
        print("The official benchmark is running. Start a benchmark after it finishes.", file=sys.stderr)
        return 3
    cancel = threading.Event()
    _result: dict = {}
    worker = threading.Thread(target=lambda: _result.update(runner.execute(run_id, cancel)), daemon=True)
    worker.start()
    try:
        while worker.is_alive():
            worker.join(1.0)
            st = runner.status(run_id)
            c = st["counts"] or {}
            print(f"\r{st['status']}/{st['phase']}: {c.get('done', 0)} ok, {c.get('failed', 0)} failed, {c.get('pending', 0)} left   ", end="", flush=True)
    except KeyboardInterrupt:
        print("\nCancelling… finished documents are kept.", file=sys.stderr)
        cancel.set()
        worker.join(120)
    print()
    status = runner.status(run_id)
    _print({k: status[k] for k in ("run_id", "status", "score_kind", "is_official", "counts", "elapsed_s", "error")})
    try:
        res = runner.results(run_id)
        _print({k: res.get(k) for k in ("validity", "completeness", "overall_score_pct", "diagnostic_score_pct", "categories", "documents", "cases", "notes")})
    except RunError:
        pass
    return 0 if status["status"] == "completed" else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="benchmark", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("categories")
    sub.add_parser("list")
    r = sub.add_parser("run")
    r.add_argument("--mode", choices=["quick", "full"], default="quick")
    r.add_argument("--categories", default="all", help="comma-separated official category names, or 'all'")
    r.add_argument("--per-category", type=int, default=5)
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--workers", type=int, default=2)
    r.add_argument("--timeout", type=int, default=300, help="seconds per PDF")
    r.add_argument("--confirm-full", action="store_true", help="required for --mode full")
    for name in ("resume", "status"):
        sub.add_parser(name).add_argument("run_id")
    c = sub.add_parser("cases")
    c.add_argument("run_id")
    c.add_argument("--outcome", choices=["failed", "passed", "all"], default="failed")
    c.add_argument("--limit", type=int, default=20)
    rec = sub.add_parser("recommend")
    rec.add_argument("--base")
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("a")
    cmp_.add_argument("b")
    cmp_.add_argument("--partial", action="store_true")
    imp = sub.add_parser("import")
    imp.add_argument("source")
    args = ap.parse_args(argv)

    runner = BenchmarkRunner()
    try:
        if args.cmd == "categories":
            _print([{"name": c.name, "pdfs": c.pdf_count, "tests": c.test_count} for c in runner.categories().values()])
        elif args.cmd == "list":
            _print(runner.history())
        elif args.cmd == "status":
            _print(runner.status(args.run_id))
        elif args.cmd == "cases":
            _print(CaseBrowser(runner).list(args.run_id, outcome=args.outcome, limit=args.limit))
        elif args.cmd == "run":
            names = [x for x in args.categories.split(",") if x]
            run_id = runner.create_run(mode=args.mode, categories=names, per_category=None if args.mode == "full" else args.per_category,
                                       seed=args.seed, workers=args.workers, timeout_s=args.timeout, confirm_full=args.confirm_full)
            print(f"created {run_id}")
            return _execute(runner, run_id)
        elif args.cmd == "resume":
            return _execute(runner, args.run_id)
        elif args.cmd == "recommend":
            last = next((h["git"]["sha"] for h in runner.history() if h["status"] == "completed" and h["git"].get("sha")), None)
            _print(changes.recommend(runner.settings.repo_root, list(runner.categories()), args.base, last))
        elif args.cmd == "compare":
            _print(compare.compare_runs(runner, args.a, args.b, args.partial))
        elif args.cmd == "import":
            _print(importer.import_official(runner, args.source))
    except RunError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 2
    except changes.ChangeError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
