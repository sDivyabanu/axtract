#!/usr/bin/env python3
"""Performance benchmarking for AXTRACT.

Measures:
  - Total processing time
  - Processing time per page
  - Pages per minute throughput
  - Extractor usage breakdown
  - Failure/warning rates
  - Block count by type

Usage:
  python benchmark.py <file_or_directory> [--runs N]

Processes each supported file and reports aggregate metrics.
All processing is local (no external API costs).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "backend"))

from extractors.registry import get_extractor
from services.layout_service import assign_reading_order
from services.markdown_service import blocks_to_markdown
from utils.files import SUPPORTED_EXTENSIONS, get_extension


def benchmark_file(file_path: Path) -> dict:
    """Process a single file and return metrics."""
    ext = get_extension(file_path.name)
    extractor = get_extractor(ext)
    if extractor is None:
        return {"file": file_path.name, "error": "unsupported format"}

    start = time.perf_counter()
    try:
        result = extractor.extract(file_path)
        # Apply layout and markdown
        ordered = assign_reading_order(result.blocks)
        md = blocks_to_markdown(ordered)
        elapsed = time.perf_counter() - start
    except Exception as e:
        return {
            "file": file_path.name,
            "error": str(e),
            "time_ms": round((time.perf_counter() - start) * 1000),
        }

    # Collect metrics
    type_counts: dict[str, int] = {}
    extractors_used: set[str] = set()
    confidence_values: list[float] = []
    review_count = 0

    for block in ordered:
        type_counts[block.type] = type_counts.get(block.type, 0) + 1
        extractors_used.add(block.extractor)
        if block.requires_review:
            review_count += 1
        if block.confidence is not None:
            confidence_values.append(block.confidence)

    time_ms = round(elapsed * 1000)
    pages = result.page_count or 1

    return {
        "file": file_path.name,
        "file_type": ext,
        "pages": pages,
        "blocks": len(ordered),
        "errors": len(result.errors),
        "time_ms": time_ms,
        "ms_per_page": round(time_ms / pages, 1),
        "pages_per_minute": round(pages / elapsed * 60, 1) if elapsed > 0 else 0,
        "type_counts": type_counts,
        "extractors": sorted(extractors_used),
        "review_blocks": review_count,
        "markdown_chars": len(md),
        "avg_confidence": (
            round(sum(confidence_values) / len(confidence_values), 4)
            if confidence_values else None
        ),
    }


def main():
    parser = argparse.ArgumentParser(description="AXTRACT Benchmark")
    parser.add_argument("path", help="File or directory to benchmark")
    parser.add_argument("--runs", type=int, default=1, help="Repetitions per file")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    target = Path(args.path)
    files: list[Path] = []

    if target.is_file():
        files = [target]
    elif target.is_dir():
        for ext in SUPPORTED_EXTENSIONS:
            files.extend(target.glob(f"*.{ext}"))
    else:
        print(f"Not found: {target}")
        sys.exit(1)

    if not files:
        print("No supported files found.")
        sys.exit(1)

    all_results: list[dict] = []
    for f in sorted(files):
        best = None
        for _ in range(args.runs):
            r = benchmark_file(f)
            if best is None or r.get("time_ms", 99999) < best.get("time_ms", 99999):
                best = r
        all_results.append(best)

    if args.json:
        print(json.dumps(all_results, indent=2))
        return

    # Print summary
    print(f"{'File':<30} {'Type':<6} {'Pages':>5} {'Blocks':>6} {'Time ms':>8} {'ms/pg':>7} {'pg/min':>7} {'Errors':>6}")
    print("-" * 100)
    total_pages = 0
    total_time = 0
    total_blocks = 0
    total_errors = 0

    for r in all_results:
        if "error" in r and "pages" not in r:
            print(f"{r['file']:<30} ERROR: {r['error']}")
            continue
        print(
            f"{r['file']:<30} {r.get('file_type','?'):<6} "
            f"{r.get('pages',0):>5} {r.get('blocks',0):>6} "
            f"{r.get('time_ms',0):>8} {r.get('ms_per_page',0):>7} "
            f"{r.get('pages_per_minute',0):>7.0f} {r.get('errors',0):>6}"
        )
        total_pages += r.get("pages", 0)
        total_time += r.get("time_ms", 0)
        total_blocks += r.get("blocks", 0)
        total_errors += r.get("errors", 0)

    print("-" * 100)
    if total_pages > 0:
        print(
            f"{'TOTAL':<30} {'':6} {total_pages:>5} {total_blocks:>6} "
            f"{total_time:>8} {total_time/total_pages:>7.1f} "
            f"{total_pages/(total_time/1000)*60:>7.0f} {total_errors:>6}"
        )
    print(f"\nFiles: {len(all_results)}")
    print(f"All processing is local — no external API costs.")
    print(f"Infrastructure cost: compute time on this machine only.")


if __name__ == "__main__":
    main()
