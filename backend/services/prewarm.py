"""Load the document pipeline's heavy parts in the background at server start.

The first document after a restart otherwise pays for importing the pipeline (~1.2 s), building the OCR and
layout sessions (~1 s) and the first inference on each (another 1-3 s). Doing that on a daemon thread while the
server is already accepting requests moves the cost off the first upload. It never blocks startup, never raises,
and a request that arrives mid-warm-up simply waits on the same one-time initialisation it would have run itself.

Set AXTRACT_PREWARM=0 to skip it (e.g. to keep an idle backend as small as possible).
"""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

state: dict = {"status": "idle", "steps": {}}


def enabled() -> bool:
    return os.environ.get("AXTRACT_PREWARM", "1").strip().lower() not in ("0", "false", "off", "no")


def _sample_image():
    """A small page-like image with real glyphs, so detection and recognition both execute."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 220), "white")
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(("Warm-up sample 2024", "Revenue 1,234.5 Total 99%")):
        draw.text((24, 30 + 60 * i), line, fill="black")
    return img


def _step(name: str, fn) -> None:
    t0 = time.perf_counter()
    try:
        fn()
        state["steps"][name] = {"ok": True, "ms": round((time.perf_counter() - t0) * 1000)}
    except Exception as exc:  # noqa: BLE001 - warm-up must never affect the server
        state["steps"][name] = {"ok": False, "error": type(exc).__name__}
        logger.warning("prewarm step %s failed: %s", name, type(exc).__name__)


def run() -> dict:
    import numpy as np

    state.update(status="warming", steps={})
    t0 = time.perf_counter()
    _step("pipeline_imports", lambda: __import__("services.parse_service"))
    img = _sample_image()

    def ocr() -> None:
        from extractors.ocr_extractor import run_ocr

        run_ocr(np.array(img))

    def layout() -> None:
        from services.vision import models

        models.detect_layout(img)

    _step("ocr", ocr)
    _step("layout", layout)
    state.update(status="ready", ms=round((time.perf_counter() - t0) * 1000))
    return state


def start() -> None:
    if enabled():
        threading.Thread(target=run, name="axtract-prewarm", daemon=True).start()
