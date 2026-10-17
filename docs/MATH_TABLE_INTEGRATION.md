# Provisional math/table integration

## Branch and immutable inputs

Integration workspace: `C:/Users/sdivy/.codex/worktrees/math-table-integration/axtract`.
Branch: `codex/math-table-integration`. HEAD/base:
`7a2e20586cb4950560972751fb9f7ea50eeb1188` (`axtract-benchmarks`).
This is an **uncommitted implementation**, with no merge commit. The benchmark
branch is 35 commits ahead of main `54827d5b88a1f81d685145009768932b38b0f609`.
No original branch, index, worktree, process or benchmark artifact was modified.
No remote operations were performed.

Committed source snapshots incorporated:

- Table: `9d52e30d6bfeb7fab6ddc4818529306a7a303fd4`, including every earlier
  table commit and the final engine at `aee1df1`.
- Math: `f3adcddfa45e4ef68c7b92168faa43bdc4d5e5d5`, including `e594f5c`
  and `a294150`. Its committed OCR runtime, prewarm, chart-raster and model
  compatibility changes are retained as part of that snapshot.

Claude is still working: the final read-only audit found uncommitted changes in
`pymupdf_extractor.py`, `services/region_router.py` and `services/vision/models.py`,
plus its local formula package and sample outputs. None were copied. Therefore
this is ready for review and further snapshot integration, **not final math**.
The existing table worktree is clean; the benchmark checkout retains its
original uncommitted runtime changes and artifacts.

## Manual semantic resolutions

`backend/extractors/pymupdf_extractor.py` combines both branches rather than
choosing a complete version of either shared file:

1. Detect and recover tables first, retaining source geometry, explicit span
   provenance and conservative chart rejection.
2. Apply table ownership to fully contained native **words**, retaining words
   that straddle a table and surrounding prose in the same original block.
3. Exclude glyphs owned by those words before `page_math` reconstruction;
   exclude accepted table/chart rules from fraction recognition. Block indices
   remain stable and rejected candidates keep their source text.
4. Retain committed math metadata/heading behavior and table margin-header
   classification, including its math exclusions.
5. Preserve source block indices for rebuilt math. Fraction/radical/operator
   tokens carry all consumed source blocks, preventing fallback from re-emitting
   denominator or limit glyphs. Transitively include prose rows sharing a
   rewritten block so moved prose is neither lost nor duplicated.
6. Emit `<` and `>` inside reconstructed math as `\lt` and `\gt`, preserving
   relations through the existing HTML sanitizer. No security bypass was added.

Table geometry, missing-cell behavior, semantic HTML spans, rectangular Markdown,
Wingdings provenance and native text conservation remain from the table branch.
No official scorer, expected answer, PDF or dataset was changed or generated.
New ownership tests use in-memory glyph structures, not benchmark fixtures.

## Verification

Final focused suite: **168 passed**, **3 blocked/deselected** because the existing
`evaluation/fixtures/pymupdf_block_quality_pages.pdf` is absent. The new tests first
reproduced two ownership defects and now pass. Existing service, Office extractor,
PyMuPDF, table, equation/LaTeX and sanitizer tests also pass. Exact output is saved
in `backend/data/integration/test_results.txt`.

The original, development and held-out native table selections cover 51 existing
PDFs and 274 original official assertions. Their final results are identical
case-for-case to the completed table implementation: tables **31/50, 30/72,
21/70**, controls **7/30, 19/30, 16/22**. No lost cases.

A separate fixed digital full-pipeline sample has 17 existing PDFs and 99 original
assertions, including a page with both a table and 11 reconstructed math blocks
(`tables/0ee4f7788272e811ae7717b7133770892fe6_pg8.pdf`).

| Full pipeline | Tables | Math | Headers/footers | Multi-column | All selected cases |
|---|---:|---:|---:|---:|---:|
| Benchmark base `7a2e205` | 18/50 | 0/34 | 3/5 | 4/10 | 25/99 |
| Committed math `f3adcdd` | 18/50 | 20/34 | 3/5 | 4/10 | 45/99 |
| Provisional combined | 31/50 | 20/34 | 3/5 | 4/10 | 58/99 |

The same 17-PDF native-stage comparison is 18/50 -> 31/50 tables, 20/34 -> 20/34
math, and 7/15 -> 7/15 controls. These stage-only results omit routing, OCR,
security sanitation, preview and Verify. The full-pipeline runs call `parse_path`
with routing/security/preview/Verify enabled and score the actual sanitized
response Markdown. All 17 pages are single-page official PDFs.

There are 33 new passing cases vs benchmark base and 13 vs committed math;
**zero lost passing cases**. Remaining failures: 19 table, 14 math, 2 margin and
6 reading-order assertions. Verify completed on all 17, reporting
`review_required`; this must not be represented as fully verified extraction.
No formula ONNX weights were added: the local model directory has only the
tracked tokenizer, so scanned math/model-assisted accuracy is not established.

