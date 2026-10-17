# Table follow-up and scanned-math integration — 2026-10-17

## Status and scope

Implementation is local and **uncommitted**, on `codex/math-table-integration`, HEAD/base `7a2e20586cb4950560972751fb9f7ea50eeb1188`. No merge into any source branch, commits, staging, remote operations or model downloads. The integrated table snapshot is `9d52e30d6bfeb7fab6ddc4818529306a7a303fd4`; committed math snapshot is `f3adcddfa45e4ef68c7b92168faa43bdc4d5e5d5`. The explicitly authorized six uncommitted math source files were read-only snapshotted with SHA256, then selectively ported. All six original source hashes remain unchanged. No whole-file replacement of the integrated PyMuPDF extractor occurred.

This supersedes the measurements and committed-snapshot-only restriction in the historical `docs/MATH_TABLE_INTEGRATION.md`: the user later explicitly authorized porting Claude's unfinished uncommitted MFR sources. Existing reports and historical attempts remain intact.

All accuracy below uses **unchanged official assertions**, scorer `f7cfe4c22098b154c76b6ec950d1c0a464eecf8d`. These are frozen samples, **not full-corpus olmOCR-Bench accuracy**, and do not establish the 80% target. No new benchmark PDFs, expected answers or datasets were generated.

## Confirmed table/layout defects and fixes

- Physical table rows were collapsed into multiline cells. Split only repeated source-aligned rows with an alphabetic label column and at least two numeric columns, matching original word/cell content exactly; reject ambiguous wrapping and explicit merged geometry. Three newly passing table assertions.
- Padded headers exceeded the text-height gap gate. Permit the additional gap only with a thin source rule covering the numeric grid; one new pass.
- Adjacent prose defeated borderless discovery and individual number tokens split cells such as number/percentage pairs. Build conservative local grids from native cell lines, retaining wrapped headers and exact word coverage. Seven new passes.
- The first local-grid attempt shadowed three larger development/held-out tables. Broader complete-grid preference fixed all three losses; intermediate attempts remain saved.
- PyMuPDF word block indices enumerate text blocks, while dict/rawdict include images. Align indices before assigning source boxes/fonts. Preserve original word coordinates and math reconstruction indices.
- Line bottom coordinates were incorrectly derived from word tops; corrected geometry and ordinary line joining. Dehyphenate only when the same unbroken word already exists on the source page; exclude math-font content and retain repair provenance.
- Author/address grids obscured the real column gutter. Prefer dominant long body paragraphs for column inference.
- Detached footer/folio detection now uses source font, position and horizontal body-gap evidence with math/footnote guards. Footer content stays structured and reviewable.

Table glyph ownership, text around partial overlaps, semantic spans, source boxes, chart rejection, sanitizer behavior and `text_math.py` reconstruction remain preserved. Eight table failures remain on one raster page with 460 invisible, corrupted native OCR spans (`tables/008d1dbe3c5a5fd2bac6e4f29acc45e711aa_pg8_pg1.pdf`); source geometry cannot recover its actual cell characters. No missing values were invented.

## Scanned math implementation and audit

- Added lazy Pix2Text-MFR ONNX sessions and tokenizer loading, atomically published and reused. CPU-only, two threads by default (cap four), spinning/CPU arena disabled. Existing shared inference lock retained. Greedy EOS decoding has repetition/token/time checks; truncated output is never emitted as a complete equation.
- Added opt-in scanned/mixed-OCR routing with 200-DPI horizontal ink line bands. Full-page images with invisible OCR and no visible native text are also eligible; native table ownership remains protected. This corrected a real hidden-OCR page previously routed as digital.
- Default per page: 20-second recognition budget, 24 attempts, at most five seconds per prediction, and six seconds parser reserve. RAM guard before loading/each prediction; missing weights, prediction exceptions, invalid/incomplete output and budget exhaustion preserve OCR fallback. These are cooperative bounds: one ONNX call/session initialization cannot be preempted in-process. Benchmark workers additionally have a 75-second external limit.
- Emit existing equation schema, source OCR IDs/text, crop geometry and model timing, low confidence and required independent review. Only fully covered non-prose OCR fragments are suppressed from Markdown; their exact original structured content remains. Partial paragraphs remain visible. Existing equation/table/chart overlap guards prevent duplicate ownership.
- Ported bounded legacy pix2tex decoding and router reread caps. Legacy pix2tex weights were not enabled in the measured integration environment; those lazy changes have unit coverage, not real-model performance validation here.
- Expanded the LaTeX whitelist for legitimate standard symbols/delimiters while preserving unknown-command, brace/environment, argument and MathML checks. Unsafe input/write/definition/HTML commands still reject. Corrected a concrete MathML false-valid case: token-spaced `hspace` dimensions accepted by the converter but rejected by KaTeX.
- **Rejected unsafe normalization as production behavior:** the historical cleanup removes bold/vector distinctions and text spaces. Production preserves bold/roman/script notation, delimiters and text/control spaces; only display-size hints are removed, and angle relations are escaped for the existing sanitizer. Styling commands alone cannot classify prose as mathematics. Validation establishes syntax, never mathematical correctness; model outputs require review.

