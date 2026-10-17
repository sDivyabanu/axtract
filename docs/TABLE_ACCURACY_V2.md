# Table accuracy follow-up: reproduction and integration

## Scope and source

Continue the existing `codex/table-accuracy` branch; do not reset or switch another
checkout. The reference `ef499d5` includes all earlier table improvements through
`b1cc77b`. The final accuracy source is `aee1df1`. Source replay uses `git show` in
memory, without checking out an older revision or changing any worktree.

All measurements use native PyMuPDF extraction, table enhancement, layout and
Markdown only. They exclude PDF routing, OCR, vision, security sanitation, previews,
Verify, and generated official baseline checks. The unchanged official
`load_single_test(...).run(...)` implementation evaluates the original selected
cases. These results are not a valid full-pipeline/full-corpus olmOCR-Bench score.

The original selection remains unchanged. Additional development and held-out
selections are disjoint from it and each other. PDF paths are ranked by SHA256
with seed `AXTRACT-table-2026-10-17-v2`, then alternating ranks define splits,
without consulting outcomes. All official cases for selected PDFs are retained.
The held-out case text/source pages were not used to implement fixes; aggregate
validation results were checked after changes. This is repeated validation,
not a pristine final blind test. Baseline held-out table passes were zero, so
retention of passing table cases is primarily checked in the other samples.

## Confirmed defects and implementations

- The legacy numeric gate uses PDF line IDs that repeat across cell blocks;
  within-line gutter detection misses cells emitted as separate blocks. New
  `physical_rows`/`numeric_table_candidates` use native baseline geometry and
  recurring right-aligned numeric columns. They require nearby repeated labelled
  rows, open gutters and source-word conservation. Missing cells stay empty.
  Neighbouring prose and existing table regions are excluded. The original
  generic text fallback limits remain; these new evidence-backed paths bypass
  them only for supported structures.
- Shaded table cells can be mistaken for chart bars. `has_ruled_grid` joins thin
  source rule segments, including zero-area stroked lines, and requires both
  orientations to span the region. Plot strokes override a grid background;
  genuine line/bar charts are covered by negative tests.
- Some text tables have segmented horizontal cell borders with no vertical rules.
  `ruled_text_candidates` requires repeated border breakpoints to recover columns
  and native block overlap to keep wrapped cell lines in one logical row.
  Long unresolved native blocks, partial frames and overlapping accepted tables
  are rejected conservatively; original text remains available.
- Non-numeric ruled tables could be skipped before `find_tables` ran.
  `ruled_grid_regions` supplies positive source evidence independent of digits.
- Nonstandard `<colspan:n>`/`<rowspan:n>` suffixes alter cell values. The serializer
  now emits supported HTML spans for explicitly known extractor structure only.
  `None` inference is tagged separately and cannot invent spans or mask values.
  Conflicting/out-of-bounds span metadata falls back to preserved source cells.
- The restored citation notice sits detached in the extreme top margin, across
  a multi-column body. Classification uses position, separation, width, font and
  body-column evidence, with no citation-text matching. Narrow citations, nearby
  body text, bottom footnotes, large/bold headings and math-marked content are
  excluded. The source content remains in a reviewable structured HEADER block.
  This is still a conservative heuristic, not a universal furniture classifier.

No missing cell contents are synthesized. Ambiguous PDF merged cells, short or
single-numeric-column borderless grids, scanned tables and unresolved reading
order remain limitations. No OCR/model changes are included.

## Local reproduction (PowerShell, in this worktree)

Use existing environments; no model/package downloads or remote operations.

```powershell
$appPython = 'C:/Users/sdivy/wdev/Projects/axtract/backend/.venv/Scripts/python.exe'
$dataRoot = 'C:/Users/sdivy/wdev/Projects/axtract/benchmarks/olmOCR-bench/bench_data'
# Only create manifests when absent; select refuses to overwrite them.
if (-not (Test-Path backend/data/table_accuracy/selection.json)) {
    & $appPython scripts/table_accuracy_sample.py select --selection-config evaluation/table_accuracy_selection.json --data-root $dataRoot --manifest backend/data/table_accuracy/selection.json --output backend/data/table_accuracy
}
foreach ($splitName in @('development','heldout')) {
    if (-not (Test-Path "backend/data/table_accuracy_v2/$splitName.json")) {
        & $appPython scripts/table_accuracy_sample.py select --selection-config "evaluation/table_accuracy_$($splitName)_v2.json" --data-root $dataRoot --manifest "backend/data/table_accuracy_v2/$splitName.json" --output backend/data/table_accuracy_v2
    }
}
# Use fresh labels; existing candidate folders are never overwritten.
& ./scripts/run_table_accuracy_suite.ps1 -Label reproduction_before -Revision ef499d5
& ./scripts/run_table_accuracy_suite.ps1 -Label reproduction_after -Revision aee1df1
& $appPython scripts/compare_table_accuracy.py --before-label reproduction_before --after-label reproduction_after --output-prefix backend/data/table_accuracy_v2/reproduction_comparison
```

The suite runs original, development and held-out samples sequentially. It invokes
the existing bundled scorer Python plus `scorer312/Lib/site-packages`, avoiding the
old scorer venv launcher's stale interpreter path. Scorer repository:
`C:/Users/sdivy/wdev/Projects/axtract/axtract-olmocr-data/scorer`, commit
`f7cfe4c22098b154c76b6ec950d1c0a464eecf8d` (allenai/olmocr).
Inputs, scorer source and extraction module hashes are recorded and compared.
The source module replay also includes the new table geometry helper.

