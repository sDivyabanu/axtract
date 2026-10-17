# Accuracy + timeout-recovery integration (local, 2026-10-17)

Branch `integrate/accuracy-timeouts`, worktree `C:\Users\sdivy\wdev\Projects\axtract-accuracy-integration`,
created from the local `axtract-benchmarks` HEAD `7a2e20586cb4950560972751fb9f7ea50eeb1188`.
Nothing was pushed, fetched or merged; no source worktree was modified (hash-verified, see "Safety").
All accuracy numbers are frozen samples scored with the **unchanged official olmOCR scorer**
(`axtract-olmocr-data/scorer`); they are not full-corpus accuracy.

## Commits (local; author `sDivyabanu <sdivyabanu09@gmail.com>`, no AI/co-author trailers)

| Commit | Content |
|---|---|
| `9919869` | Baseline checkout's *uncommitted* perf files copied verbatim: `ocr_extractor.py`, `ocr_runtime.py`, `main.py`, `prewarm.py`, `chart_raster.py` |
| `63ca14f` | Antigravity timeout recovery (cherry-pick of `a7651a5`, applies cleanly to the base) |
| `e76b50c` | Codex combined work ported from its *uncommitted* worktree: tables/layout, digital math, opt-in scanned math |
| `3f0c5c5` | Integration fixes + `evaluation/test_integration_interactions.py` + tooling |
| `9f379da` | Scanned-math rendering: measured renderer under the OCR pixel caps; ID-comparison tool |
| `cf956a4` | Final verification runner |
| (this doc) | Report |

`git diff --shortstat 7a2e205 cf956a4`: 51 files changed, 4980 insertions, 137 deletions.

## Change matrix

| File(s) | Origin | Notes |
|---|---|---|
| `extractors/ocr_extractor.py`, `ocr_runtime.py`, `main.py`, `services/prewarm.py`, `vision/chart_raster.py` | Baseline (uncommitted) | Codex carried identical copies; committed once, separately |
| `services/pdf_router.py`, `utils/request_cache.py`, `scripts/benchmark_axtract_olmocr.py` | Antigravity only | adaptive DPI, 4.5 MP/2500 px caps, bounded tiling, image cache, atomic writes, truthful empty/partial status |
| `services/parse_service.py` | Antigravity + fix | `DocumentError` import was missing (see defects) |
| `extractors/pymupdf_extractor.py` | **Overlap** (Antigravity + Codex) | merged cleanly; drawing-count guards and Codex table/math hooks coexist |
| `services/region_router.py` | **Overlap** (Antigravity + Codex + Claude) | cdrawings guard, deadline checks, cached overview, per-page re-read cap, scanned-math default-off gating, `route_scanned_math` hook |
| `extractors/text_math.py`, `vision/line_math.py`, `vision/pix2text_mfr.py`, `vision/models.py`, `vision/latex.py` | Claude origin, hardened by Codex | Codex's `text_math.py` is a strict superset of Claude's latest (radical fix, bullet guard, `\ldots`); all present |
| `extractors/table_geometry.py`, `services/layout_service.py`, `table_service.py`, `markdown_service.py` | Codex only | table row splitting, borderless grids, glyph ownership, footer detection |
| `services/scanned_math.py` | Codex + integration fix | render under pixel caps; **opt-in** `AXTRACT_SCANNED_MATH` (default 0) |
| `evaluation/*`, `docs/*`, `scripts/*` | Codex (tests/manifests/tools) + this integration | |

Feature flags: `AXTRACT_SCANNED_MATH` (default `0`), `AXTRACT_MFR_DIR`, `AXTRACT_FORMULA_SITE`,
`AXTRACT_MFR_PAGE_SECONDS` (20), `AXTRACT_MFR_MAX_LINES` (24), `AXTRACT_FORMULA_SCANNED` (legacy pix2tex on scans, off),
`AXTRACT_FORMULA_REREAD_PER_PAGE` (6). Needs the existing MFR weights (118 MB) only when scanned math is on.

## Defects found by the integration and fixed

