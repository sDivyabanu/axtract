"""Benchmark script for stress testing the document extraction pipeline.

Run this script to test performance on a set of documents:
    python benchmark.py /path/to/test/files/

Output:
- Performance metrics (throughput, cost)
- Comparison with baseline extractors
- Stress test report
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from services.cost_service import benchmark_processing, create_stress_test_report
from services.parse_service import parse_upload


class MockUploadFile:
    """Mock UploadFile for benchmarking."""
    
    def __init__(self, file_path: Path):
        self.filename = file_path.name
        self.file = open(file_path, "rb")
        self.content_type = "application/octet-stream"


def parse_file_for_benchmark(file_path: Path):
    """Parse a file for benchmarking."""
    mock_upload = MockUploadFile(file_path)
    try:
        return parse_upload(mock_upload)
    finally:
        mock_upload.file.close()


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark document extraction pipeline"
    )
    parser.add_argument(
        "test_dir",
        type=Path,
        help="Directory containing test files to benchmark",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output file for benchmark report (default: stdout)",
    )
    
    args = parser.parse_args()
    
    if not args.test_dir.exists():
        print(f"Error: Test directory not found: {args.test_dir}")
        sys.exit(1)
    
    # Find all supported files
    supported_extensions = {".pdf", ".docx", ".pptx", ".xlsx", ".jpg", ".jpeg", ".png"}
    test_files = [
        f for f in args.test_dir.iterdir()
        if f.is_file() and f.suffix.lower() in supported_extensions
    ]
    
    if not test_files:
        print(f"No supported files found in {args.test_dir}")
        print(f"Supported extensions: {', '.join(supported_extensions)}")
        sys.exit(1)
    
    print(f"Found {len(test_files)} test files")
    print("Running benchmark...")
    
    # Run benchmark
    results = benchmark_processing(test_files, parse_file_for_benchmark)
    
    # Generate report
    report = create_stress_test_report(results)
    
    # Output report
    if args.output:
        args.output.write_text(report)
        print(f"Benchmark report saved to: {args.output}")
    else:
        print("\n" + report)


if __name__ == "__main__":
    main()
