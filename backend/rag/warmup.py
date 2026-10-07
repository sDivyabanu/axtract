"""Warm everything the first question would otherwise pay for, in the background at server start.

* the local LLM: load the weights and prime the prompt with the real system prompt (keep_alive keeps them resident)
* the embedding model and the cross-encoder (ONNX sessions)
* the retrieval index of the most recently used data rooms (BM25 + vector matrix)
Nothing here touches document text beyond building the same in-memory index a question would build.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from rag import config, db, embed, index, llm

state: dict[str, Any] = {"status": "idle", "steps": {}}


def _step(name: str, fn) -> None:
    t = time.time()
    try:
        fn()
        state["steps"][name] = {"ok": True, "ms": round((time.time() - t) * 1000)}
    except Exception as exc:  # noqa: BLE001 - warm-up must never stop the server
        state["steps"][name] = {"ok": False, "error": type(exc).__name__}


def run() -> dict[str, Any]:
    from rag import qa

    state.update(status="warming", steps={})
    t0 = time.time()
    _step("embedding", lambda: embed.embed_query("warm up"))
    _step("reranker", lambda: embed.rerank("warm up", ["warm up"]))

    def indexes() -> None:
        with db.connect() as c:
            rows = c.execute("SELECT id FROM workspaces ORDER BY created_at DESC LIMIT 12").fetchall()
        for r in rows:
            index.get_index(r["id"])

    _step("indexes", indexes)

    def model() -> None:
        if llm.status(force=True)["available"]:
            llm.chat([{"role": "system", "content": qa._SYSTEM}, {"role": "user", "content": "<sources>\n<source id=\"1\">warm up</source>\n</sources>\nQuestion: ok"}], max_tokens=1)

    _step("llm", model)
    state.update(status="ready", ms=round((time.time() - t0) * 1000))
    return state


def start() -> None:
    if config.WARMUP:
        threading.Thread(target=run, name="deallens-warmup", daemon=True).start()
