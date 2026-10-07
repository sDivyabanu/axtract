"""Per-workspace hybrid index: BM25 + dense vectors, Reciprocal Rank Fusion, cross-encoder rerank.

Everything is scoped to one workspace (tenant isolation) and quarantined chunks are never
loaded. The index lives in memory and is rebuilt from SQLite when the workspace changes.
"""

from __future__ import annotations

import math
import re
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from rag import config, db, embed

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?|[₹$€£%]")
_STOP = frozenset(
    "a an and are as at be but by for from has have in is it its of on or that the this to was were will with "
    "what which who whom how many much does do did there their they".split()
)


def stem(t: str) -> str:
    """Very light suffix stripping so 'maturing', 'matures' and 'maturity' share a term."""
    if len(t) < 5 or t.isdigit():
        return t
    if t.endswith("ies") and len(t) > 5:
        return t[:-3] + "y"
    for suf in ("ing", "ity", "ed", "es", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 4:
            return t[: -len(suf)]
    return t


def tokenize(text: str) -> list[str]:
    text = text.lower().replace(",", "")
    return [stem(t) for t in _TOKEN.findall(text) if t not in _STOP]


@dataclass
class Hit:
    chunk_id: str
    bm25: float = 0.0
    bm25_rank: int | None = None
    dense: float = 0.0
    dense_rank: int | None = None
    rrf: float = 0.0
    rerank: float | None = None
    used: bool = False


@dataclass
class _Index:
    ids: list[str]
    rows: dict[str, dict[str, Any]]
    matrix: np.ndarray
    postings: dict[str, list[tuple[int, int]]]
    lengths: np.ndarray
    avg_len: float
    version: int = 0


_lock = threading.Lock()
_cache: dict[str, _Index] = {}
_versions: dict[str, int] = defaultdict(int)


def invalidate(workspace_id: str) -> None:
    """Call after any write that changes a workspace's chunks."""
    with _lock:
        _versions[workspace_id] += 1
        _cache.pop(workspace_id, None)


def _build(workspace_id: str) -> _Index:
    with db.connect() as c:
        rows = c.execute(
            "SELECT chunk_id, doc_id, kind, text, embed_text, heading_json, block_ids_json, pages_json, printed_json,"
            " bboxes_json, min_confidence, flags_json, meta_json, table_ref, embedding FROM chunks"
            " WHERE workspace_id=? AND trust='normal' ORDER BY ord", (workspace_id,)).fetchall()
    ids, meta_rows, vecs = [], {}, []
    postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
    lengths = []
    for i, r in enumerate(rows):
        cid = r["chunk_id"]
        ids.append(cid)
        meta_rows[cid] = {
            "chunk_id": cid, "doc_id": r["doc_id"], "kind": r["kind"], "text": r["text"],
            "heading_path": db.jload(r["heading_json"], []), "block_ids": db.jload(r["block_ids_json"], []),
            "pages": db.jload(r["pages_json"], []), "printed_pages": db.jload(r["printed_json"], []),
            "bboxes": db.jload(r["bboxes_json"], []), "min_confidence": r["min_confidence"],
            "flags": db.jload(r["flags_json"], []), "meta": db.jload(r["meta_json"], {}), "table_ref": r["table_ref"],
        }
        vecs.append(np.frombuffer(r["embedding"], dtype=np.float32) if r["embedding"] else np.zeros(384, np.float32))
        toks = tokenize(r["embed_text"] + " " + r["text"])
        tf: dict[str, int] = defaultdict(int)
        for t in toks:
            tf[t] += 1
        for t, f in tf.items():
            postings[t].append((i, f))
        lengths.append(len(toks))
    matrix = np.vstack(vecs).astype(np.float32) if vecs else np.zeros((0, 384), np.float32)
    lens = np.array(lengths, dtype=np.float32) if lengths else np.zeros(0, np.float32)
    return _Index(ids, meta_rows, matrix, dict(postings), lens, float(lens.mean()) if len(lens) else 1.0,
                  _versions[workspace_id])


def get_index(workspace_id: str) -> _Index:
    with _lock:
        idx = _cache.get(workspace_id)
    if idx is not None:
        return idx
    idx = _build(workspace_id)
    with _lock:
        _cache[workspace_id] = idx
    return idx


def chunk_row(workspace_id: str, chunk_id: str) -> dict[str, Any] | None:
    return get_index(workspace_id).rows.get(chunk_id)


def _passes(row: dict[str, Any], doc_ids, doc_types, periods, kinds) -> bool:
    if doc_ids and row["doc_id"] not in doc_ids:
        return False
    if kinds and row["kind"] not in kinds:
        return False
    if doc_types and row["meta"].get("doc_type") not in doc_types:
        return False
    if periods and row["meta"].get("period") not in periods:
        return False
    return True


def search(
    workspace_id: str,
    query: str,
    *,
    doc_ids: set[str] | None = None,
    doc_types: set[str] | None = None,
    periods: set[str] | None = None,
    kinds: set[str] | None = None,
    k: int = config.RETRIEVE_K,
    dense_only: bool = False,
) -> list[Hit]:
    """Hybrid retrieval (BM25 + dense, fused with RRF). `dense_only` is used by the naive baseline."""
    idx = get_index(workspace_id)
    n = len(idx.ids)
    if n == 0:
        return []
    allowed = np.array([_passes(idx.rows[cid], doc_ids, doc_types, periods, kinds) for cid in idx.ids])
    if not allowed.any():
        return []

    # dense
    qv = embed.embed_query(query)
    dense = idx.matrix @ qv
    dense = np.where(allowed, dense, -np.inf)
    dense_order = np.argsort(-dense)[:k]

    # BM25 (Okapi, k1=1.5, b=0.75)
    bm = np.zeros(n, dtype=np.float32)
    if not dense_only:
        k1, b = 1.5, 0.75
        for term in set(tokenize(query)):
            post = idx.postings.get(term)
            if not post:
                continue
            idf = math.log(1 + (n - len(post) + 0.5) / (len(post) + 0.5))
            for i, f in post:
                bm[i] += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * idx.lengths[i] / idx.avg_len))
        bm = np.where(allowed, bm, 0.0)
    bm_order = [i for i in np.argsort(-bm)[:k] if bm[i] > 0]

    hits: dict[int, Hit] = {}
    for rank, i in enumerate(dense_order, 1):
        if not np.isfinite(dense[i]):
            continue
        h = hits.setdefault(int(i), Hit(idx.ids[i]))
        h.dense, h.dense_rank = float(dense[i]), rank
        h.rrf += 1.0 / (config.RRF_K + rank)
    for rank, i in enumerate(bm_order, 1):
        h = hits.setdefault(int(i), Hit(idx.ids[i]))
        h.bm25, h.bm25_rank = float(bm[i]), rank
        h.rrf += 1.0 / (config.RRF_K + rank)
    return sorted(hits.values(), key=lambda h: -h.rrf)[:k]