Only the corrected math-character constant was ported into the shared PyMuPDF extractor during this phase. Integrated `page_math`, glyph ownership, source metadata and table repairs were not replaced.

## Actual measurements

### Fixed 17-PDF combined full pipeline (99 assertions)

| Category | Starting integration | Final (MFR enabled) |
|---|---:|---:|
| Tables | 31/50 (62%) | **42/50 (84%)** |
| Digital mathematics | 20/34 | **20/34** |
| Headers/footers | 3/5 | **5/5** |
| Multi-column | 4/10 | **6/10** |
| Total selected assertions | 58/99 (58.59%) | **73/99 (73.74%)** |

All 17 succeeded; zero extraction errors/timeouts and scorer exceptions. Fifteen gained assertion IDs, zero lost. Final MFR port retains the table-phase final result case-for-case. `table_followup_01/FINAL_COMPARISON.json` lists the fifteen exact IDs. The table improvements generalize cautiously: development and held-out table totals are unchanged, not improved.

### Original frozen 32-PDF selection — real AXTRACT benchmark adapter

The harness calls the existing `process_single_pdf` adapter, real `parse_path`, Verify-enabled configuration and actual adapter Markdown export. This differs from a native-stage or crop-only experiment. All 32 outputs succeeded, with zero extraction errors/timeouts and zero scorer exceptions.

| Category | User's earlier report | Measured pre-MFR integration | Final enabled integration |
|---|---:|---:|---:|
| arxiv_math | 43/83 | 43/83 | **43/83** |
| old_scans_math | 0/84 | 0/84 | **20/84 (23.81%)** |
| multi_column | 12/29 | 15/29 | **15/29** |
| headers_footers | 4/5 | 4/5 | **4/5** |
| long_tiny_text | 3/5 | 3/5 | **3/5** |
| tables | 5/5 | 5/5 | **5/5** |
| Total selected assertions | 67/211 | 70/211 | **90/211** |

The difference in pre-MFR multi-column results reflects the preceding table/layout fixes. Final MFR adds twenty passes and loses **none**, verified by exact IDs across all 211 assertions. An earlier enabled run was 14/84; hidden-OCR eligibility and prose/validation corrections produced the final 20/84. All attempts are preserved.

Final scanned-page scores: `3_pg270` 0/4; `1_pg113` 1/14; `2_pg39` 6/17; `1_pg19` 10/14; `5_pg174` 0/24; `1_pg131` 3/11. The 64 remaining failures include wrong exponents/variables and formula structure, merged/split line bands, incomplete decoding, conservative prose/ownership exclusions and page-budget stops. `5_pg174` has a broad OCR table over a polynomial-division layout; only one candidate reached recognition. Do not discard table ownership merely to improve this math sample. `3_pg270` emits syntactically valid but incorrect formulas, demonstrating the recognition limit.

### Native table selections (OCR/router/security/Verify excluded)

| Selection | Before table follow-up | Final tables | Final multi-column |
|---|---:|---:|---:|
| Original | 31/50 | **42/50** | 6/10 |
| Development | 30/72 | **30/72** | 4/8 |
| Held-out | 21/70 | **21/70** | 5/7 |