1. **`parse_service.py` used `DocumentError` without importing it** (from the timeout commit). Any run reaching the
   deadline branch raised `NameError` and lost the partial output (two workers crashed in the first matrix run).
2. **Scanned math rendered at a fixed 200 DPI**, bypassing the timeout branch's pixel caps (huge scans were ~31 MP).
   Now rendered under the same pixel/side caps.
3. **Reusing the OCR stage's cached image for scanned math lost assertions** (different renderer, shifted line bands).
   Scanned math renders with the measured renderer; the OCR cache is still used by OCR.

## Tests

| Suite | Result |
|---|---|
| Focused suite (Codex's command + new interaction tests) | **219 passed**, 3 deselected (pre-existing: need absent `evaluation/fixtures/pymupdf_block_quality_pages.pdf`) |
| Full `backend/tests` on integrated tree | 1060 passed, 65 failed, 54 errors |
| Full `backend/tests` on untouched baseline checkout | 990 passed, 79 failed, 110 errors |
| Failures only on the integrated tree | **none** (integrated failure set is a subset of the baseline's) |

Remaining pre-existing failures are environment/fixture related (e.g. `test_encryption`: "Local document storage is disabled",
`test_regressions`, verify fault campaign fixtures). They are not caused by this integration.
New tests: default-off, exhausted deadline, no layout inference, render DPI cap, OCR-cache non-use, guard presence.

## Official results (exact assertion IDs compared)

### 17-PDF combined full pipeline (99 assertions) - both modes identical
| Category | Result |
|---|---|
| tables | 42/50 |
| digital math | 20/34 |
| headers/footers | 5/5 |
| multi-column | 6/10 |
| **total** | **73/99** (Codex starting integration 58/99). IDs identical to Codex's final: 0 gained, 0 lost |

### 32-PDF adapter sample (211 assertions)
| Category | Default (scanned math 0) | Scanned math 1 | Codex final (1) |
|---|---:|---:|---:|
| arxiv_math | 43/83 | 43/83 | 43/83 |
| old_scans_math | 0/84 | 19/84 | 20/84 |
| multi_column | 15/29 | 15/29 | 15/29 |
| headers_footers | 4/5 | 4/5 | 4/5 |
| long_tiny_text | 3/5 | 3/5 | 3/5 |
| tables | 5/5 | 5/5 | 5/5 |
| **total** | **70/211** | **89/211** | 90/211 |

Default mode: identical IDs to Codex's pre-math integration (0 gained / 0 lost; user-reported earlier figure was 67/211).
Scanned math on: one assertion fewer than Codex, `1_pg19_equation_13`, a budget-edge effect: the page stopped at the 20 s
cooperative page budget after 12 attempts on this machine (Codex: 13 attempts in 19.6 s). With a 30 s budget `1_pg19` scores 10/14, equal to Codex.

### Native table selections (stage-only, default mode)
original 51/80, development 51/102, held-out 39/92: **identical to Codex's frozen results, 0 differing IDs.**

### Antigravity's 22 historical timeout PDFs (129 assertions)
| | Saved timeout-branch outputs | Integrated default | Integrated scanned math 1 |
|---|---:|---:|---:|
| Official assertions | 25/129 | **26/129** | 25/129 |
| Newly passing | | `20_pg39_pg1_text_04` | `20_pg39_pg1_text_04` |
| Newly failing | | none | `10_235772` (`old_scans/10.pdf`) |
| Completed | 22/22 | 22/22 | 22/22 |
| Slowest document | | 45.0 s | 49.0 s |
| Median / total | | 7.2 s / 224 s | 11.2 s / 301 s |

## Runtime, memory, empties and partials (quiet machine, serial, one process per PDF)

| Run | Docs | Total | Median | Max | Peak working set | Empty outputs | Doc errors/timeouts |
|---|---:|---:|---:|---:|---:|---:|---:|
| 17-PDF (either flag) | 17 | 28 s | 1.4 s | 3.5 s | 561 MiB | 0 | 0 |
| 32-PDF, flag 0 | 32 | 69 s | 1.6 s | 6.3 s | 785 MiB | 0 | 0 |
| 32-PDF, flag 1 | 32 | 165 s | 1.6 s | 27.3 s | 785 MiB | 0 | 0 |
| 22 timeout PDFs, flag 0 | 22 | 224 s | 7.2 s | 45.0 s | 1464 MiB | 0 | 0 |
| 22 timeout PDFs, flag 1 | 22 | 301 s | 11.2 s | 49.0 s | 1464 MiB | 0 | 0 |

The vector document `arxiv_math/2503.09528_pg16` (peak 1464 MiB) takes 44-45 s integrated vs 42-50 s on the timeout branch (controlled A/B);
~25 s of it is the Verify inventory (pdfplumber on ~80k curves). Runs made while another scorer was running showed 63-99 s and
crashes from defect 1: timing is contention-sensitive, so measurements were repeated on a quiet machine.

## Unresolved / caveats

1. **Scanned math (experimental) can replace ordinary typed text**: on `old_scans/10.pdf` the line "Dear Sir:- Indiana County ..." was
   turned into an equation block and `10_235772` fails (match 0.934 vs 1.0). Keep `AXTRACT_SCANNED_MATH=0`; add a stronger prose guard before enabling.
2. Scanned-math results are speed-dependent at the default 20 s page budget (cooperative, not preemptive).
3. Layout inference is skipped unless the legacy pix2tex weights are available (timeout-branch choice); this can skip layout-based chart detection on scans on machines without those weights. Not exercised by the frozen selections.
4. Eight table failures remain on one raster page with corrupted native OCR spans (Codex report).
5. Pre-existing backend test failures listed above.
6. Verify inventory on pathological vector pages costs ~25 s (not guarded by the timeout branch).

## Safety

Source worktrees (`axtract`, `axtract-math-wt`, `axtract-timeout-wt`, Codex `math-table-integration`, `axtract-smart-bench-wt`):
`git status`/HEAD recorded before and compared after: **unchanged**. Codex's 1,949 dirty/untracked/ignored-relevant files and my 6 uncommitted files were
copied to `C:\Users\sdivy\wdev\Projects\_integration_backup_20261017` and SHA-256 verified (originals re-hashed at the end: identical).
`axtract-benchmarks` is still `7a2e205`. Official scorer, assertions, PDFs and all historical results untouched. No remote operations.

## Reproduction (PowerShell/bash from the integration worktree)

```bash
# focused tests
python -B -m pytest evaluation backend/tests/test_pdf_block_quality.py backend/tests/test_vision_units.py backend/tests/test_services.py backend/tests/test_extractors.py backend/tests/test_table_ops.py backend/tests/test_verify_sanitiser.py -q -p no:cacheprovider -k "not test_financial_highlights_text_and_footnotes_are_separated and not test_chart_grid_becomes_reviewable_figure_not_giant_table and not test_page_fourteen_chart_is_not_table_and_markers_are_detached"
# extraction matrix (32/17/22 PDFs x scanned math 0/1), fresh label each time
bash scripts/run_integration_matrix.sh <label>
# native table selections + scanned-math-on sets
bash scripts/run_final_checks.sh <label>
# score one output folder with the unchanged scorer (Python 3.12 scorer env)
integration_accuracy.py score --data-root <bench_data> --output <folder> --score-report official_case_results_utf8.json --scorer <scorer> --scorer-packages <site-packages>
# exact assertion-ID comparison
python scripts/compare_case_results.py BEFORE.json AFTER.json
```
Environment for scanned math: `AXTRACT_SCANNED_MATH=1`, `AXTRACT_MFR_DIR=...\axtract-math-wt\backend\model_weights\pix2text_mfr`,
`AXTRACT_FORMULA_SITE=...\axtract-math-wt\backend\model_weights\latex_ocr\site`. Result folders live under `backend/data/integration/accuracy_timeouts_r{2,3,4}` (gitignored).

## Ready for a full official run?

**Default mode (`AXTRACT_SCANNED_MATH=0`): yes**, with the caveats above. No regressions against any frozen reference, no timeouts, empty outputs or crashes.
Scanned-math mode: not recommended for a submission run until caveat 1 is addressed. A full 1,403-PDF run was **not** performed and needs your authorization.