def rerank_hits(workspace_id: str, query: str, hits: list[Hit], top: int = config.RERANK_K) -> list[Hit]:
    """Cross-encoder rerank of the best fused candidates."""
    idx = get_index(workspace_id)
    cand = hits[:top]
    scores = embed.rerank(query, [idx.rows[h.chunk_id]["text"] if idx.rows[h.chunk_id]["kind"] != "table_summary"
                                  else idx.rows[h.chunk_id]["text"] for h in cand])
    for h, s in zip(cand, scores):
        h.rerank = s
    return sorted(cand, key=lambda h: -(h.rerank if h.rerank is not None else -1e9))


def rrf_fuse(rankings: list[list[str]], k: int = config.RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion over several ranked id lists (exposed for tests)."""
    score: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, cid in enumerate(ranking, 1):
            score[cid] += 1.0 / (k + rank)
    return sorted(score.items(), key=lambda kv: -kv[1])


def coverage(workspace_id: str, query: str, texts: list[str]) -> float:
    """idf-weighted share of the question's terms found in `texts` (0-1).

    A question about the "chief executive officer" scores ~0 against passages that only repeat the
    company name, because the rare terms are missing; a table lookup scores high when its row and
    column words are present. Terms absent from the whole workspace weigh the most.
    """
    idx = get_index(workspace_id)
    n = max(len(idx.ids), 1)
    q = set(tokenize(query))
    if not q:
        return 0.0
    have = set()
    for t in texts:
        have |= set(tokenize(t))
    total = got = 0.0
    for term in q:
        df = len(idx.postings.get(term, ()))
        idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
        total += idf
        if term in have:
            got += idf
    return got / total if total else 0.0