51 existing PDFs, 274 assertions overall. Final MFR changes produce identical outcomes to the table-phase final native results on every assertion. These native results are not full-pipeline scores.

### Separate regenerated crop experiment

Claude's reported 22/84 could not be reproduced exactly because its original crop manifest was unavailable. Existing six PDFs were segmented again with recorded coordinates and bounded inference: raw **21/84**, historical lossy cleanup **26/84**, safe cleanup **20/84**, all zero scorer exceptions. Total shared recognition time 101.10 seconds. These are diagnostic hypotheses without adapter routing, sanitation or Verify; the raw variant includes incomplete predictions. The higher lossy score does not justify erasing mathematical distinctions. Production uses the safe path only. See `EXPERIMENT_COMPARISON.json` and `line_experiment/*` for inputs, model hashes, crops, predictions and official assertion outcomes.

## Runtime, memory and default decision

| Actual adapter measurement | Pre-MFR | Final enabled |
|---|---:|---:|
| 32-PDF summed parser/adapter time | 49.08 s | **154.24 s** |
| Median per-page parser/adapter time | 0.755 s | **0.768 s** |
| Largest worker OS lifetime peak working set | 783.6 MiB | **784.8 MiB** |

Each PDF uses a separate worker, so ONNX sessions are cold per PDF; a persistent server reuses sessions. Times exclude imports/source replay, include real adapter work, and are not end-to-end benchmark wall time. Warm caches and concurrent system load affect medians. The final 17-PDF run took 15.38 seconds total parser time, median 0.636 seconds, peak 560.3 MiB. Pre-MFR table-final was 12.84 seconds, median 0.530 seconds, peak 560.7 MiB; this small timing difference is not controlled enough to attribute to code alone.

**Keep `AXTRACT_SCANNED_MATH=0` by default.** Scanned recognition adds meaningful accuracy but raises total selected-adapter time over threefold. One ordinary scanned header/footer control consumed 20 seconds and accepted no equations. Peak memory above is a per-worker lifetime maximum, not system-wide RAM or an incremental model estimate. Measured MFR scan workers were approximately 389–500 MiB. Do not infer the whole suite's near-equal maximum means MFR is free.

An earlier table/layout attempt recorded a 689-second elapsed hard-timeout during a delayed tool response; the cause is not established. Its results remain preserved. Final 17/32 runs completed cleanly. No full corpus was run.

## Tests and remaining work

Final expanded focused suite: **210 passed, 3 deselected/blocked**, 3.99 seconds; saved as `test_results_final.txt`. The earlier 199-pass log is preserved separately. Tests cover table serialization/discovery/source conservation, shared glyph ownership, math validation, scan fallback/budgets/deduplication, semantic cleanup, services, extractors, table operations and sanitizer behavior.

The three pre-existing tests require absent `evaluation/fixtures/pymupdf_block_quality_pages.pdf`: `test_financial_highlights_text_and_footnotes_are_separated`, `test_chart_grid_becomes_reviewable_figure_not_giant_table`, `test_page_fourteen_chart_is_not_table_and_markers_are_detached`. No replacement fixture or altered expectation was created.

Ranked next engineering work:

1. **High impact / medium effort:** recover text from image-backed native table candidates using bounded region OCR with cell geometry and source comparisons; addresses the eight remaining fixed-sample table failures. Validate on existing held-out scans before enabling.
2. **High impact / medium-high effort:** distinguish true tables from aligned polynomial division, and improve formula line/column segmentation plus OCR evidence localized to crop. Preserve table ownership until disambiguated; requires mixed scan controls.
3. **Medium-high impact / medium effort:** prioritize likely formula bands before decoding, cache encoder/session work and profile greedy decoder cost; prevent ordinary scans spending a full page budget. Keep semantic validation and review requirements. Incorrect recognition still needs source-grounded evaluation; whitelist expansion alone cannot solve it.

## Changed implementation files in these follow-ups

Table/layout: `backend/extractors/table_geometry.py`, `backend/extractors/pymupdf_extractor.py`, `backend/services/layout_service.py`, `evaluation/test_table_geometry_followup.py`.

