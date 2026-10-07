#!/usr/bin/env python3
"""Text extraction accuracy evaluation.

Metrics:
  - Normalized text match (Levenshtein-based similarity)
  - Content coverage (what fraction of expected text appears)
  - Block type accuracy (heading/paragraph/table classification)

Usage:
  python evaluate_text.py <result.json> <ground_truth.txt>

Ground truth format: plain text file with expected content.
Result format: JSON output from /api/parse endpoint.

Without ground truth, reports extraction statistics only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def normalize_text(text: str) -> str:
    """Normalize text for comparison: lowercase, collapse whitespace, strip."""
    import re
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def levenshtein_similarity(a: str, b: str) -> float:
    """Compute normalized Levenshtein similarity (0.0–1.0)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0

    # For very long strings, use a simpler metric
    if len(a) > 10000 or len(b) > 10000:
        return content_coverage(a, b)

    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            temp = dp[j]
            if a[i - 1] == b[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(dp[j], dp[j - 1], prev)
            prev = temp

    distance = dp[n]
    return 1.0 - distance / max(m, n)


def content_coverage(extracted: str, expected: str) -> float:
    """What fraction of expected words appear in extracted text."""
    expected_words = set(expected.split())
    if not expected_words:
        return 1.0
    extracted_words = set(extracted.split())
    found = expected_words & extracted_words
    return len(found) / len(expected_words)


def word_error_rate(extracted: str, expected: str) -> float:
    """Word Error Rate (WER): edit distance at word level / reference length."""
    ref_words = expected.split()
    hyp_words = extracted.split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0

    m, n = len(ref_words), len(hyp_words)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            temp = dp[j]
            if ref_words[i - 1] == hyp_words[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(dp[j], dp[j - 1], prev)
            prev = temp

    return dp[n] / m


def character_error_rate(extracted: str, expected: str) -> float:
    """Character Error Rate (CER): edit distance at char level / reference length."""
    if not expected:
        return 0.0 if not extracted else 1.0
    return 1.0 - levenshtein_similarity(extracted, expected)


def evaluate(result_path: str, ground_truth_path: str | None = None):
    """Run evaluation on a parse result."""
    with open(result_path) as f:
        result = json.load(f)

    blocks = result.get("blocks", [])
    markdown = result.get("markdown", "")

    # Basic statistics
    print(f"Document: {result.get('filename', 'unknown')}")
    print(f"Status: {result.get('status')}")
    print(f"Pages: {result.get('page_count')}")
    print(f"Blocks: {len(blocks)}")
    print(f"Processing time: {result.get('processing_time_ms')} ms")

    type_counts: dict[str, int] = {}
    extractors: set[str] = set()
    review_count = 0
    confidence_values: list[float] = []

    for block in blocks:
        t = block.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
        extractors.add(block.get("extractor", "?"))
        if block.get("requires_review"):
            review_count += 1
        if block.get("confidence") is not None:
            confidence_values.append(block["confidence"])

    print(f"\nBlock types: {type_counts}")
    print(f"Extractors used: {extractors}")
    print(f"Blocks requiring review: {review_count}")
    if confidence_values:
        avg_conf = sum(confidence_values) / len(confidence_values)
        min_conf = min(confidence_values)
        print(f"Confidence: avg={avg_conf:.4f} min={min_conf:.4f} n={len(confidence_values)}")
    else:
        print("Confidence: no blocks with confidence values")

    errors = result.get("errors", [])
    if errors:
        print(f"\nErrors/warnings: {len(errors)}")
        for e in errors:
            print(f"  [{e.get('code')}] {e.get('message')}")

    # Compare with ground truth if provided
    if ground_truth_path:
        with open(ground_truth_path) as f:
            expected = normalize_text(f.read())

        extracted = normalize_text(
            " ".join(b.get("content", "") for b in blocks)
        )

        sim = levenshtein_similarity(extracted, expected)
        cov = content_coverage(extracted, expected)
        wer = word_error_rate(extracted, expected)
        cer = character_error_rate(extracted, expected)

        print(f"\n--- Accuracy Metrics ---")
        print(f"Text similarity: {sim:.4f}")
        print(f"Content coverage: {cov:.4f}")
        print(f"Word Error Rate (WER): {wer:.4f}")
        print(f"Character Error Rate (CER): {cer:.4f}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python evaluate_text.py <result.json> [ground_truth.txt]")
        sys.exit(1)

    gt = sys.argv[2] if len(sys.argv) > 2 else None
    evaluate(sys.argv[1], gt)
