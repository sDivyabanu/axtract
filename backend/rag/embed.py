"""Local embeddings (bge-small, ONNX) and cross-encoder reranker (MiniLM, ONNX) via fastembed."""

from __future__ import annotations

import os
import threading
from typing import Sequence

import numpy as np

from rag import config

_lock = threading.Lock()
_embedder = None
_reranker = None


def _offline_if_pinned() -> None:
    """After scripts/setup_rag.sh has recorded the weight digests, never touch the network."""
    if (config.MODEL_DIR / "MANIFEST.sha256").exists():
        os.environ.setdefault("HF_HUB_OFFLINE", "1")


def _get_embedder():
    global _embedder
    with _lock:
        if _embedder is None:
            _offline_if_pinned()
            from fastembed import TextEmbedding

            _embedder = TextEmbedding(config.EMBED_MODEL, cache_dir=str(config.MODEL_DIR))
        return _embedder


def _get_reranker():
    global _reranker
    with _lock:
        if _reranker is None:
            _offline_if_pinned()
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            _reranker = TextCrossEncoder(config.RERANK_MODEL, cache_dir=str(config.MODEL_DIR))
        return _reranker


def embed_texts(texts: Sequence[str], batch_size: int = 32) -> np.ndarray:
    """L2-normalised float32 vectors, shape (n, dim)."""
    if not texts:
        return np.zeros((0, 384), dtype=np.float32)
    vecs = np.array(list(_get_embedder().embed(list(texts), batch_size=batch_size)), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.maximum(norms, 1e-9)


def embed_query(q: str) -> np.ndarray:
    return embed_texts([config.QUERY_PREFIX + q])[0]


def rerank(query: str, docs: Sequence[str]) -> list[float]:
    """Cross-encoder relevance logits (higher = more relevant), aligned with `docs`."""
    if not docs:
        return []
    return [float(s) for s in _get_reranker().rerank(query, [d[:1100] for d in docs])]
