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
`scripts/full_benchmark.py` runs the whole benchmark and scores it with the unchanged official scorer. Inputs you provision once
(nothing is downloaded): the olmOCR-bench `bench_data` folder (`pdfs/` and the `*.jsonl` files; the six assertion files must match the
dataset revision recorded in `evaluation/frozen70/manifest.json`), the allenai/olmocr scorer checkout at the recorded commit, and a
Python 3.12 scorer interpreter with Playwright (`docs/FROZEN70_REPRODUCTION.md`).

    $data = "C:\path\to\olmOCR-bench\bench_data"
    # 1. extraction: one isolated process per PDF (75 s hard limit, 60 s pipeline deadline, 2 GiB cap, free-RAM guard); resumable
    .venv\Scripts\python scripts\full_benchmark.py extract --data-root $data --candidate-name axtract_v2 --scanned-math require
    #    interrupted? continue with the same command plus --resume (finished documents are kept)
    # 2. official scoring (olmocr.bench.benchmark --force --skip_baseline)
    .venv\Scripts\python scripts\full_benchmark.py score --data-root $data --candidate-name axtract_v2 `
        --scorer-python <scorer python 3.12> --scorer <allenai/olmocr checkout> --scorer-packages <scorer site-packages>

Output goes to `$data\axtract_v2\<category>\<pdf>_pg1_repeat1.md` (where the official scorer looks) and the run record to
`runs\full_axtract_v2\` (`documents.jsonl`, `run.json`, `official_score.txt`, `failed_tests.jsonl`).

Two properties of the official scorer that the runner handles without editing the scorer:
- If any assertion page has no Markdown file the scorer prints "FAILED (errors)" and 0% for the whole candidate, even with `--force`.
  The runner records a page that produced no text (or whose document hit the hard limit) as an empty file, lists those pages in
  `run.json`, and refuses to score an incomplete candidate unless `--fill-missing` is given. Those assertions then fail honestly.
- On Windows the scorer matches `category\name.pdf` against `category/name.pdf` and finds nothing. `scripts/official_scorer_shim.py`
  makes `os.path.relpath` return forward slashes and then runs the scorer's own `main()`; no scorer file, test or threshold changes.

Checked on a 4-PDF candidate (the other pages filled with empty files): the official scorer evaluates it and prints its per-file
summary. Estimate: the 70-PDF run took about 12 minutes with scanned math on (654 s of document time), roughly 9 s per PDF, so about
3.5 hours for extraction, plus scoring (several minutes for 7,019 assertions). No full-benchmark score has been measured for this branch.
