#!/usr/bin/env bash
# Sequential extraction matrix for the accuracy+timeouts integration. Fresh output labels only.
# usage: scripts/run_integration_matrix.sh <label-suffix>
set -u
SUF="${1:?label suffix}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
APP=C:/Users/sdivy/wdev/Projects/axtract/backend/.venv/Scripts/python.exe
DATA=C:/Users/sdivy/wdev/Projects/axtract/benchmarks/olmOCR-bench/bench_data
SEL32=C:/Users/sdivy/wdev/Projects/axtract-math-wt/bench_math_sample/selection.json
SEL22="$ROOT/evaluation/timeout_recovery_selection.json"
OUT=backend/data/integration/accuracy_timeouts_$SUF
export PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 AXTRACT_ORT_THREADS=2 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
export AXTRACT_MFR_DIR=C:/Users/sdivy/wdev/Projects/axtract-math-wt/backend/model_weights/pix2text_mfr
export AXTRACT_FORMULA_SITE=C:/Users/sdivy/wdev/Projects/axtract-math-wt/backend/model_weights/latex_ocr/site
export AXTRACT_MFR_PAGE_SECONDS=20 AXTRACT_MFR_MAX_LINES=24
mkdir -p "$OUT"
run() { # label mode selection-flag-args... (env AXTRACT_SCANNED_MATH set by caller)
  local label=$1; shift
  echo "=== $label $(date +%T)"
  "$APP" -B -X utf8 scripts/integration_accuracy.py extract --data-root "$DATA" --output "$OUT/$label" "$@" || echo "!! $label exit $?"
}
for flag in 0 1; do
  export AXTRACT_SCANNED_MATH=$flag
  run "adapter32_sm$flag" --mode adapter --selection "$SEL32"
  run "full17_sm$flag" --mode full
  run "timeout22_sm$flag" --mode adapter --selection "$SEL22"
done
echo "=== DONE $(date +%T)"
