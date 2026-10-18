# Combined accuracy branch: results, known limits, how to run

Branch `integration/combined-accuracy` combines the table sprint, the structural sprint (R2), the scanned-math/fidelity sprint and
the digital-math and multi-column work. Measured on the frozen 70-PDF / 430-assertion diagnostic with the unchanged official scorer.

| Run (commit `d6017b0`, clean tree) | Passed | Macro | Micro |
|---|---|---|---|
| Scanned math required (`AXTRACT_SCANNED_MATH=require`) | 227/430 | 58.42% | 52.79% |
| Scanned math off (default) | 188/430 | 53.82% | 43.72% |

Per category (required): arxiv_math 46/60, headers_footers 29/33, long_tiny_text 30/63, multi_column 28/39, old_scans 6/54,
old_scans_math 39/121, tables 49/60. All 70 documents completed, 0 hard timeouts. This is a 70-PDF diagnostic, not the full benchmark.

## "DIFFERS from the frozen reference"
`scripts/frozen70_benchmark.py report` compares each category with the band recorded in `evaluation/frozen70/manifest.json`.
That band is the result of the earlier sprint that the manifest froze, so any real accuracy change prints `<-- outside reference`.
It is a change detector and not an error: the sample, PDF hashes, assertions and scorer are identical, and the totals above are
the new measurements.

## Known limits
- 119 tests fail in the full suite (65 failures, 54 errors). They fail identically before and after this branch (same set,
  compared by test id) and come from the environment (missing optional services and model downloads), not from extraction.
- Pix2Text-MFR ONNX weights: SHA-256 manifest in `scripts/scanned_math_assets.manifest.json`; the model id is `breezedeus/pix2text-mfr`
  (MIT). The exact URL of the ONNX export used is not recorded, so the hashes are the identity of the files. Weights are not committed.
- The scorer environment was not rebuilt from scratch for this release; `docs/FROZEN70_REPRODUCTION.md` lists its requirements.
- Remaining math failures are mostly author macros (`\norm`, `\overset`, `\bm`, `\underline`), tall-delimiter pieces and long
  multi-line displays. Remaining multi-column failures are mostly OCR-quality text and ligature/spacing differences.

## Convert a folder of PDFs to Markdown
    backend\.venv\Scripts\python.exe scripts\axtract_batch.py .\input-pdfs .\markdown-output

## Full 1,403-PDF official benchmark
Not run by this branch. See `docs/FROZEN70_REPRODUCTION.md` for the scorer setup; the adapter is
`scripts/benchmark_axtract_olmocr.py`. At the measured 70-PDF rate (about 9 s per PDF, scanned math on) expect roughly 3.5 hours
for extraction plus scoring time.
