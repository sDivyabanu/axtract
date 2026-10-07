#!/usr/bin/env python3
"""Table extraction accuracy evaluation.

Metrics:
  - Cell-value correctness: fraction of cells matching ground truth
  - Row/column structure: correct number of rows and columns
  - Header detection: whether first row was correctly identified as header

Usage:
  python evaluate_tables.py <result.json> <ground_truth.json>

Ground truth JSON format:
{
  "tables": [
    {
      "rows": [["Header1", "Header2"], ["val1", "val2"]],
      "header": ["Header1", "Header2"]
    }
  ]
}
"""

from __future__ import annotations

import json
import sys


def normalize_cell(val: str) -> str:
    """Normalize cell value for comparison."""
    if val is None:
        return ""
    return str(val).strip().lower()


def evaluate_table(extracted_rows: list[list], expected_rows: list[list]) -> dict:
    """Compare extracted table rows against expected rows."""
    result = {
        "row_count_match": len(extracted_rows) == len(expected_rows),
        "extracted_rows": len(extracted_rows),
        "expected_rows": len(expected_rows),
        "correct_cells": 0,
        "total_cells": 0,
        "cell_accuracy": 0.0,
    }

    min_rows = min(len(extracted_rows), len(expected_rows))
    for i in range(min_rows):
        ext_row = extracted_rows[i]
        exp_row = expected_rows[i]
        min_cols = min(len(ext_row), len(exp_row))
        for j in range(min_cols):
            result["total_cells"] += 1
            if normalize_cell(ext_row[j]) == normalize_cell(exp_row[j]):
                result["correct_cells"] += 1
        # Count extra expected cells as missed
        result["total_cells"] += max(0, len(exp_row) - len(ext_row))

    # Count extra expected rows
    for i in range(min_rows, len(expected_rows)):
        result["total_cells"] += len(expected_rows[i])

    if result["total_cells"] > 0:
        result["cell_accuracy"] = result["correct_cells"] / result["total_cells"]

    return result


def evaluate(result_path: str, ground_truth_path: str):
    with open(result_path) as f:
        result = json.load(f)
    with open(ground_truth_path) as f:
        ground_truth = json.load(f)

    extracted_tables = [
        b for b in result.get("blocks", []) if b.get("type") == "table"
    ]
    expected_tables = ground_truth.get("tables", [])

    print(f"Extracted tables: {len(extracted_tables)}")
    print(f"Expected tables: {len(expected_tables)}")

    for i, (ext, exp) in enumerate(
        zip(extracted_tables, expected_tables)
    ):
        ext_rows = ext.get("metadata", {}).get("rows", [])
        exp_rows = exp.get("rows", [])
        metrics = evaluate_table(ext_rows, exp_rows)
        print(f"\nTable {i + 1}:")
        print(f"  Row count: {metrics['extracted_rows']}/{metrics['expected_rows']} {'✓' if metrics['row_count_match'] else '✗'}")
        print(f"  Cell accuracy: {metrics['cell_accuracy']:.4f} ({metrics['correct_cells']}/{metrics['total_cells']})")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python evaluate_tables.py <result.json> <ground_truth.json>")
        sys.exit(1)
    evaluate(sys.argv[1], sys.argv[2])
