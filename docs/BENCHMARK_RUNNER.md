# Smart Benchmark Runner (Phase 1)

Runs a small, deterministic sample of the official olmOCR-bench through AXTRACT's existing
extraction (`scripts/benchmark_axtract_olmocr.py::process_single_pdf`) and the unmodified
official scorer (`olmocr.bench.benchmark`). Results are always labelled **quick sample scores**,
never full official scores.

## Configuration (environment)

| Variable | Default | Purpose |
|---|---|---|
| `AXTRACT_BENCHMARK_USERS` | empty (nobody) | Comma-separated Supabase user ids or emails allowed to use the API. `*` = any signed-in user. |
| `AXTRACT_BENCH_DATA_DIR` | `benchmarks/olmOCR-bench/bench_data` | Official dataset (`pdfs/` + `*.jsonl`). Read-only. |
| `AXTRACT_BENCH_WORKSPACE` | `benchmark_runs/` | Where runs are written. Must not overlap the dataset or `reports/benchmarks`. |
| `AXTRACT_BENCH_SCORER_PYTHON` | `benchmarks/.venv/Scripts/python.exe` | Python with `olmocr` installed (runs the official scorer). |

`benchmarks/` is git-ignored, so from a git worktree point `AXTRACT_BENCH_DATA_DIR` and
`AXTRACT_BENCH_SCORER_PYTHON` at the main checkout's copies.

## How a run works

1. **Discover**: categories come from the dataset itself (each `*.jsonl` row's `pdf` folder). A PDF counts
   only if it exists on disk and has at least one official test case.
2. **Sample**: per category, `random.Random(f"{seed}:{category}")` picks N PDFs (default 5, seed 42).
   Categories are independent, PDFs are de-duplicated, and the exact PDFs and test ids go in `manifest.json`.
3. **Isolate**: PDFs are copied and the jsonl files filtered into `runs/<id>/scoring/`. Nothing is written
   to the dataset, the official candidate folders or `reports/`.
4. **Extract**: a bounded thread pool (default 2, max 4 workers) runs the existing adapter. A failing
   document is recorded and never stops the run. `progress.json` is rewritten atomically after each document.
5. **Score**: the official scorer runs once over the sample (`--force`, `--test_report`, `--output_failed`);
   its summary is parsed into overall and per-category scores. Output: `results.json`, `report.html`,
   `failed_tests.jsonl`, `run.log`, `scorer_output.txt`.
6. **Resume**: `POST /runs/{id}/resume` re-runs only documents not yet `done` (failed ones are retried),
   then scores. A heartbeat lock allows one active run; a run whose process died shows as `interrupted`.

Guards: starting is refused (409) while the official benchmark's processes are running, while another
run holds the lock, and for unknown categories, oversized samples or invalid run ids.

## API (`/api/benchmark`, all require an allow-listed user)

`GET /categories` · `POST /runs` · `GET /runs` · `GET /runs/{id}` · `GET /runs/{id}/results` ·
`GET /runs/{id}/manifest` · `GET /runs/{id}/log` · `GET /runs/{id}/report` ·
`POST /runs/{id}/resume` · `POST /runs/{id}/cancel` · `GET /compare?a=&b=`

## Dashboard

Sidebar, "Benchmark" (`/benchmark`). Start backend and frontend as usual, sign in as an allow-listed user.

## Tests

```
cd backend && python -m pytest benchmark/tests -q
```

Synthetic fixtures with mocked extraction and scoring; no PDF is opened and the scorer is not launched.
