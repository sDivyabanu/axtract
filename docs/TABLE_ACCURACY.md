# Focused table accuracy work

This documents the first series through `b1cc77b`. Follow-up changes and current
reproduction/integration instructions are in [TABLE_ACCURACY_V2.md](TABLE_ACCURACY_V2.md).

## Scope

Branch `codex/table-accuracy` starts at `7a2e205` from `axtract-benchmarks`.
The sample deliberately exercises the native PyMuPDF table stage, table enhancement,
layout ordering and Markdown rendering. It excludes PDF routing, OCR, vision,
preview generation, sanitization and Verify. It is not the complete application
pipeline or a valid full-corpus olmOCR-Bench score, and does not establish an 80% score.

The unchanged official `olmocr.bench.tests.load_single_test(...).run(...)` evaluates
all original cases for the selected PDFs. No expected strings are copied into
production code. `evaluation/table_accuracy_selection.json` contains only PDF
paths, hashes and selection policy, not dataset answers. Selection is frozen from
historical evidence before candidate measurements: three named failure PDFs,
lexicographic first three passing and three failing table PDFs, and two PDFs per
non-math control category. This is a development sample, not a held-out estimate.

## Implemented changes

1. Normalize cell whitespace to a single line; pad ragged rows without moving
   values; preserve empty metadata-backed tables. Literal pipes use escaped HTML
   tables, supported by Markdown and the official scorer.
2. Preserve text from rejected non-chart grids. For accepted table regions, remove
   only contained words instead of dropping an overlapping native text block.
   Chart-specific routing remains unchanged. This corrects proven text loss,
   but exposes a citation-header control failure: header text previously discarded
   accidentally now survives. No new header suppression rule was added.
   The follow-up also removes the non-chart placeholder figure so layout
   deduplication cannot discard the restored prose in its favor.
3. Decode Wingdings U+F0E0 as a Unicode right arrow only when the original table
   cell geometry and source font verify every occurrence. Other fonts and missing
   geometry remain untouched. Recovery provenance is stored in table metadata.
   The source crop for the selected path-table fixture confirms visible right arrows.

Missing values are not guessed, and broad table-discovery thresholds are unchanged.
Merged-cell inference and its existing span notation are unchanged.

## Reproduce (PowerShell, from the table worktree)

Use the existing local environments; no package downloads or model loading are
required. The old scorer venv launcher has a stale interpreter path, so use its
bundled Python with the existing site-packages directory explicitly.

```powershell
$appPython = 'C:/Users/sdivy/wdev/Projects/axtract/backend/.venv/Scripts/python.exe'
$scorerPython = 'C:/Users/sdivy/wdev/Projects/axtract/axtract-olmocr-data/python/cpython-3.12.15-windows-x86_64-none/python.exe'
$dataRoot = 'C:/Users/sdivy/wdev/Projects/axtract/benchmarks/olmOCR-bench/bench_data'
$scorerRoot = 'C:/Users/sdivy/wdev/Projects/axtract/axtract-olmocr-data/scorer'
$scorerPackages = 'C:/Users/sdivy/wdev/Projects/axtract/axtract-olmocr-data/scorer312/Lib/site-packages'
$runRoot = 'backend/data/table_accuracy_reproduction'

& $appPython scripts/table_accuracy_sample.py select --data-root $dataRoot --manifest "$runRoot/selection.json" --output $runRoot
foreach ($revision in @('7a2e205','ba6c9c8','e797925','263e0c6','b1cc77b')) {
    & $appPython scripts/table_accuracy_sample.py extract --source-commit $revision --data-root $dataRoot --manifest "$runRoot/selection.json" --output "$runRoot/$revision"
    if ($LASTEXITCODE -ne 0) { throw 'Extraction failed' }
    & $scorerPython scripts/table_accuracy_sample.py score --data-root $dataRoot --manifest "$runRoot/selection.json" --output "$runRoot/$revision" --scorer $scorerRoot --scorer-site-packages $scorerPackages
    if ($LASTEXITCODE -ne 0) { throw 'Scoring failed' }
}
```

`--source-commit` loads the four stage modules from `git show` in memory. It does
not switch, reset, cherry-pick or otherwise modify any checkout. The output records
the exact source commit, module hashes, Python and package versions. Output folders
and score files must be new; earlier reports are not overwritten. Dataset hashes
are checked before extraction and evaluation. All work is sequential.

Original measurements are under `backend/data/table_accuracy/verified_<revision>/`:

- `official_case_results.json`: unchanged official case results, failures,
  explanations, scorer hashes and candidate metadata.
- `performance.json`: source hashes, errors, per-document duration/pages and peak
  traced Python allocation. Native PyMuPDF allocations and total process RSS are
  not measured; timing includes tracing overhead and filesystem/cache effects.
- Category folders: candidate Markdown, separate from historical benchmark outputs.

The final source commit `b1cc77b` is measured in `verified_final/`.
`selection.json`, `final_comparison.json`, `FINAL_REPORT.md`, and `source_path_cells.png` are
local supporting artifacts (alongside preserved earlier reports) under `backend/data/table_accuracy/`. None are committed.
The official scorer checkout is `f7cfe4c22098b154c76b6ec950d1c0a464eecf8d` from
https://github.com/allenai/olmocr; its source files are unchanged.

## Tests

```powershell
& $appPython -m pytest backend/tests/test_services.py backend/tests/test_table_ops.py backend/tests/test_pdf_block_quality.py evaluation/test_table_serialization.py evaluation/test_table_text_conservation.py evaluation/test_table_symbol_recovery.py -k 'not financial_highlights_text_and_footnotes and not chart_grid_becomes and not page_fourteen_chart' -q --disable-warnings --basetemp=backend/data/table_accuracy_reproduction/pytest --tb=short
```

Three existing PDF-quality tests cannot execute: their fixture
`evaluation/fixtures/pymupdf_block_quality_pages.pdf` is absent from both the main
checkout and this worktree. An initial run reports these as three failures; the
command above explicitly deselects them. Restore the original fixture to run those
checks; do not fabricate it or treat them as passing.

## Integration handoff (approval required)

No integration or remote operation has been performed. Preserve Claude's math
changes and review table commits in order. `markdown_service.py` changes only the
empty-table guard, imports and table serialization. The shared
`pymupdf_extractor.py` has table detection masking, native text filtering, the
`_table_block` call/signature and a table-only symbol helper changed.

Claude's current math diff inserts `page_math` reconstruction directly before
`font_sizes` and a math branch before `lines_by_block` consumption. These overlap
with the text-conservation hunk. An integrator must preserve both implementations
and ensure the rebuilt math branch does not reintroduce table-covered text or
suppress surviving prose. Do not blindly replace either file. Re-run this sample,
math fixtures and a bounded full-pipeline integration sample after approval.

The citation-header regression must remain visible during integration review;
these commits are not presented as regression-free or ready for an automatic merge.
