"""Pix2Text-MFR (TrOCR-style formula recognizer, ONNX) on plain onnxruntime. No torch, no transformers.

Weights live in ``model_weights/pix2text_mfr`` (118 MB: encoder_model.onnx, decoder_model.onnx,
tokenizer.json, ...). The tokenizer comes from the ``tokenizers`` package, which is already unpacked
next to the other formula runtime (see ``models.formula_runtime_status``). Decoding is greedy and stops
on the end token or on degenerate repetition, so latency is bounded.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from services.vision import models

logger = logging.getLogger(__name__)

MFR_DIR = Path(os.environ.get("AXTRACT_MFR_DIR", models.WEIGHTS_DIR / "pix2text_mfr"))
MFR_MAX_TOKENS = int(os.environ.get("AXTRACT_MFR_MAX_TOKENS", "200"))
_FILES = ("encoder_model.onnx", "decoder_model.onnx", "tokenizer.json")
_START = _EOS = 2

_lock = threading.Lock()
_state: dict = {}


def mfr_status() -> dict:
    """What the recognizer needs and whether each piece is present (no model load)."""
    import importlib.util

    models.formula_runtime_status()  # makes the isolated site folder importable
    missing = [f for f in _FILES if not (MFR_DIR / f).exists()]
    tokenizers_ok = importlib.util.find_spec("tokenizers") is not None
    return {"weights_missing": missing, "tokenizers_available": tokenizers_ok,
            "available": not missing and tokenizers_ok}


def mfr_available() -> bool:
    return mfr_status()["available"]


def _load():
    with _lock:
        if not _state:
            if not mfr_available():
                raise models.ModelUnavailable(f"Pix2Text-MFR not available: {mfr_status()}")
            import onnxruntime as ort
            from tokenizers import Tokenizer

            opts = ort.SessionOptions()
            opts.intra_op_num_threads = max(1, min(4, int(os.environ.get("AXTRACT_ORT_THREADS", "2"))))
            opts.inter_op_num_threads = 1
            opts.enable_cpu_mem_arena = False
            opts.enable_mem_pattern = False
            opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
            loaded = {}
            loaded["enc"] = ort.InferenceSession(str(MFR_DIR / "encoder_model.onnx"), opts,
                                                 providers=["CPUExecutionProvider"])
            loaded["dec"] = ort.InferenceSession(str(MFR_DIR / "decoder_model.onnx"), opts,
                                                 providers=["CPUExecutionProvider"])
            loaded["tok"] = Tokenizer.from_file(str(MFR_DIR / "tokenizer.json"))
            _state.update(loaded)  # publish only a complete, usable session pair
        return _state


def _prep(img: Image.Image) -> np.ndarray:
    # TrOCR processor: squash to 384x384 (bicubic), scale to [-1, 1]. Padding to a square was measured worse.
    a = np.asarray(img.convert("RGB").resize((384, 384), Image.BICUBIC), dtype=np.float32) / 255.0
    return ((a - 0.5) / 0.5).transpose(2, 0, 1)[None]


def _looping(ids: list[int]) -> bool:
    if len(ids) < 24:
        return False
    tail = ids[-24:]
    return any(all(tail[i] == tail[i - p] for i in range(p, 24)) for p in range(1, 9))


@dataclass
class Prediction:
    latex: str
    completed: bool
    stop_reason: str
    tokens: int
    seconds: float


def recognize(img: Image.Image, *, max_seconds: float = 5.0, max_tokens: int | None = None) -> Prediction:
    """Bounded greedy recognition; incomplete output is not a verified equation."""
    start = time.monotonic()
    if max_seconds<=0:
        return Prediction("",False,"budget",0,0.0)
    st = _load()
    ids = [_START]
    reason = "token_limit"
    limit = max(1, min(512, max_tokens if max_tokens is not None else MFR_MAX_TOKENS))
    # Lock waiting counts toward this call's budget; never start late work.
    remaining = max_seconds-(time.monotonic()-start)
    if remaining<=0 or not models._infer_lock.acquire(timeout=remaining):
        return Prediction("",False,"budget",0,time.monotonic()-start)
    try:
        if time.monotonic()-start>=max_seconds:
            return Prediction("",False,"budget",0,time.monotonic()-start)
        ctx = st["enc"].run(None, {"pixel_values": _prep(img)})[0]
        for _ in range(limit):
            if time.monotonic()-start>=max_seconds:
                reason="budget";break
            logits = st["dec"].run(None, {"input_ids": np.array([ids], dtype=np.int64),
                                          "encoder_hidden_states": ctx})[0]
            nxt = int(np.argmax(logits[0, -1]))
            if nxt == _EOS:
                reason="eos";break
            ids.append(nxt)
            if _looping(ids[1:]):
                reason="repetition";break
    finally:
        models._infer_lock.release()
    return Prediction(st["tok"].decode(ids[1:]).strip(), reason=="eos",reason,
                      len(ids)-1,time.monotonic()-start)


def image_to_latex(img: Image.Image) -> str:
    """Compatibility entry point: never present truncated predictions as complete."""
    result=recognize(img)
    return result.latex if result.completed else ""