MFR: `backend/services/vision/{pix2text_mfr,line_math,latex,models}.py`, `backend/services/scanned_math.py`, `backend/services/region_router.py`, `backend/services/markdown_service.py`, shared PyMuPDF math-character constant, `evaluation/test_scanned_math.py`.

Reproducibility: `scripts/integration_accuracy.py`, `scripts/experiment_scanned_math.py`, this documentation. All earlier integration files remain. `source_manifest_final.json` lists every dirty source/support file with SHA256 and identifies changes versus the preserved starting integration snapshot. The final `line_math.py` docstrings were corrected after benchmarking to label historical claims and lossy cleanup accurately; executable behavior is unchanged and the final expanded tests pass.

## Reports and safe handoff

All paths below are relative to the integration repository root:

- `backend/data/integration/table_followup_01/REPORT.md` and `FINAL_COMPARISON.json`: table investigation, exact fifteen gains, no losses, native results; historical candidates and source-page inspections retained alongside them.
- `backend/data/integration/mfr_takeover_01/REPORT.md`: this final report.
- `backend/data/integration/mfr_takeover_01/FINAL_COMPARISON.json`: exact twenty scanned-math gains, all failures, native and combined comparison, timing and read-only Claude source verification.
- `backend/data/integration/mfr_takeover_01/EXPERIMENT_COMPARISON.json`: separate crop-diagnostic results and provenance.
- `backend/data/integration/mfr_takeover_01/adapter_{before,enabled,final}/`: per-document structured output, actual Markdown, logs, performance and `official_case_results_utf8.json`. `adapter_final` is final; earlier runs are retained.
- `backend/data/integration/mfr_takeover_01/combined17_final/`: final combined full pipeline with the same report names; `combined17_enabled` remains historical.
- `backend/data/table_accuracy_v2/mfr_verified_final/{original,development,heldout}/`: final native Markdown, performance and `official_case_results.json`.
- `backend/data/integration/mfr_takeover_01/line_experiment/{raw,legacy_cleanup,safe_cleanup}/`: crop-only Markdown, exact official diagnostics, performance/model hashes/crop predictions.
- `backend/data/integration/mfr_takeover_01/claude_snapshot/` and `pre_mfr_source/`: read-only-source and pre-port immutable snapshots.
- `backend/data/integration/mfr_takeover_01/test_results_final.txt`: final expanded test output; `source_manifest_final.json`: implementation identity.

Nothing is integrated into `axtract-benchmarks` or main. Future integration needs manual handling of `pymupdf_extractor.py`, `region_router.py`, `models.py`, `latex.py` and Markdown suppression. Retain table-first word/glyph ownership, source alignment, all page-math reconstruction, source OCR blocks, strict sanitation and opt-in MFR. Run the frozen comparisons again after incorporating any newer Claude work. No commit is authorized or created.


## Local setup and exact reproduction commands (PowerShell)

Run from `C:/Users/sdivy/.codex/worktrees/math-table-integration/axtract`. Existing assets are reused read-only. Every output label must be fresh; the scripts reject overwriting reports. The commands below reproduce the measured invocations with fresh `reproduce_` output labels. Saved measured labels were `adapter_before`, `adapter_final`, `combined17_final`, native `mfr_verified_final` and `line_experiment`.

