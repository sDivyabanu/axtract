"""Run the *unmodified* official olmOCR scorer and capture per-case outcomes.

Executed by the scorer's own Python (`python -I -X utf8 score_wrapper.py ...`), never imported by AXTRACT.
It calls `olmocr.bench.benchmark.main()` with the official arguments. The only thing replaced is the HTML
report generator: instead of an (often huge) HTML file, it writes one compact JSON line per test case.
When `--html` is given the original generator also runs. Scoring rules and aggregation are untouched.

Usage: score_wrapper.py --dir D --candidate NAME --outcomes OUT.jsonl [--html REPORT.html] [--skip-baseline]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys


def clip(value, limit=600):
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--outcomes", required=True)
    ap.add_argument("--html", default="")
    ap.add_argument("--skip-baseline", action="store_true")
    ap.add_argument("--max-reports", type=int, default=None)
    args = ap.parse_args()

    import olmocr.bench.benchmark as official

    original = official.generate_html_report

    def capture(test_results_by_candidate, pdf_folder, output_file, *a, **kw):
        test_to_jsonl = kw.get("test_to_jsonl") or {}
        with open(args.outcomes, "w", encoding="utf-8") as out:
            for cand, by_pdf in test_results_by_candidate.items():
                if cand != args.candidate:
                    continue
                for pdf, by_page in by_pdf.items():
                    for page, results in by_page.items():
                        for test, passed, explanation in results:
                            fields = {k: clip(v) for k, v in dataclasses.asdict(test).items()
                                      if k not in ("pdf", "page", "id", "type") and v is not None}
                            out.write(json.dumps({
                                "id": test.id, "pdf": pdf, "page": page, "type": test.type,
                                "source": test_to_jsonl.get(test.id), "passed": bool(passed),
                                "explanation": clip(explanation), "test": fields,
                            }) + "\n")
        if args.html:
            return original(test_results_by_candidate, pdf_folder, args.html, *a, **kw)
        return None

    official.generate_html_report = capture
    argv = ["olmocr.bench.benchmark", "--dir", args.dir, "--candidate", args.candidate, "--force",
            "--test_report", args.outcomes + ".unused.html"]
    if args.skip_baseline:
        argv.append("--skip_baseline")
    if args.max_reports:
        argv += ["--max_reports", str(args.max_reports)]
    sys.argv = argv
    official.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
