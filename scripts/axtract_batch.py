#!/usr/bin/env python3
"""Convert a directory tree of PDFs to one Markdown file per PDF."""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.parse_service import parse_path  # noqa: E402


def _convert(pdf_path: Path, relative_path: Path, output_dir: Path) -> tuple[Path, float, str | None]:
    started = time.perf_counter()
    target = (output_dir / relative_path).with_suffix(".md")
    try:
        response = parse_path(pdf_path, pdf_path.name, persistent_preview=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(response.markdown or "", encoding="utf-8")
        return target, time.perf_counter() - started, None
    except Exception as exc:  # each file is isolated so one bad PDF does not stop the batch
        return target, time.perf_counter() - started, f"{type(exc).__name__}: {exc}"


def run_batch(input_dir: Path, output_dir: Path, workers: int = 1) -> dict:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    if not input_dir.is_dir():
        raise ValueError(f"Input directory does not exist: {input_dir}")
    if workers < 1:
        raise ValueError("workers must be at least 1")

    pdfs = sorted(path for path in input_dir.rglob("*.pdf") if path.is_file())
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_convert, path, path.relative_to(input_dir), output_dir): path
            for path in pdfs
        }
        for future in as_completed(futures):
            results.append((futures[future], *future.result()))

    failures = [(source, error) for source, _, _, error in results if error]
    durations = [duration for _, _, duration, error in results if not error]
    return {
        "total": len(pdfs),
        "succeeded": len(pdfs) - len(failures),
        "failed": failures,
        "median_seconds": statistics.median(durations) if durations else 0.0,
        "output_dir": output_dir,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path, help="Directory containing PDFs (searched recursively)")
    parser.add_argument("output_dir", type=Path, help="Directory for Markdown output")
    parser.add_argument("--workers", type=int, default=1, help="Concurrent PDF workers (default: 1)")
    args = parser.parse_args(argv)
    # Same configuration source as the API server; variables already set in the shell take precedence.
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / "backend" / ".env", override=False)

    try:
        summary = run_batch(args.input_dir, args.output_dir, args.workers)
    except ValueError as exc:
        parser.error(str(exc))

    print(
        f"Processed {summary['succeeded']}/{summary['total']} PDFs; "
        f"failed {summary['failed']}; median {summary['median_seconds']:.2f}s/PDF; "
        f"output: {summary['output_dir']}"
    )
    for source, error in summary["failed"]:
        print(f"FAILED {source}: {error}", file=sys.stderr)
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
