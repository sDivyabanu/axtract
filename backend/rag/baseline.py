"""The deliberately naive baseline pipeline (for the Compare page and the evaluation).

What a typical quick RAG demo does: plain text extraction (pypdf / python-docx / openpyxl / python-pptx text only,
no OCR, no layout, no tables, hidden text included), fixed 500-token chunks, dense-only retrieval, the same local LLM,
no citations check, no abstention, no quarantine. Nothing here is rigged to fail: it simply lacks the safeguards.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from rag import config, db, embed, llm, verifier, workspaces

CHUNK_TOKENS = 500
TOP_K = 4
_cache: dict[str, dict[str, Any]] = {}


def _pages_text(path: Path, file_type: str) -> list[tuple[int, str]]:
    """(page, text) pairs from plain extractors only."""
    if file_type == "pdf":
        from pypdf import PdfReader

        r = PdfReader(str(path))
        return [(i + 1, (p.extract_text() or "")) for i, p in enumerate(r.pages)]
    if file_type == "docx":
        import docx

        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return [(1, "\n".join(parts))]
    if file_type == "xlsx":
        import openpyxl

        wb = openpyxl.load_workbook(str(path), data_only=True)  # hidden sheets included, as a naive reader would
        out = []
        for i, ws in enumerate(wb.worksheets, 1):
            lines = [" | ".join("" if c is None else str(c) for c in row) for row in ws.iter_rows(values_only=True)]
            out.append((i, f"Sheet {ws.title}\n" + "\n".join(lines)))
        return out
    if file_type == "pptx":
        from pptx import Presentation

        prs = Presentation(str(path))
        return [(i + 1, "\n".join(sh.text_frame.text for sh in s.shapes if sh.has_text_frame)) for i, s in enumerate(prs.slides)]
    return []  # images: no OCR in the baseline


def _chunks(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Fixed windows of ~CHUNK_TOKENS tokens (words x 1.3), ignoring structure."""
    words_per = int(CHUNK_TOKENS / 1.3)
    out: list[tuple[int, str]] = []
    for page, text in pages:
        words = text.split()
        for i in range(0, len(words), words_per):
            out.append((page, " ".join(words[i: i + words_per])))
    return out


def _index(workspace_id: str) -> dict[str, Any]:
    docs = [d for d in workspaces.documents(workspace_id) if d["status"] == "ready"]
    key = "|".join(sorted(d["sha256"] for d in docs))
    cached = _cache.get(workspace_id)
    if cached and cached["key"] == key:
        return cached
    with db.connect() as c:
        paths = {r["id"]: r["source_path"] for r in c.execute("SELECT id, source_path FROM documents WHERE workspace_id=?", (workspace_id,))}
    items: list[dict[str, Any]] = []
    for d in docs:
        p = paths.get(d["doc_id"])
        if not p or not Path(p).exists():
            continue
        try:
            for page, text in _chunks(_pages_text(Path(p), d["file_type"])):
                if text.strip():
                    items.append({"doc_id": d["doc_id"], "filename": d["filename"], "page": page, "text": text})
        except Exception:  # noqa: BLE001 - a naive pipeline just skips what it cannot read
            continue
    mat = embed.embed_texts([i["text"] for i in items]) if items else np.zeros((0, 384), np.float32)
    idx = {"key": key, "items": items, "matrix": mat}
    _cache[workspace_id] = idx
    return idx


def answer(workspace_id: str, question: str) -> dict[str, Any]:
    """Run the baseline end to end. Always returns an answer (it never abstains)."""
    t0 = time.time()
    stages: list[dict[str, Any]] = []
    t = time.time()
    idx = _index(workspace_id)
    stages.append({"name": "Index (plain text, 500-token chunks)", "ms": round((time.time() - t) * 1000)})

    t = time.time()
    if not idx["items"]:
        return {"text": "No text could be extracted.", "mode": "baseline", "model": None, "sources": [], "stages": stages,
                "total_ms": round((time.time() - t0) * 1000), "pipeline": "baseline", "checks": None}
    sims = idx["matrix"] @ embed.embed_query(question)
    top = [int(i) for i in np.argsort(-sims)[:TOP_K]]
    sources = [{"filename": idx["items"][i]["filename"], "page": idx["items"][i]["page"], "text": idx["items"][i]["text"],
                "dense": round(float(sims[i]), 4)} for i in top]
    stages.append({"name": "Retrieve (dense only)", "ms": round((time.time() - t) * 1000)})

    t = time.time()
    context = "\n\n".join(s["text"] for s in sources)
    mode, model = "extractive", None
    if llm.status()["available"]:
        try:
            text = llm.chat([{"role": "system", "content": "You are a helpful assistant."},
                             {"role": "user", "content": f"Answer the question using the context.\n\nContext:\n{context}\n\nQuestion: {question}"}],
                            max_tokens=220)
            mode, model = "llm", config.LLM_MODEL
        except RuntimeError:
            text = sources[0]["text"][:400]
    else:
        text = sources[0]["text"][:400]
    stages.append({"name": "Generate", "ms": round((time.time() - t) * 1000)})
    return {"text": text.strip(), "mode": mode, "model": model, "sources": sources, "stages": stages,
            "total_ms": round((time.time() - t0) * 1000), "pipeline": "baseline", "checks": audit_baseline(text, sources)}


def audit_baseline(text: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Run OUR checks on the baseline's output, for display only (the baseline itself does none of this).

    * grounding: every number/entity in the answer against everything the baseline retrieved
    * instructions: retrieved passages that contain text aimed at an AI
    """
    from rag import qa, security

    pool = {i + 1: s["text"] for i, s in enumerate(sources)}
    checks = [verifier.check_sentence(sent, list(pool), pool) for sent, _ in qa.split_sentences(text)]
    g = verifier.grounding(checks) if checks else {"verified": 0, "total": 0}
    injected = [{"filename": s["filename"], "page": s["page"], "reason": f.reason, "snippet": f.detail}
                for s in sources for f in security.scan_text(s["text"])]
    return {"grounding": g, "unsupported": [c.reason for c in checks if not c.verified and c.reason],
            "retrieved_instructions": injected}