An initial 19-PDF attempt hit the RAM guard on scan-routed `long_tiny_text/10a_pg1.pdf`:
13 succeeded, 1 was stopped, 5 were not attempted. Its outputs/logs remain in
`full_combined_initial`. The digital-only 17-PDF selection was then fixed for all
comparisons. The two scan-routed controls remain in the native-stage tests.
No full-corpus score or target-80% claim is made.

Earlier math reports encountered Windows CP1252 errors while printing KaTeX
hypothesis diagnostics. They are preserved; use **`official_case_results_utf8.json`**,
which has zero scorer exceptions. Invalid formula hypotheses still fail normally.
The scorer source and official assertions are unchanged.

## Reproduce (PowerShell in this integration worktree)

Use fresh output names: scripts deliberately refuse to overwrite outputs/reports.
Existing dependency environments and PDFs are reused; no downloads are required.

```powershell
$appPython = 'C:/Users/sdivy/wdev/Projects/axtract/backend/.venv/Scripts/python.exe'
$scorerPython = 'C:/Users/sdivy/wdev/Projects/axtract/axtract-olmocr-data/python/cpython-3.12.15-windows-x86_64-none/python.exe'
$dataRoot = 'C:/Users/sdivy/wdev/Projects/axtract/benchmarks/olmOCR-bench/bench_data'
$scorer = 'C:/Users/sdivy/wdev/Projects/axtract/axtract-olmocr-data/scorer'
$packages = 'C:/Users/sdivy/wdev/Projects/axtract/axtract-olmocr-data/scorer312/Lib/site-packages'
# Baseline, math-only, combined, respectively:
& $appPython scripts/integration_accuracy.py extract --mode full --revision 7a2e205 --data-root $dataRoot --output backend/data/integration/reproduce_base
& $appPython scripts/integration_accuracy.py extract --mode full --revision f3adcdd --data-root $dataRoot --output backend/data/integration/reproduce_math
& $appPython scripts/integration_accuracy.py extract --mode full --data-root $dataRoot --output backend/data/integration/reproduce_combined
foreach ($label in @('reproduce_base','reproduce_math','reproduce_combined')) {
    & $scorerPython -X utf8 scripts/integration_accuracy.py score --data-root $dataRoot --output "backend/data/integration/$label" --score-report official_case_results_utf8.json --scorer $scorer --scorer-packages $packages
}
# Use --mode native with the same commands for the matching native-stage sample.
& ./scripts/run_table_accuracy_suite.ps1 -Label reproduce_native_tables
```

The frozen manifests are copied unchanged from the existing table worktree to
`backend/data/table_accuracy/selection.json` and
`backend/data/table_accuracy_v2/{development,heldout}.json`. If absent on another
checkout, reproduce them using the existing instructions in `TABLE_ACCURACY_V2.md`.
Do not change the dataset JSONLs or manufacture a missing fixture.

Harness configuration: one child/PDF, 45-second parser budget, 75-second hard
limit, stop if available system RAM falls below 300 MiB, Verify enabled, OCR arena
off, OCR/OMP/BLAS thread budget 2. No other process is stopped. Formula/layout
availability follows local installed assets; no model fallback is fabricated.

Measured labels: `full_base`, `full_math`, `full_final`, `native_math`,
`native_final`; final native table label: `integrated_final`.
Earlier `full_combined` is before the relation-sanitization correction.
`timing_math`/`timing_final` isolate parser time after imports for a fair timing
comparison; earlier worker timing includes snapshot replay and imports.

## Reports and next integration

`backend/data/integration/REPORT.md` and `comparison.json` contain runtime,
category totals, every gained/lost case ID, remaining failures and changed files.
`source_manifest_final.json` identifies all uncommitted sources by SHA256.
Each run retains per-page Markdown, `performance.json`, per-document JSON with
blocks/Verify/stage timings and logs. `official_case_results_utf8.json` records
all assertions and scorer identity (`f7cfe4c22098b154c76b6ec950d1c0a464eecf8d`).
The three native table report directories are under
`backend/data/table_accuracy_v2/integrated_final/`.

Before using newer math work: recheck that Claude has finished and inspect a new
committed snapshot only. Port its changes manually into this integration branch,
retaining table recovery, word/glyph exclusions, structural-token ownership,
prose-row closure, relation escaping and source metadata. Pay particular attention
to `pymupdf_extractor.py`, `text_math.py`, `services/region_router.py` and
`services/vision/models.py`. Repeat the focused tests and frozen comparisons.
Do not copy active files, overwrite page reconstruction, or integrate into the
benchmark/main checkout without approval. No commit has been created.