Commands used for the final paired measurements:

```powershell
& ./scripts/run_table_accuracy_suite.ps1 -Label verified_before -Revision ef499d5
& ./scripts/run_table_accuracy_suite.ps1 -Label verified_after -Revision aee1df1
& ./scripts/run_table_accuracy_suite.ps1 -Label semantic_verified -Revision cb80202
& $appPython scripts/compare_table_accuracy.py --output-prefix backend/data/table_accuracy_v2/final_comparison
```

Intermediate stage labels `ruled_grid`, `numeric_geometry`, `segmented_frames`,
`semantic_spans`, `detached_margins`, `ruled_gate` and `chart_safe_gate` were run
without `-Revision` against their then-current working snapshots. The exact
retained stage sources are associated with commits using recorded module hashes,
allowing checkout line-ending differences; `semantic_verified` and the final
paired runs use explicit Git revisions. `STAGE_AUDIT.md` identifies canonical
stage results; other snapshots remain preserved as experimental history.

## Artifacts (all local and uncommitted)

Under `backend/data/table_accuracy_v2/`:

- `FINAL_REPORT.md`: implementation findings, verification and handoff summary.
- `final_comparison.md`/`.json`: paired results, all gained/lost IDs, category and
  performance breakdowns.
- `final_comparison_failed_cases.jsonl`: every still-failing selected case.
- `STAGE_AUDIT.md`/`stage_audit.json`: per-change gains/losses and verified commits.
- `verified_before/<split>/official_case_results.json` and
  `verified_after/<split>/official_case_results.json`: all official case outcomes,
  explanations and source/input/scorer metadata.
- `verified_before/<split>/performance.json` and
  `verified_after/<split>/performance.json`: per-PDF timing, CPU, Python allocation
  peaks and Windows process lifetime peak working set (native allocations included).
- Each candidate's category directories: Markdown used by scoring.
- `development.json`/`heldout.json`: frozen cases, input hashes and configuration.
- `discovery_diagnostics.json`: development-only gate/rejection evidence.
- `test_results.txt`: final available-tests output.

Peak working-set counters are cumulative across each sample process, not per-file
exclusive allocations. Traced Python peaks exclude native memory. Timing includes
tracemalloc and writes. Concurrent laptop load, CPU frequency and cache state
prevent a reliable speedup claim from a single paired run. No models are loaded;
one extraction process/PDF at a time is used.

## Tests and missing fixture

```powershell
& $appPython -m pytest backend/tests/test_services.py backend/tests/test_table_ops.py backend/tests/test_pdf_block_quality.py backend/tests/test_extractors.py::TestPyMuPDFExtractor backend/tests/test_extractors.py::TestDocxExtractor backend/tests/test_extractors.py::TestPptxExtractor backend/tests/test_extractors.py::TestXlsxExtractor evaluation/test_table_discovery.py evaluation/test_native_margin_evidence.py evaluation/test_table_serialization.py evaluation/test_table_text_conservation.py evaluation/test_table_symbol_recovery.py -k 'not financial_highlights_text_and_footnotes and not chart_grid_becomes and not page_fourteen_chart' -q --disable-warnings --basetemp=backend/data/table_accuracy_v2/pytest-reproduction --tb=short
```

The three deselected tests still require the absent
`evaluation/fixtures/pymupdf_block_quality_pages.pdf`, missing in both checkouts.
They are blocked, not counted as passing. Restore the original fixture to run
its financial/chart assertions; the synthetic tests do not replace that evidence.

## Manual integration plan — approval required

No merge, cherry-pick, rebase, reset, remote write or other-checkout modification
has been performed. Keep this branch isolated pending approval.

At inspection, Claude's math HEAD was `a294150`, following `e594f5c`, and
`backend/extractors/text_math.py` still had uncommitted changes. These files were
read-only to this task. Refresh the math status before eventual integration.

Shared file `backend/extractors/pymupdf_extractor.py`, relative to common baseline
`7a2e205`:

| Common-base location | Math changes | Table changes / resolution |
|---|---|---|
| import after line 14 | imports `page_has_math_fonts`, `rebuild_page` | add table geometry imports alongside; preserve both |
| after line 363 | inserts `page_math` rawdict reconstruction (math lines 365–377) | inserts table recovery and filtered `lines_by_block` (table lines 378–409); preserve both stages |
| lines 379–380 | retains whole-block table skip, then uses `page_math.blocks` (math lines 395–406) | removes whole-block skipping to keep outside-table words (table around line 424); this is a semantic overlap |
| around lines 471–480 | sets `text_layer_math`, adjusts heading/formula logic | adds margin HEADER classification (table lines 521–527); preserve math metadata and guards |

The integrator must make text ownership explicit. Table-covered glyphs must not
be re-emitted by `page_math.blocks`, and partial overlap must not drop surviving
prose or math. Prefer filtering table-covered glyphs by geometry before math
rebuilding or carrying source coverage into its result, rather than skipping an
entire native block. This may require a coordinated math-helper adjustment after
approval; it is deliberately not implemented in this table branch.

Do not copy either version of the whole extractor over the other. The table-only
symbol helper, table serialization, span provenance and geometry module can be
reviewed independently. Re-run both branches' focused fixtures and a bounded
full-pipeline integration sample, including mixed math/table/prose and citation
headers, before claiming combined gains. Full-corpus scoring remains a separate
explicitly approved step.
