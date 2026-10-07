"""Settings, overridable by environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from utils.files import DATA_DIR

DB_PATH = Path(os.environ.get("DEALLENS_DB", DATA_DIR / "deallens.db"))
FILES_DIR = DATA_DIR / "files"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
LLM_MODEL = os.environ.get("DEALLENS_LLM_MODEL", "qwen3:4b-instruct")  # Apache-2.0; see docs/RAG_DESIGN.md
LLM_TIMEOUT_S = float(os.environ.get("DEALLENS_LLM_TIMEOUT", "120"))
# -1 = keep the model in memory until Ollama stops (no first-answer delay mid-demo; costs ~2.5 GB of RAM while it is up)
LLM_KEEP_ALIVE = os.environ.get("DEALLENS_KEEP_ALIVE", "-1")
# The LLM planner (JSON plan for the table calculator) is opt-in: on the golden set it never produced an answer the rules did not,
# and on large reports it cost 4-20 s per numeric-sounding question.
LLM_PLANNER = os.environ.get("DEALLENS_LLM_PLANNER", "0") == "1"
ANSWER_MAX_TOKENS = int(os.environ.get("DEALLENS_ANSWER_TOKENS", "170"))  # ~60 words; at ~20 tok/s every extra token is 50 ms
WARMUP = os.environ.get("DEALLENS_WARMUP", "1") != "0"
LLM_NUM_CTX = int(os.environ.get("DEALLENS_LLM_CTX", "4096"))  # prompt budget ~1.1k tokens + system + 250 answer; planner catalogs are capped well below this

MODEL_DIR = Path(os.environ.get("DEALLENS_RAG_MODEL_DIR", Path(__file__).resolve().parents[1] / "model_weights" / "rag"))
EMBED_MODEL = "BAAI/bge-small-en-v1.5"  # MIT
RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"  # Apache-2.0
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# Parsing a large data-room document may take longer than the public 60 s parse contract.
PARSE_BUDGET_S = float(os.environ.get("DEALLENS_PARSE_BUDGET", "240"))

# Chunking
TARGET_CHUNK_TOKENS = 450
MAX_CHUNK_TOKENS = 600
EMBED_MAX_CHARS = 1800  # long tables are embedded from a prefix; BM25 still sees everything

# Retrieval
RETRIEVE_K = 40
RERANK_K = 14
FINAL_K = 6
PROMPT_K = int(os.environ.get("DEALLENS_PROMPT_K", "4"))               # sources actually sent to the LLM (the rest stay available as citations)
PROMPT_CHARS = int(os.environ.get("DEALLENS_PROMPT_CHARS", "3200"))     # hard budget for all source text in one prompt (~0.8k tokens)
RRF_K = 60
