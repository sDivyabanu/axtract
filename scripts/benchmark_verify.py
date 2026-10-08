#!/usr/bin/env python3
"""Benchmark AXTRACT Verify. Writes reports/verify_benchmark.{json,md}.

Three separate things are measured and kept apart:

1. Overhead   validation time next to extraction time on the real documents in sample_files/.
2. Real documents   the status Verify gives each one, and any blocking issue it raises (false positives).
3. Fault injection   known corruptions applied to clean extractions of generated fixtures.

IMPORTANT: (3) measures whether Verify notices faults that were injected on purpose into a small set of
fixtures. It is NOT a measure of how accurate AXTRACT is on real documents, nor of how many real
extraction errors Verify would catch. Real silent-loss rate needs labelled real-world ground truth.

Usage (repo root, backend virtualenv):  python scripts/benchmark_verify.py
"""

from __future__ import annotations

import collections
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from tests.fault_campaign import run_campaign  # noqa: E402
from tests.verify_fixtures import extract  # noqa: E402
from verify.engine import run_verification  # noqa: E402

BLOCKING = ("medium", "high", "critical")


def real_documents() -> list[dict]:
    rows = []
    for f in sorted((ROOT / "sample_files").iterdir()):
        if f.suffix.lstrip(".") not in ("pdf", "docx", "pptx", "xlsx", "png", "jpg") or f.name.startswith(("12_", "13_")):
            continue
        t0 = time.perf_counter()
        resp = extract(f)
        extract_ms = (time.perf_counter() - t0) * 1000
        rep = run_verification(f, resp).report
        s = rep.summary
        rows.append(dict(
            file=f.name, status=rep.status.value, extract_ms=round(extract_ms), verify_ms=round(rep.timings_ms["total"]),
            units=s.units_total, verified=s.verified, not_verifiable=s.not_verifiable, review_required=s.review_required,
            evidence_coverage=s.evidence_coverage, agreement_rate=s.agreement_rate,
            blocking_issues=sorted({f"{i.layer.value}:{i.code}" for i in rep.issues if i.severity.value in BLOCKING}),
            stages={k: round(v) for k, v in rep.timings_ms.items() if k != "total"}))
    return rows


def fault_injection() -> dict:
    results, _ = run_campaign(Path(tempfile.mkdtemp()))
    by = collections.defaultdict(lambda: {"injected": 0, "detected": 0, "right_layer": 0})
    for r in results:
        d = by[r.fault.case]
        d["injected"] += 1
        d["detected"] += r.detected
        d["right_layer"] += r.detected and r.matched_expectation
    return dict(
        injected=len(results), detected=sum(r.detected for r in results), missed=sum(not r.detected for r in results),
        right_layer_and_code=sum(r.detected and r.matched_expectation for r in results), by_format=dict(by),
        faults=[dict(format=r.fault.case, fault=r.fault.name, detected=r.detected, codes=sorted(r.codes), status=r.status) for r in results])


def main() -> None:
    real = real_documents()
    faults = fault_injection()
    tot_e, tot_v = sum(r["extract_ms"] for r in real), sum(r["verify_ms"] for r in real)
    data = dict(real_documents=real, overhead=dict(extract_ms=tot_e, verify_ms=tot_v, percent=round(100 * tot_v / max(tot_e, 1), 1)), fault_injection=faults)
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    (out / "verify_benchmark.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    md = ["# AXTRACT Verify benchmark", "",
          "> Fault injection below measures whether Verify notices corruptions injected on purpose into generated fixtures.",
          "> It is **not** real-world accuracy and **not** a real silent-loss rate.", "",
          "## Real documents (`sample_files/`)", "",
          "| file | status | units | verified | not verifiable | review | extract ms | verify ms | blocking issues |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in real:
        md.append(f"| {r['file']} | {r['status']} | {r['units']} | {r['verified']} | {r['not_verifiable']} | {r['review_required']} | "
                  f"{r['extract_ms']} | {r['verify_ms']} | {', '.join(r['blocking_issues']) or '-'} |")
    md += ["", f"Total: extraction {tot_e} ms, validation {tot_v} ms ({data['overhead']['percent']}% overhead).", "",
           "## Fault injection (fixtures)", "", "| format | injected | detected | right layer + code | missed |", "|---|---|---|---|---|"]
    for k, v in faults["by_format"].items():
        md.append(f"| {k} | {v['injected']} | {v['detected']} | {v['right_layer']} | {v['injected'] - v['detected']} |")
    md.append(f"| **total** | {faults['injected']} | {faults['detected']} | {faults['right_layer_and_code']} | {faults['missed']} |")
    (out / "verify_benchmark.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
