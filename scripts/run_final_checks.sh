#!/usr/bin/env bash
# Final verification pass on the committed tree: native table selections, then scanned-math-on sets. Fresh labels only.
set -u
SUF="${1:?label}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
echo "=== native tables $(date +%T)"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/run_table_accuracy_suite.ps1 -Label "integ_$SUF" || echo "!! native exit $?"
APP=C:/Users/sdivy/wdev/Projects/axtract/backend/.venv/Scripts/python.exe
DATA=C:/Users/sdivy/wdev/Projects/axtract/benchmarks/olmOCR-bench/bench_data
export PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 AXTRACT_ORT_THREADS=2 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 AXTRACT_SCANNED_MATH=1
export AXTRACT_MFR_DIR=C:/Users/sdivy/wdev/Projects/axtract-math-wt/backend/model_weights/pix2text_mfr
export AXTRACT_FORMULA_SITE=C:/Users/sdivy/wdev/Projects/axtract-math-wt/backend/model_weights/latex_ocr/site
export AXTRACT_MFR_PAGE_SECONDS=20 AXTRACT_MFR_MAX_LINES=24
OUT=backend/data/integration/accuracy_timeouts_$SUF; mkdir -p "$OUT"
echo "=== adapter32_sm1 $(date +%T)"; "$APP" -B -X utf8 scripts/integration_accuracy.py extract --data-root "$DATA" --output "$OUT/adapter32_sm1" --mode adapter --selection C:/Users/sdivy/wdev/Projects/axtract-math-wt/bench_math_sample/selection.json
echo "=== timeout22_sm1 $(date +%T)"; "$APP" -B -X utf8 scripts/integration_accuracy.py extract --data-root "$DATA" --output "$OUT/timeout22_sm1" --mode adapter --selection evaluation/timeout_recovery_selection.json
echo "=== DONE $(date +%T)"
