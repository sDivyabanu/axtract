"""Parse the text summary printed by the unmodified official scorer (`olmocr.bench.benchmark`)."""

from __future__ import annotations

import re

_OVERALL = re.compile(r"Average Score:\s*([\d.]+)%\s*(?:±\s*([\d.]+)%)?")
_TYPE = re.compile(r"^\s*(\w+)\s*:\s*([\d.]+)%\s*average pass rate over\s*(\d+)\s*tests")
_JSONL = re.compile(r"^\s*([^\s:]+)\s*:\s*([\d.]+)%\s*\((\d+)/(\d+)\s*tests\)")


def parse_scorer_output(stdout: str) -> dict:
    """Return overall score, CI, per-test-type and per-jsonl results from the scorer's final summary."""
    out: dict = {"overall_score": None, "confidence_interval": None, "test_types": {}, "jsonl_results": {}, "errors": []}
    in_jsonl = False
    for line in stdout.splitlines():
        if "Results by JSONL file" in line:
            in_jsonl = True
            continue
        m = _OVERALL.search(line)
        if m and "average of per-JSONL" in line:  # the final-summary line is authoritative
            out["overall_score"] = float(m.group(1))
            out["confidence_interval"] = f"± {m.group(2)}%" if m.group(2) else None
            continue
        if in_jsonl:
            m = _JSONL.match(line)
            if m:
                out["jsonl_results"][m.group(1)] = {
                    "pass_rate_pct": float(m.group(2)), "passed": int(m.group(3)), "total": int(m.group(4)),
                }
            continue
        m = _TYPE.match(line)
        if m:
            out["test_types"][m.group(1)] = {"pass_rate_pct": float(m.group(2)), "test_count": int(m.group(3))}
        elif "[ERROR]" in line:
            out["errors"].append(line.strip())
    return out
