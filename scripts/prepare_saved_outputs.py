"""Lay out saved benchmark Markdown (e.g. the timeout branch's bench_out) so integration_accuracy.py can score it.

Read-only on the source folder. Creates <output>/<category>/<stem>_pg1_repeat1.md copies and a
performance.json carrying the expected PDF hashes and official case-file hashes that `score` verifies.
usage: prepare_saved_outputs.py --bench-out DIR --selection JSON --data-root DIR --output DIR
"""
import argparse
import glob
import hashlib
import json
import shutil
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--bench-out", type=Path, required=True)
ap.add_argument("--selection", type=Path, required=True)
ap.add_argument("--data-root", type=Path, required=True)
ap.add_argument("--output", type=Path, required=True)
a = ap.parse_args()


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


pdfs = json.loads(a.selection.read_text(encoding="utf-8"))["pdfs"]
a.output.mkdir(parents=True, exist_ok=False)
for f in sorted(glob.glob(str(a.bench_out / "*" / "*" / "*_repeat1.md"))):
    cat = Path(f).parent.name
    dest = a.output / cat / Path(f).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(f, dest)
expected = {p: digest(a.data_root / "pdfs" / p) for p in pdfs}
sources = {p.name: digest(p) for p in a.data_root.glob("*.jsonl")}
(a.output / "performance.json").write_text(json.dumps(
    {"mode": "saved-outputs", "expected_pdfs": expected, "case_source_hashes": sources, "documents": []}, indent=1),
    encoding="utf-8")
print("prepared", len(pdfs), "pdfs ->", a.output)
