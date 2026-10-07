"""Ollama client with graceful degradation.

The LLM only plans (JSON) and phrases answers; it never decides facts or does arithmetic.
If Ollama is unreachable or the model is not pulled, callers fall back to extractive mode.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Iterator

import httpx

from rag import config

_stats = threading.local()  # per-thread stats of the last streamed completion (prompt tokens, time to first token, ...)
_status_lock = threading.Lock()
_status: dict[str, Any] = {"ts": 0.0, "available": False, "reason": "not checked"}


def status(force: bool = False) -> dict[str, Any]:
    """{'available': bool, 'model': str, 'reason': str}; cached for 5 s."""
    with _status_lock:
        if not force and time.time() - _status["ts"] < 5:
            return dict(_status)
    result = {"ts": time.time(), "available": False, "model": config.LLM_MODEL, "reason": ""}
    try:
        r = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=2.0)
        r.raise_for_status()
        names = {m["name"] for m in r.json().get("models", [])}
        base = config.LLM_MODEL.split(":")[0]
        if config.LLM_MODEL in names or any(n.split(":")[0] == base and ":" not in config.LLM_MODEL for n in names):
            result["available"] = True
        else:
            result["reason"] = f"model '{config.LLM_MODEL}' is not pulled (ollama pull {config.LLM_MODEL})"
    except Exception:  # noqa: BLE001
        result["reason"] = "Ollama is not running"
    with _status_lock:
        _status.update(result)
        return dict(_status)


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S)


def _no_think(messages: list[dict]) -> list[dict]:
    """Qwen3's soft switch for a direct answer (its reasoning, if any, lands in a separate
    `thinking` field that we never read). Harmless for models that do not know the switch."""
    if not config.LLM_MODEL.startswith("qwen3") or "instruct" in config.LLM_MODEL:
        return messages
    msgs = [dict(m) for m in messages]
    for m in reversed(msgs):
        if m["role"] == "user":
            m["content"] = m["content"] + " /no_think"
            break
    return msgs


def _keep_alive() -> Any:
    k = str(config.LLM_KEEP_ALIVE).strip()
    return int(k) if k.lstrip("-").isdigit() else k  # Ollama wants a number for -1 / seconds, a string like '30m' otherwise


def _payload(messages: list[dict], stream: bool, json_mode: bool, max_tokens: int) -> dict[str, Any]:
    p: dict[str, Any] = {
        "model": config.LLM_MODEL,
        "messages": _no_think(messages),
        "stream": stream,
        "options": {"temperature": 0, "num_ctx": config.LLM_NUM_CTX, "num_predict": max_tokens},
        "keep_alive": _keep_alive(),
    }
    if json_mode:
        p["format"] = "json"
    return p


def chat(messages: list[dict], *, json_mode: bool = False, max_tokens: int = 700) -> str:
    """One blocking completion. Raises RuntimeError when the LLM is unavailable."""
    if not status()["available"]:
        raise RuntimeError(status()["reason"] or "LLM unavailable")
    try:
        r = httpx.post(f"{config.OLLAMA_URL}/api/chat", json=_payload(messages, False, json_mode, max_tokens),
                       timeout=config.LLM_TIMEOUT_S)
        r.raise_for_status()
        return _THINK_BLOCK.sub("", (r.json().get("message") or {}).get("content", "")).strip()
    except httpx.HTTPError as exc:
        status(force=True)
        raise RuntimeError(f"LLM call failed: {type(exc).__name__}") from exc


def chat_json(messages: list[dict], max_tokens: int = 500) -> dict[str, Any] | None:
    """A JSON object from the model, or None if unavailable / not valid JSON (never raises)."""
    try:
        raw = chat(messages, json_mode=True, max_tokens=max_tokens)
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except (RuntimeError, ValueError):
        return None


def last_stats() -> dict[str, Any]:
    """Stats of the last stream() on this thread: prompt_tokens, completion_tokens, ttft_ms, load_ms (ids and numbers only)."""
    return dict(getattr(_stats, "v", {}))


def stream(messages: list[dict], *, max_tokens: int = 700) -> Iterator[str]:
    """Token stream. Raises RuntimeError when the LLM is unavailable."""
    if not status()["available"]:
        raise RuntimeError(status()["reason"] or "LLM unavailable")
    t0, first = time.time(), None
    _stats.v = {}
    try:
        with httpx.stream("POST", f"{config.OLLAMA_URL}/api/chat", json=_payload(messages, True, False, max_tokens),
                          timeout=config.LLM_TIMEOUT_S) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                obj = json.loads(line)
                tok = (obj.get("message") or {}).get("content", "")
                if tok:
                    if first is None:
                        first = time.time()
                    yield tok
                if obj.get("done"):
                    _stats.v = {"prompt_tokens": obj.get("prompt_eval_count"), "completion_tokens": obj.get("eval_count"),
                                "load_ms": round((obj.get("load_duration") or 0) / 1e6),
                                "prompt_eval_ms": round((obj.get("prompt_eval_duration") or 0) / 1e6),
                                "gen_ms": round((obj.get("eval_duration") or 0) / 1e6),
                                "ttft_ms": round(((first or time.time()) - t0) * 1000)}
                    break
    except httpx.HTTPError as exc:
        status(force=True)
        raise RuntimeError(f"LLM stream failed: {type(exc).__name__}") from exc
