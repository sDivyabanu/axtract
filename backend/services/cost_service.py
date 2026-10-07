"""Cost meter and benchmark service.

Implements:
1. Per-document cost and speed meter
2. Processing cost tracking
3. Performance benchmarking
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from models.document import DocumentResponse

logger = logging.getLogger(__name__)


@dataclass
class ProcessingMetrics:
    """Metrics for document processing."""
    filename: str
    file_size_bytes: int
    processing_time_ms: int
    page_count: int
    block_count: int
    cost_estimate_usd: float
    throughput_pages_per_second: float


def estimate_processing_cost(
    processing_time_ms: int,
    page_count: int,
    uses_ocr: bool = False,
    uses_llm: bool = False,
) -> float:
    """Estimate processing cost in USD.
    
    Cost factors:
    - Compute time (at $0.0001 per second)
    - OCR (if used): $0.01 per page
    - LLM calls (if used): $0.01 per call
    
    Returns estimated cost in USD.
    """
    # Base compute cost
    compute_cost = (processing_time_ms / 1000) * 0.0001
    
    # OCR cost
    ocr_cost = page_count * 0.01 if uses_ocr else 0
    
    # LLM cost (approximate)
    llm_cost = 0.01 if uses_llm else 0
    
    total_cost = compute_cost + ocr_cost + llm_cost
    return round(total_cost, 6)


def calculate_throughput(
    processing_time_ms: int, page_count: int
) -> float:
    """Calculate processing throughput in pages per second."""
    if processing_time_ms == 0:
        return 0.0
    return round((page_count / (processing_time_ms / 1000)), 2)


def generate_cost_summary(
    processing_time_ms: int,
    page_count: int,
    block_count: int,
    file_size_bytes: int = 0,
    uses_ocr: bool = False,
    uses_llm: bool = False,
) -> dict[str, Any]:
    """Generate cost and performance summary for a document.
    
    Returns:
    - processing_time_ms: int
    - cost_estimate_usd: float
    - throughput_pages_per_second: float
    - cost_per_page: float
    - pages_processed: int
    - blocks_extracted: int
    """
    cost_estimate = estimate_processing_cost(
        processing_time_ms,
        page_count,
        uses_ocr,
        uses_llm,
    )
    
    throughput = calculate_throughput(
        processing_time_ms,
        page_count,
    )
    
    cost_per_page = cost_estimate / page_count if page_count > 0 else 0
    
    return {
        "processing_time_ms": processing_time_ms,
        "cost_estimate_usd": cost_estimate,
        "throughput_pages_per_second": throughput,
        "cost_per_page": round(cost_per_page, 6),
        "pages_processed": page_count,
        "blocks_extracted": block_count,
        "file_size_bytes": file_size_bytes,
        "file_size_mb": round(file_size_bytes / (1024 * 1024), 2),
    }


def log_processing_metrics(metrics: ProcessingMetrics) -> None:
    """Log processing metrics for monitoring."""
    logger.info(
        f"Processed {metrics.filename}: "
        f"{metrics.page_count} pages in {metrics.processing_time_ms}ms "
        f"({metrics.throughput_pages_per_second:.2f} pages/sec), "
        f"cost: ${metrics.cost_estimate_usd:.6f}"
    )


def benchmark_processing(
    test_files: list[Path], parse_func
) -> dict[str, Any]:
    """Run benchmark on a set of test files.
    
    Args:
        test_files: List of file paths to benchmark
        parse_func: Function to parse a file (should return DocumentResponse)
    
    Returns benchmark results.
    """
    results = []
    total_time = 0
    total_pages = 0
    total_cost = 0
    
    for file_path in test_files:
        if not file_path.exists():
            logger.warning(f"Test file not found: {file_path}")
            continue
        
        try:
            file_size = file_path.stat().st_size
            start_time = time.time()
            
            # Parse the file
            response = parse_func(file_path)
            
            elapsed_ms = int((time.time() - start_time) * 1000)
            
            cost = estimate_processing_cost(
                elapsed_ms,
                response.page_count,
                uses_ocr=False,  # Could detect this from response
                uses_llm=False,
            )
            
            throughput = calculate_throughput(elapsed_ms, response.page_count)
            
            result = {
                "filename": file_path.name,
                "file_size_bytes": file_size,
                "processing_time_ms": elapsed_ms,
                "page_count": response.page_count,
                "block_count": len(response.blocks),
                "cost_estimate_usd": cost,
                "throughput_pages_per_second": throughput,
            }
            results.append(result)
            
            total_time += elapsed_ms
            total_pages += response.page_count
            total_cost += cost
            
        except Exception as e:
            logger.error(f"Benchmark failed for {file_path.name}: {e}")
            results.append({
                "filename": file_path.name,
                "error": str(e),
            })
    
    # Calculate aggregates
    avg_throughput = calculate_throughput(total_time, total_pages) if total_pages > 0 else 0
    
    return {
        "test_files_count": len(test_files),
        "successful_runs": len([r for r in results if "error" not in r]),
        "failed_runs": len([r for r in results if "error" in r]),
        "total_processing_time_ms": total_time,
        "total_pages_processed": total_pages,
        "total_cost_usd": round(total_cost, 6),
        "average_throughput_pages_per_second": avg_throughput,
        "average_cost_per_page": round(total_cost / total_pages, 6) if total_pages > 0 else 0,
        "individual_results": results,
    }


def create_stress_test_report(
    benchmark_results: dict[str, Any]
) -> str:
    """Create a human-readable stress test report."""
    report_lines = [
        "=== STRESS TEST REPORT ===",
        f"Test Files: {benchmark_results['test_files_count']}",
        f"Successful: {benchmark_results['successful_runs']}",
        f"Failed: {benchmark_results['failed_runs']}",
        "",
        "AGGREGATE METRICS:",
        f"Total Processing Time: {benchmark_results['total_processing_time_ms']}ms",
        f"Total Pages Processed: {benchmark_results['total_pages_processed']}",
        f"Total Cost: ${benchmark_results['total_cost_usd']:.6f}",
        f"Average Throughput: {benchmark_results['average_throughput_pages_per_second']:.2f} pages/sec",
        f"Average Cost per Page: ${benchmark_results['average_cost_per_page']:.6f}",
        "",
        "INDIVIDUAL RESULTS:",
    ]
    
    for result in benchmark_results["individual_results"]:
        if "error" in result:
            report_lines.append(f"  {result['filename']}: FAILED - {result['error']}")
        else:
            report_lines.append(
                f"  {result['filename']}: "
                f"{result['page_count']} pages, "
                f"{result['processing_time_ms']}ms, "
                f"${result['cost_estimate_usd']:.6f}, "
                f"{result['throughput_pages_per_second']:.2f} pages/sec"
            )
    
    return "\n".join(report_lines)
