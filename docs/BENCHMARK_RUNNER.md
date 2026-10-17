> Local access now uses the verified loopback launcher; see [LOCAL_DEVELOPMENT.md](LOCAL_DEVELOPMENT.md). No account configuration is required.

# Smart Benchmark Runner

Test AXTRACT against the official olmOCR-bench quickly, without re-running all 1,403 PDFs after every change.
It reuses AXTRACT's extraction (`scripts/benchmark_axtract_olmocr.py::process_single_pdf`) and the **unmodified**
official scorer (`olmocr.bench.benchmark`). Quick results are *samples*, never full official scores.

## What it can do

- **Quick mode**: a deterministic sample (default 5 PDFs per category, seed 42, configurable) of all or chosen categories.
- **Change-aware testing**: `git` tells us which files changed; rules map them to the categories at risk
  (OCR to scanned and tiny-text, tables to tables, layout to multi-column and headers, equations to the math sets,
  shared pipeline code to everything). You can always override the recommendation.
- **Before/after comparison**: commit SHAs, categories, pass rates, newly passing and newly failing cases, latency.
  Only directly compatible runs (same dataset files, scoring configuration and sample) are compared; otherwise it
  refuses and says why. A labelled *partial* comparison on the shared cases can be requested explicitly.
- **History and trends**: every run is kept in the workspace with time, commit, mode, sample identity, category
  scores, duration and status; the dashboard draws a like-for-like trend per sample.
- **Failure inspector**: filter and page through failed cases, see the expected condition in plain words, the scorer's
  feedback, the actual extracted Markdown (capped), and the extraction log. The huge HTML report is never loaded.
- **Full benchmark mode**: the complete official run, behind an explicit confirmation. Bounded workers, per-document
  timeout, checkpoint after every document, cancel and resume. It is never started automatically.
- **Import**: read the saved artifacts of a finished official run without re-running anything (see below).

## Result honesty

| `score_kind` | Meaning |
|---|---|
| `quick_sample` | Sample of the benchmark. Never an official score. |
| `full_official` | Complete run, every document produced output, official scorer returned a score. |
| `full_incomplete` | A full run with missing outputs. Diagnostic only. |
| `imported_invalid` | An imported official run the scorer rejected (`FAILED (errors)`). Score is `null`; any percentage is diagnostic. |
| `imported_official` | An imported official run with a valid score. |

When any document produced no output (for example a timeout) the official scorer refuses to score the candidate.
The runner keeps that verdict (`validity: "invalid"`, `overall_score_pct: null`) and also reports per-category pass
rates computed from the per-case results, marked `basis: "diagnostic"`. Missing outputs count as failures there.

## Configuration (environment)

| Variable | Default | Purpose |
|---|---|---|
| `AXTRACT_BENCH_DATA_DIR` | `benchmarks/olmOCR-bench/bench_data` | Official dataset (`pdfs/` and `*.jsonl`). Read-only. |
| `AXTRACT_BENCH_WORKSPACE` | `benchmark_runs/` | Where runs are written. Must not overlap the dataset, `reports/benchmarks` or the import root. |
| `AXTRACT_BENCH_SCORER_PYTHON` | `benchmarks/.venv/Scripts/python.exe` | Python with `olmocr` installed (runs the official scorer). |
| `AXTRACT_BENCH_EXTRACT_PYTHON` | the API server's Python | Interpreter for extraction worker processes. |
| `AXTRACT_BENCH_IMPORT_ROOT` | `<repo>/reports/benchmarks/olmocr` | Read-only folder official-run artifacts may be imported from. |

`benchmarks/` is git-ignored, so from a git worktree point `AXTRACT_BENCH_DATA_DIR` and `AXTRACT_BENCH_SCORER_PYTHON`
at the main checkout's copies (this only reads them).

## How a run works

1. **Discover**: categories come from the dataset (each `*.jsonl` row's `pdf` folder). A PDF counts only if it exists
   and has official test cases.
2. **Sample**: per category `random.Random(f"{seed}:{category}")` picks N PDFs; categories are independent and
   PDFs are de-duplicated. `manifest.json` records every PDF and test case id. `sample_id` and `dataset_id` identify
   what was tested so runs can be compared safely.
3. **Isolate**: PDFs are hard-linked (or copied) and the test files filtered into `runs/<id>/scoring/`. Nothing is
   written to the dataset, the official candidate folders or `reports/`.
4. **Extract**: a pool of warm worker processes runs the existing adapter. Each document has a hard timeout; a worker
   that times out, crashes or is cancelled is killed with its children and replaced. Children run at reduced priority.
   Every finished document is appended to `docs.jsonl` (fsynced); `progress.json` is a throttled snapshot.
5. **Score**: `score_wrapper.py` runs the official scorer's `main()` with the official full-run flags
   (`--force --skip_baseline`) under the scorer's own Python. Only the HTML report generator is replaced by a compact
   per-case record (`outcomes.jsonl`); the real HTML report is also produced for quick runs.
6. **Resume**: only documents not yet `done` are redone (failures and timeouts are retried). A run whose process
   died shows as `interrupted`. A heartbeat lock allows one active run.

Guards: refused while the official benchmark's Python processes are running, while another run holds the lock, with
too little free memory, and for unknown categories, oversized samples or invalid ids.

## Importing the 05deead official run

`POST /api/benchmark/imports {"source": "current_05deead/official_05deead_001"}` (or the Import tab, or
`python -m benchmark.cli import current_05deead/official_05deead_001`).

It reads the small JSON artifacts, converts `case_outcomes_diagnostic.jsonl` into the per-case record, and never opens
the 800 MB `official_results.html`. The imported run keeps `official_status: "FAILED (errors)"`,
`overall_score_pct: null`, `is_official: false`, and shows the 22.28% only as a *diagnostic average*. Importing twice
returns the same run. The source folder must be inside the import root.

## API (`/api/benchmark`, all require verified local-loopback access)

`GET /environment` · `GET /categories` · `GET /changes?base=` · `POST /runs` · `GET /runs` · `GET /runs/active` ·
`GET /runs/{id}` · `GET /runs/{id}/results|manifest|log|report|cases|case?case_id=` · `POST /runs/{id}/resume|cancel` ·
`GET /compare?a=&b=&allow_partial=` · `GET /trends?sample_id=` · `GET /imports` · `POST /imports`

## CLI (no web server, no login)

```
cd backend
python -m benchmark.cli categories
python -m benchmark.cli run --categories tables,multi_column --per-category 2 --workers 1
python -m benchmark.cli recommend
python -m benchmark.cli compare RUN_A RUN_B
python -m benchmark.cli import current_05deead/official_05deead_001
```

Ctrl-C during `run` cancels cleanly; `resume RUN_ID` continues.

## Dashboard

Sidebar, **Benchmark** (`/benchmark`): Run, History and trends, Compare, Failures, Import.
No account is required on the verified local listener. After a page refresh it re-attaches to the running benchmark.

## Tests

```
cd backend && python -m pytest benchmark/tests -q        # mocked extraction and scoring, synthetic data
cd frontend && npm test && npx tsc --noEmit && npx eslint app/benchmark components/benchmark lib
```
