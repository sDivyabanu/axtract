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
LLM_NUM_CTX = int(os.environ.get("DEALLENS_LLM_CTX", "6144"))

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
RRF_K = 60
