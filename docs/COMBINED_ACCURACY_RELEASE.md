# Combined accuracy branch: results, known limits, how to run

Branch `integration/combined-accuracy` combines the table sprint, the structural sprint (R2), the scanned-math/fidelity sprint and
the digital-math and multi-column work. Measured on the frozen 70-PDF / 430-assertion diagnostic with the unchanged official scorer.

| Run (commit `d6017b0`, clean tree) | Passed | Macro | Micro |
|---|---|---|---|
| Scanned math required (`AXTRACT_SCANNED_MATH=require`) | 227/430 | 58.42% | 52.79% |
| Scanned math off (default) | 188/430 | 53.82% | 43.72% |

Per category (required): arxiv_math 46/60, headers_footers 29/33, long_tiny_text 30/63, multi_column 28/39, old_scans 6/54,
old_scans_math 39/121, tables 49/60. All 70 documents completed, 0 hard timeouts. This is a 70-PDF diagnostic, not the full benchmark.

## Old-scan / scanned-math sprint (merged after the above)
Commit `8dfc1a8` (`feature/old-scan-accuracy-v2`, based on `e6003b6`), same frozen 70 PDFs / 430 assertions, unchanged official
scorer, `AXTRACT_SCANNED_MATH=require`, clean tree. **This is a frozen-70 diagnostic result and not the official full-benchmark score.**

| Result | Before (`e6003b6`) | After (`8dfc1a8`) |
|---|---|---|
| Passed | 227/430 | **236/430** |
| Macro / micro | 58.42% / 52.79% | **59.49% / 54.88%** |
| old_scans_math | 39/121 | **48/121** |
| old_scans | 6/54 | 6/54 |
| arxiv_math, headers_footers, long_tiny_text, multi_column, tables | 46/60, 29/33, 30/63, 28/39, 49/60 | unchanged |

10 assertions gained, 1 lost (all in old_scans_math). Aggregate document processing time 654 s versus 557 s (more equation lines are
now recognized); 70/70 completed, 0 hard timeouts, slowest document 45 s (was 41 s), peak working set about 1.0 GiB (unchanged).
Scanned-math results are time-capped and so depend slightly on machine load (the same code scored 50/121 in a 10-PDF development loop).

What changed: the scanned-math line finder ignores dark scan-edge streaks and long rules (they fused whole pages into one tall band,
so equation lines were never offered); candidate lines are decoded most-equation-like first and capital-only running heads are
skipped; a decoding cut off by the token or time limit keeps its finished array rows; the per-page recognition budget is 28 s
(still bounded by the 60 s pipeline deadline). No new dependency, model asset or network requirement. old_scans is handwriting;
no suitable offline recognizer was found, so it is unchanged. Still missing: equations inside prose lines, and accurate
recognition of dense multi-row arrays.

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

## Full 1,403-PDF official benchmark (not run here)
Set `$data` to the olmOCR-bench `bench_data` folder (it holds `pdfs/` and the `*.jsonl` files), from this repository's venv:

    $env:AXTRACT_SCANNED_MATH="require"; $env:AXTRACT_VERIFY="0"
    .venv\Scripts\python scripts\benchmark_axtract_olmocr.py --bench-dir $data --output-dir runs\full\candidate --report-dir runs\full\report --no-verify --workers 2

Then score with the unchanged official scorer under its own Python 3.12 environment (flags as in the scorer's `--help`):

    scorer-python -m olmocr.bench.benchmark --dir $data --candidate <path to runs\full\candidate>

Estimate: the 70-PDF run took about 12 minutes with scanned math on (654 s of document time), roughly 9 s per PDF, so about 3.5 hours
for extraction at that rate, plus scoring. No full-benchmark score has been measured for this branch.
