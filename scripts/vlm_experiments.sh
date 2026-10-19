#!/usr/bin/env bash
# Throughput/accuracy matrix on the fixed 14-page frozen-70 subset (first 14 manifest PDFs: 2 per category, 92 assertions).
# One configuration at a time; each variant gets its own llama-server on port 8091, stopped before the next starts.
# Usage: bash scripts/vlm_experiments.sh <runs-dir> <assets-dir> <data-root> <scorer-root>
set -u
RUNS=$1; ASSETS=$2; DATA=$3; SC=$4
PY=.venv/Scripts/python.exe
SUBSET=$($PY -c "import json;print(' '.join(list(json.load(open('evaluation/frozen70/manifest.json'))['pdfs'])[:14]))")
SCORE_ARGS="--scorer-python $SC/python/cpython-3.12.15-windows-x86_64-none/python.exe --scorer $SC/scorer --scorer-packages $SC/scorer312/Lib/site-packages"

run() {  # name model mmproj np concurrency longest port
  local name=$1 model=$2 mmproj=$3 np=$4 conc=$5 longest=$6 port=$7 pid=""
  local out=$RUNS/sub14_$name
  if [ "$port" != 8090 ]; then
    "$ASSETS/llama-vulkan/llama-server.exe" -m "$ASSETS/models/$model" --mmproj "$ASSETS/models/$mmproj" --host 127.0.0.1 \
      --port $port -c $((8192 * np)) -ngl 99 --jinja --chat-template-kwargs '{"enable_thinking": false}' -np $np \
      > "$RUNS/server_$name.log" 2>&1 &
    pid=$!
    until curl -s 127.0.0.1:$port/health | grep -q ok; do sleep 2; done
  fi
  $PY -X utf8 scripts/vlm_throughput.py --data-root "$DATA" --out "$out" --server http://127.0.0.1:$port \
      --model-id "${model%.gguf}" --longest $longest --concurrency $conc --only $SUBSET > "$out.log" 2>&1
  [ -n "$pid" ] && kill $pid 2>/dev/null; [ -n "$pid" ] && wait $pid 2>/dev/null
  $PY scripts/frozen70_benchmark.py score --out "$out" $SCORE_ARGS > /dev/null 2>&1
  echo "== $name: $(tail -1 "$out.log")"
}

run e0_q8_np1_2048   lo3-0.8b-Q8_0.gguf   lo3-0.8b-mmproj-f16.gguf 1 1 2048 8090
run e1_q8_np4_2048   lo3-0.8b-Q8_0.gguf   lo3-0.8b-mmproj-f16.gguf 4 4 2048 8091
run e2_q4km_np4_2048 lo3-0.8b-Q4_K_M.gguf lo3-0.8b-mmproj-f16.gguf 4 4 2048 8091
run e3_q6k_np4_2048  lo3-0.8b-Q6_K.gguf   lo3-0.8b-mmproj-f16.gguf 4 4 2048 8091
run e4_q8_np4_1540   lo3-0.8b-Q8_0.gguf   lo3-0.8b-mmproj-f16.gguf 4 4 1540 8091
run e5_q8_np8_2048   lo3-0.8b-Q8_0.gguf   lo3-0.8b-mmproj-f16.gguf 8 8 2048 8091
echo ALL DONE
