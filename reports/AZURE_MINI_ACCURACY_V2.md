# Azure gpt-5.4-mini accuracy pipeline: final measurement (2026-10-19)

All paid extraction used `gpt-5.4-mini` only. Scores come from the official olmOCR-bench test classes (pinned scorer, unchanged rules) on the fixed 70-PDF Set A (frozen manifest) and Set B. This is a diagnostic sample, not the 1,403-PDF corpus. Both sets were run once with the final code; development decisions were made on a separate held-out dev sample (54 bench PDFs that are in neither set) and on offline re-scoring.

## Results

| Category | Set A baseline | Set A final | Set B baseline | Set B final |
|---|---:|---:|---:|---:|
| arxiv_math | 88.3 (53/60) | 80.0 (48/60) | 66.1 (37/56) | 75.0 (42/56) |
| headers_footers | 45.5 (15/33) | 75.8 (25/33) | 37.9 (11/29) | 82.8 (24/29) |
| long_tiny_text | 88.9 (56/63) | 88.9 (56/63) | 91.9 (57/62) | 93.5 (58/62) |
| multi_column | 79.5 (31/39) | 76.9 (30/39) | 52.6 (20/38) | 71.1 (27/38) |
| old_scans | 16.7 (9/54) | 20.4 (11/54) | 32.1 (17/53) | 37.7 (20/53) |
| old_scans_math | 84.3 (102/121) | 83.5 (101/121) | 78.0 (99/127) | 80.3 (102/127) |
| tables | 60.0 (36/60) | 85.0 (51/60) | 73.1 (38/52) | 75.0 (39/52) |
| **Macro** | **66.16** | **72.92** | **61.67** | **73.63** |
| Micro | 70.23 | 74.88 | 66.91 | 74.82 |
| API cost (70 pages) | $0.871 | $0.862 | $0.808 | $0.909 |

All 70 pages produced output in both final runs (wall time ~10.5 min each, 3 workers). Baselines: Set A `runs/azure_mini_normalized70_20261019` (Codex), Set B `runs/azure_mini_baseline_setB_recovered_20261019` (Codex's configuration, run on Set B in this session). Finals: `runs/final_setA_recovered`, `runs/final_setB_recovered`.

**The 80% target was not reached.** Gains: +6.76 macro (A) and +11.96 (B).

Regressions (Set A): arxiv_math lost 6 and gained 1 (2503.09432, 04189, 04245, 05334: valid-looking LaTeX that differs from the expected expression; run-to-run variance on these pages was observed in every run). multi_column −1, old_scans_math −1 net.

## What changed (all in the Azure path; LightOnOCR and local extraction untouched)

- **Prompt** (`AXTRACT_AZURE_PROMPT=accuracy`, default; `legacy` restores the old behaviour): the legacy prompt plus three targeted additions: tag running headers/footers/letterheads/page numbers as `<page-header>`/`<page-footer>`; tables inside figures are tables; HTML only for merged-cell tables. Broader prompt rewrites were tried and caused regressions in math (Unicode instead of LaTeX), tables (cells written as paragraphs) and line breaks, so they were dropped.
- **Page furniture as blocks**: tagged items become HEADER/FOOTER blocks (kept in JSON, out of the body Markdown). A tag is overruled when the page's visible text layer places the passage in the body region. A lone folio, or a short first/last line that the text layer puts in the outer 6% of the page, is also treated as furniture.
- **Tables**: model HTML tables are parsed (existing `html_table_to_grid`) into TABLE blocks rendered by the standard writer; previously the output sanitiser escaped them into `&lt;table>` text. Tab-separated grids become Markdown tables. Inline `<sup>/<sub>/<br>` are converted to text instead of being escaped.
- **Scanned pages**: two-page spreads (and two documents photographed together) are detected from a blank central gutter in the ink profile and transcribed as two images. A scan-only note asks for a best reading without `[illegible]`/`?` placeholders. Reasoning effort `low` is used for scanned pages only.
- **Validation and selective recovery** (one targeted reread, only when triggered): text-layer coverage below 90% (the reread is told which text-layer lines are missing); an unclosed `$` in a math paragraph; a table written one value per paragraph or a looping line; `[illegible]` placeholders. A reread is rejected if it adds problems, loses text-layer coverage, or shrinks the text by more than 10%. Only 2–5 pages per 70 trigger a reread.
- **Text-layer spelling**: near-miss prose words are corrected to the exact word of the visible text layer (same-length aligned substitutions only; math, tables, HTML and URLs untouched).
- **Robustness**: a content-filtered answer is retried once and, if filtered again, its partial text is kept and marked for review instead of an empty page.
- `scripts/azure_benchmark.py`: `--set B` runs a second fixed sample from any data root.

## Measured and rejected (held-out dev scans, 87 assertions)

| Variant | Passed | Cost |
|---|---:|---:|
| Default (reasoning none) | 46–50 | $0.20 |
| Scan reasoning `low` (adopted) | 49 | $0.22 |
| Contrast enhancement | 43 | $0.19 |
| 1.6× upscale | 48 | $0.22 |
| Self-proofread pass | 45 | $0.42 |

The rejected options remain available but are off by default (`AXTRACT_AZURE_SCAN_ENHANCE`, `AXTRACT_AZURE_SCAN_UPSCALE`, `AXTRACT_AZURE_SCAN_PROOFREAD`).

## Remaining bottlenecks

1. **Handwriting (old_scans 20–38%)**: misreads with zero allowed edits. Splitting spreads recovered missing pages, but mini's reading of cursive is the limit; no prompt, resolution or proofread variant helped on held-out scans.
2. **Math exactness and variance (arxiv_math)**: the same page passes in one run and fails in another; the expected LaTeX is matched only when the model reproduces the exact structure.
3. **Multi-column order on complex layouts** and small spelling differences in dense text.

## Validation

- 72 Azure/VLM tests pass (`test_azure_ocr*.py`, `test_vlm_engine.py`), including 10 new tests in `tests/test_azure_ocr_postprocess.py`.
- Full backend suite: 1397 passed. The 65 failures and 54 errors are identical with the baseline code restored (pre-existing; encryption, RAG, verify and progress-stream tests).
- LightOnOCR/shared files are byte-identical to the baseline, except one condition in `services/layout_service.py` that also lets cloud pages keep their order (VLM behaviour unchanged).
- Nothing committed.
