"""Cooperative wall-clock deadline for one parse job (hard limit: 60 s per file).

parse_upload() calls start(); long loops (pages, slides, model calls) call check().
Worker threads cannot be killed, so each loop must poll.
"""

from __future__ import annotations

import time
from contextvars import ContextVar

from models.errors import AppError

import os

HARD_LIMIT_SECONDS = 60
# Leave headroom so the structured TIMEOUT error itself is returned inside the limit.
_BUDGET_SECONDS = 55
# The opt-in VLM OCR engine (services/vlm_engine.py) needs tens of seconds per page on a laptop, so a deployment that enables
# it sets its own per-file limit; the 60 s contract applies whenever the engine is off.
if os.environ.get("AXTRACT_VLM_OCR", "off").strip().lower() in ("scanned", "all"):
    HARD_LIMIT_SECONDS = max(60, int(os.environ.get("AXTRACT_VLM_DOC_SECONDS", "1800")))
    _BUDGET_SECONDS = HARD_LIMIT_SECONDS - 5

_deadline: ContextVar[float | None] = ContextVar("parse_deadline", default=None)


def start(seconds: float = _BUDGET_SECONDS) -> None:
    _deadline.set(time.monotonic() + seconds)


def remaining() -> float:
    d = _deadline.get()
    return float("inf") if d is None else d - time.monotonic()


def check() -> None:
    if remaining() <= 0:
        raise AppError(
            "TIMEOUT",
            f"Processing exceeded the {HARD_LIMIT_SECONDS} second limit.",
            status_code=504,
        )
