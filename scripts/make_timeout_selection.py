"""Derive the frozen 22-PDF timeout-recovery selection from the saved timeout-branch outputs.

Read-only: lists output file names under <bench_out> and maps each to its benchmark PDF; counts the
official assertions for those PDFs. Writes evaluation/timeout_recovery_selection.json (new file).
"""
import argparse
import glob
import json
import os
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--bench-out", required=True)
ap.add_argument("--data-root", required=True)
ap.add_argument("--write", default="evaluation/timeout_recovery_selection.json")
a = ap.parse_args()

pdfs = []
for f in sorted(glob.glob(os.path.join(a.bench_out, "*", "*", "*_repeat1.md"))):
    cat = Path(f).parent.name
    stem = Path(f).name[: -len("_pg1_repeat1.md")]
    pdfs.append(f"{cat}/{stem}.pdf")
missing = [p for p in pdfs if not (Path(a.data_root) / "pdfs" / p).exists()]
print(len(pdfs), "pdfs; missing:", missing)

assertions = 0
for cat in sorted({p.split("/")[0] for p in pdfs}):
    jl = Path(a.data_root) / f"{'table_tests' if cat == 'tables' else cat}.jsonl"
    for line in open(jl, encoding="utf-8"):
        if json.loads(line)["pdf"] in pdfs:
            assertions += 1
print("official assertions for these PDFs:", assertions)
if not missing:
    Path(a.write).write_text(json.dumps({"description": "Antigravity 22 historical timeout PDFs",
                                         "pdfs": pdfs}, indent=1), encoding="utf-8")
    print("wrote", a.write)
