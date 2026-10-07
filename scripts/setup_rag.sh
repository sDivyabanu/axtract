#!/usr/bin/env bash
# One-time setup for the DealLens RAG layer (needs network ONCE; the app itself runs offline).
#   1. Ollama (local LLM runtime) - uses an existing install, otherwise downloads the official
#      binary into ~/Applications/ollama (no sudo)
#   2. pulls the default model (override: DEALLENS_LLM_MODEL=phi4-mini scripts/setup_rag.sh)
#   3. downloads embedding + reranker weights and records/verifies SHA-256 digests
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="${DEALLENS_LLM_MODEL:-qwen3:4b}"          # Apache-2.0, ~2.5 GB, fits 8 GB RAM
PY="${ROOT}/backend/.venv/bin/python"
OLLAMA_HOME="$HOME/Applications/ollama"

find_ollama() {
  if command -v ollama >/dev/null 2>&1; then command -v ollama; return; fi
  [ -x "$OLLAMA_HOME/ollama" ] && echo "$OLLAMA_HOME/ollama"
}

OLLAMA="$(find_ollama || true)"
if [ -z "$OLLAMA" ]; then
  echo ">> Ollama not found; downloading the official release to $OLLAMA_HOME"
  mkdir -p "$OLLAMA_HOME"
  case "$(uname -s)" in
    Darwin) URL="https://github.com/ollama/ollama/releases/latest/download/ollama-darwin.tgz" ;;
    Linux)  URL="https://github.com/ollama/ollama/releases/latest/download/ollama-linux-$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tgz" ;;
    *) echo "Unsupported OS; install Ollama manually from https://ollama.com" >&2; exit 1 ;;
  esac
  curl -fL --retry 3 -o "$OLLAMA_HOME/ollama.tgz" "$URL"
  tar -xzf "$OLLAMA_HOME/ollama.tgz" -C "$OLLAMA_HOME" && rm "$OLLAMA_HOME/ollama.tgz"
  OLLAMA="$OLLAMA_HOME/ollama"
fi
echo ">> using $OLLAMA"

if ! curl -fs --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null; then
  echo ">> starting ollama serve in the background"
  (nohup "$OLLAMA" serve >"${TMPDIR:-/tmp}/ollama.log" 2>&1 &)
  for _ in $(seq 1 30); do curl -fs --max-time 1 http://127.0.0.1:11434/api/tags >/dev/null && break; sleep 1; done
fi

echo ">> pulling $MODEL"
"$OLLAMA" pull "$MODEL"

echo ">> embedding + reranker weights"
"$PY" "$ROOT/scripts/fetch_rag_models.py"
echo ">> done. Start the backend:  cd backend && .venv/bin/python -m uvicorn main:app --port 8000"
echo "   If Ollama is not running the app falls back to extractive mode (no generation)."
