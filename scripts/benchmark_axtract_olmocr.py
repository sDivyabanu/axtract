#!/usr/bin/env python3
"""AXTRACT Official Benchmark Adapter for olmOCR-bench.

Processes benchmark PDFs with AXTRACT's extraction engine and exports
standardized per-page Markdown for scoring by the official olmOCR scorer.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add backend to Python path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.parse_service import parse_path
from services.markdown_service import blocks_to_markdown
import tracemalloc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("axtract_benchmark")


def process_single_pdf(
    pdf_path: Path,
    relative_path: Path,
    output_base_dir: Path,
    enable_verify: bool = True,
) -> Dict[str, Any]:
    """Process a single PDF using AXTRACT and export per-page Markdown files."""
    start_time = time.perf_counter()
    category = relative_path.parts[0] if len(relative_path.parts) > 1 else "root"
    pdf_stem = pdf_path.stem
    result_meta: Dict[str, Any] = {
        "pdf": str(relative_path).replace("\\", "/"),
        "category": category,
        "filename": pdf_path.name,
        "success": False,
        "pages": 0,
        "blocks": 0,
        "duration_ms": 0.0,
        "verify_ms": 0.0,
        "error": None,
    }

    if not enable_verify:
        os.environ["AXTRACT_VERIFY"] = "0"
    else:
        os.environ["AXTRACT_VERIFY"] = "1"

    try:
        t0 = time.perf_counter()
        doc_resp = parse_path(pdf_path, pdf_path.name, persistent_preview=False)
        t_parse = time.perf_counter()
        
        pages = doc_resp.page_count or 1
        blocks = doc_resp.blocks or []
        result_meta["pages"] = pages
        result_meta["blocks"] = len(blocks)
        result_meta["duration_ms"] = (t_parse - t0) * 1000.0

        # Calculate verify overhead if available
        if doc_resp.validation and isinstance(doc_resp.validation, dict):
            summary = doc_resp.validation.get("summary", {})
            result_meta["verify_ms"] = float(summary.get("duration_ms", 0.0))

        # Target directory matches the relative subfolder structure
        rel_parent = relative_path.parent
        target_dir = output_base_dir / rel_parent
        target_dir.mkdir(parents=True, exist_ok=True)

        # olmOCR-bench expects 1-indexed pages: {pdf_stem}_pg{page}_repeat1.md
        for page_num in range(1, pages + 1):
            page_blocks = [b for b in blocks if getattr(b, "page", 1) == page_num]
            page_md = blocks_to_markdown(page_blocks)
            
            out_file = target_dir / f"{pdf_stem}_pg{page_num}_repeat1.md"
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(page_md)

        result_meta["success"] = True
    except Exception as exc:
        result_meta["error"] = f"{type(exc).__name__}: {str(exc)}"
        logger.error(f"Failed processing {pdf_path.name}: {exc}\n{traceback.format_exc()}")

    total_time = (time.perf_counter() - start_time) * 1000.0
    result_meta["total_elapsed_ms"] = total_time
    return result_meta


def run_benchmark(
    bench_dir: Path,
    output_dir: Path,
    report_dir: Path,
    category_filter: Optional[str] = None,
    sample_limit: Optional[int] = None,
    workers: int = 4,
    enable_verify: bool = True,
) -> Dict[str, Any]:
    """Run AXTRACT on the benchmark dataset."""
    pdf_dir = bench_dir / "pdfs"
    if not pdf_dir.exists():
        raise FileNotFoundError(f"PDF directory not found at {pdf_dir}")

    all_pdfs: List[Tuple[Path, Path]] = []
    for p in sorted(pdf_dir.rglob("*.pdf")):
        rel = p.relative_to(pdf_dir)
        if category_filter and rel.parts[0] != category_filter:
            continue
        all_pdfs.append((p, rel))

    logger.info(f"Discovered {len(all_pdfs)} benchmark PDFs in {pdf_dir}")

    if sample_limit and sample_limit < len(all_pdfs):
        logger.info(f"Sampling {sample_limit} PDFs out of {len(all_pdfs)}")
        # Sample evenly across categories
        import random
        random.seed(42)
        all_pdfs = random.sample(all_pdfs, sample_limit)

    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    tracemalloc.start()
    benchmark_start = time.perf_counter()
    results: List[Dict[str, Any]] = []

    cold_start_latencies: List[float] = []
    warm_latencies: List[float] = []

    logger.info(f"Starting extraction using {workers} workers (Verify={'ON' if enable_verify else 'OFF'})...")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(process_single_pdf, pdf, rel, output_dir, enable_verify): (pdf, rel)
            for pdf, rel in all_pdfs
        }
        
        completed_count = 0
        for future in as_completed(futures):
            res = future.result()
            results.append(res)
            completed_count += 1

            if completed_count <= min(5, len(all_pdfs)):
                cold_start_latencies.append(res["duration_ms"])
            else:
                warm_latencies.append(res["duration_ms"])

            if completed_count % 25 == 0 or completed_count == len(all_pdfs):
                logger.info(f"Progress: {completed_count}/{len(all_pdfs)} PDFs processed ({completed_count / len(all_pdfs) * 100:.1f}%)")

    total_wall_s = time.perf_counter() - benchmark_start
    current_mem, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Performance Metrics
    durations = [r["duration_ms"] for r in results if r["success"]]
    verify_times = [r["verify_ms"] for r in results if r["success"] and r["verify_ms"] > 0]
    total_pages = sum(r["pages"] for r in results if r["success"])
    total_blocks = sum(r["blocks"] for r in results if r["success"])
    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    import statistics

    mean_latency = statistics.mean(durations) if durations else 0.0
    median_latency = statistics.median(durations) if durations else 0.0
    p95_latency = (
        statistics.quantiles(durations, n=20)[18] if len(durations) >= 20 else max(durations or [0.0])
    )
    mean_verify_ms = statistics.mean(verify_times) if verify_times else 0.0

    category_stats: Dict[str, Dict[str, Any]] = {}
    for r in results:
        cat = r["category"]
        if cat not in category_stats:
            category_stats[cat] = {"total": 0, "success": 0, "failed": 0, "durations": []}
        category_stats[cat]["total"] += 1
        if r["success"]:
            category_stats[cat]["success"] += 1
            category_stats[cat]["durations"].append(r["duration_ms"])
        else:
            category_stats[cat]["failed"] += 1

    for cat, stats in category_stats.items():
        cat_d = stats["durations"]
        stats["mean_ms"] = round(statistics.mean(cat_d), 2) if cat_d else 0.0
        stats["median_ms"] = round(statistics.median(cat_d), 2) if cat_d else 0.0

    summary: Dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dataset": "allenai/olmOCR-bench",
        "scorer": "olmocr.bench.benchmark",
        "total_documents": len(all_pdfs),
        "successful_documents": len(successful),
        "failed_documents": len(failed),
        "success_rate_pct": round(len(successful) / len(all_pdfs) * 100, 2) if all_pdfs else 0.0,
        "total_pages": total_pages,
        "total_blocks": total_blocks,
        "total_runtime_s": round(total_wall_s, 2),
        "throughput_docs_per_sec": round(len(successful) / total_wall_s, 2) if total_wall_s > 0 else 0.0,
        "throughput_pages_per_sec": round(total_pages / total_wall_s, 2) if total_wall_s > 0 else 0.0,
        "latency_ms": {
            "mean": round(mean_latency, 2),
            "median": round(median_latency, 2),
            "p95": round(p95_latency, 2),
            "min": round(min(durations), 2) if durations else 0.0,
            "max": round(max(durations), 2) if durations else 0.0,
            "cold_start_mean": round(statistics.mean(cold_start_latencies), 2) if cold_start_latencies else 0.0,
            "warm_mean": round(statistics.mean(warm_latencies), 2) if warm_latencies else 0.0,
        },
        "verify_overhead": {
            "enabled": enable_verify,
            "mean_verify_ms": round(mean_verify_ms, 2),
            "verify_pct_of_total": round((mean_verify_ms / mean_latency * 100), 2) if mean_latency > 0 else 0.0,
        },
        "memory": {
            "peak_mb": round(peak_mem / (1024 * 1024), 2),
            "current_mb": round(current_mem / (1024 * 1024), 2),
        },
        "category_breakdown": category_stats,
        "failures": [
            {"pdf": r["pdf"], "category": r["category"], "error": r["error"]}
            for r in failed
        ],
    }

    # Save performance summary
    metrics_path = report_dir / "performance_metrics.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    logger.info(f"Saved performance metrics to {metrics_path}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="AXTRACT olmOCR-bench Adapter")
    parser.add_argument(
        "--bench-dir",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "olmOCR-bench" / "bench_data",
        help="Path to olmOCR-bench data directory containing pdfs/ and .jsonl files",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "olmOCR-bench" / "bench_data" / "axtract",
        help="Path to output markdown directory for candidate",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=REPO_ROOT / "reports" / "benchmarks" / "olmocr",
        help="Path to reports directory",
    )
    parser.add_argument(
        "--category",
        type=str,
        default=None,
        help="Filter by category (e.g. arxiv_math, tables, etc.)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Limit number of sample PDFs (for smoke testing)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=6,
        help="Number of worker threads",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Disable Verify engine during benchmark run",
    )

    args = parser.parse_args()
    summary = run_benchmark(
        bench_dir=args.bench_dir,
        output_dir=args.output_dir,
        report_dir=args.report_dir,
        category_filter=args.category,
        sample_limit=args.sample,
        workers=args.workers,
        enable_verify=not args.no_verify,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