```powershell
$appPython = 'C:/Users/sdivy/wdev/Projects/axtract/backend/.venv/Scripts/python.exe'
$scorerPython = 'C:/Users/sdivy/wdev/Projects/axtract/axtract-olmocr-data/python/cpython-3.12.15-windows-x86_64-none/python.exe'
$dataRoot = 'C:/Users/sdivy/wdev/Projects/axtract/benchmarks/olmOCR-bench/bench_data'
$scorer = 'C:/Users/sdivy/wdev/Projects/axtract/axtract-olmocr-data/scorer'
$packages = 'C:/Users/sdivy/wdev/Projects/axtract/axtract-olmocr-data/scorer312/Lib/site-packages'
$selection = 'C:/Users/sdivy/wdev/Projects/axtract-math-wt/bench_math_sample/selection.json'
$reports = 'backend/data/integration/mfr_takeover_01'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:AXTRACT_ORT_THREADS = '2'
$env:OMP_NUM_THREADS = '2'
$env:OPENBLAS_NUM_THREADS = '2'
$env:AXTRACT_MFR_DIR = 'C:/Users/sdivy/wdev/Projects/axtract-math-wt/backend/model_weights/pix2text_mfr'
$env:AXTRACT_FORMULA_SITE = 'C:/Users/sdivy/wdev/Projects/axtract-math-wt/backend/model_weights/latex_ocr/site'
$env:AXTRACT_MFR_PAGE_SECONDS = '20'
$env:AXTRACT_MFR_MAX_LINES = '24'

# Immutable measured pre-MFR source, same existing 32-PDF selection.
$env:AXTRACT_SCANNED_MATH = '0'
& $appPython -B -X utf8 scripts/integration_accuracy.py extract --mode adapter --selection $selection --source-directory "$reports/pre_mfr_source" --data-root $dataRoot --output "$reports/reproduce_before"

# Opt in only for the enabled measurement; normal production default is off.
$env:AXTRACT_SCANNED_MATH = '1'
& $appPython -B -X utf8 scripts/integration_accuracy.py extract --mode adapter --selection $selection --data-root $dataRoot --output "$reports/reproduce_final"
& $appPython -B -X utf8 scripts/integration_accuracy.py extract --mode full --data-root $dataRoot --output "$reports/reproduce_combined17"
foreach ($label in @('reproduce_before','reproduce_final','reproduce_combined17')) {
    & $scorerPython -B -X utf8 scripts/integration_accuracy.py score --data-root $dataRoot --output "$reports/$label" --score-report official_case_results_utf8.json --scorer $scorer --scorer-packages $packages
    if ($LASTEXITCODE -ne 0) { throw "Scoring failed: $label" }
}

# Stage-only controls: same existing original/development/held-out manifests.
& ./scripts/run_table_accuracy_suite.ps1 -Label reproduce_mfr_native

# Diagnostic only, never a substitute for the real adapter measurement.
& $appPython -B -X utf8 scripts/experiment_scanned_math.py --data-root $dataRoot --baseline "$reports/adapter_before" --output "$reports/reproduce_line_experiment"
foreach ($variant in @('raw','legacy_cleanup','safe_cleanup')) {
    & $scorerPython -B -X utf8 scripts/integration_accuracy.py score --data-root $dataRoot --output "$reports/reproduce_line_experiment/$variant" --score-report official_case_results_utf8.json --scorer $scorer --scorer-packages $packages
    if ($LASTEXITCODE -ne 0) { throw "Scoring failed: $variant" }
}

# Exact final focused suite (existing missing-fixture cases explicitly excluded).
& $appPython -B -m pytest evaluation backend/tests/test_pdf_block_quality.py backend/tests/test_vision_units.py backend/tests/test_services.py backend/tests/test_extractors.py backend/tests/test_table_ops.py backend/tests/test_verify_sanitiser.py -q -p no:cacheprovider -k 'not test_financial_highlights_text_and_footnotes_are_separated and not test_chart_grid_becomes_reviewable_figure_not_giant_table and not test_page_fourteen_chart_is_not_table_and_markers_are_detached'

# Return ordinary local use to default-off recognition.
$env:AXTRACT_SCANNED_MATH = '0'
```

Adapter harness records environment, PDF/assertion/source hashes, parser timings and per-worker peak working set. Existing parser budgets are 55 seconds for adapter mode, 45 seconds for full mode, plus 75-second worker hard timeout and 300-MiB available-RAM guard. The page budgets are cooperative, not a guarantee that one ONNX kernel can be interrupted. Do not run these experiments concurrently on a memory-constrained laptop.

Opt-in local server use requires the same three recognition settings (`AXTRACT_SCANNED_MATH`, `AXTRACT_MFR_DIR`, `AXTRACT_FORMULA_SITE`) in its process environment. No dependency install, new weights, change to Claude's environment, or frontend/auth change is required. With the flag absent/off, the new recognizer does not query/load models or alter extraction. Missing assets with the flag on preserve OCR.
