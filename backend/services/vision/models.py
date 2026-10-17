"""Lazy, thread-safe loaders for the local ONNX models. No network access at runtime."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

WEIGHTS_DIR = Path(os.environ.get("AXTRACT_MODEL_DIR", Path(__file__).resolve().parents[2] / "model_weights"))
_LATEX_FILES = ("image_resizer.onnx", "encoder.onnx", "decoder.onnx", "tokenizer.json")

_lock = threading.Lock()
_layout = None
_latex = None
_infer_lock = threading.Lock()  # one model call at a time: keeps memory and CPU bounded


class ModelUnavailable(RuntimeError):
    """A required local model file is missing (run scripts/fetch_models.py)."""


@dataclass
class Region:
    cls: str
    score: float
    bbox: tuple[float, float, float, float]  # normalized 0-1, top-left origin


def latex_model_available() -> bool:
    return all((WEIGHTS_DIR / "latex_ocr" / f).exists() for f in _LATEX_FILES)


def _get_layout():
    global _layout
    with _lock:
        if _layout is None:
            logging.getLogger("rapid_layout").setLevel(logging.WARNING)
            from rapid_layout import RapidLayout

            _layout = RapidLayout()  # bundled CDLA ONNX model (no download)
        return _layout


def detect_layout(img: Image.Image, min_score: float = 0.35) -> list[Region]:
    """Layout regions (text, title, figure, table, equation, ...) of a page image."""
    arr = np.array(img.convert("RGB"))
    h, w = arr.shape[:2]
    with _infer_lock:
        res = _get_layout()(arr)
    boxes = getattr(res, "boxes", None)
    if boxes is None:
        return []
    out = []
    for box, name, score in zip(res.boxes, res.class_names, res.scores):
        if float(score) < min_score:
            continue
        x0, y0, x1, y1 = [float(v) for v in box]
        out.append(Region(str(name), float(score), (max(0, x0 / w), max(0, y0 / h), min(1, x1 / w), min(1, y1 / h))))
    return out


def _formula_runtime_site() -> Path:
    """rapid-latex-ocr declares Python<3.13 although it is pure Python, so it is unpacked into an
    isolated folder next to the weights instead of the backend venv (see scripts/fetch_models.py)."""
    return Path(os.environ.get("AXTRACT_FORMULA_SITE", WEIGHTS_DIR / "latex_ocr" / "site"))


def formula_runtime_status() -> dict:
    """What the formula recognizer needs and whether each piece is present (no model load)."""
    import importlib.util
    import sys

    site = _formula_runtime_site()
    if site.is_dir() and str(site) not in sys.path:
        sys.path.append(str(site))  # appended: never shadows packages already in the venv
    missing = [f for f in _LATEX_FILES if not (WEIGHTS_DIR / "latex_ocr" / f).exists()]
    package = importlib.util.find_spec("rapid_latex_ocr") is not None
    return {"weights_missing": missing, "package_available": package,
            "available": not missing and package}


def latex_runtime_available() -> bool:
    return formula_runtime_status()["available"]


def _get_latex():
    global _latex
    with _lock:
        if _latex is None:
            if not latex_model_available():
                raise ModelUnavailable(
                    "Formula model weights not found. Run: backend/.venv/bin/python scripts/fetch_models.py"
                )
            if not formula_runtime_status()["package_available"]:
                raise ModelUnavailable(
                    "rapid-latex-ocr is not installed. Run: backend/.venv/bin/python scripts/fetch_models.py"
                )
            from rapid_latex_ocr import LaTeXOCR

            class _NumpyTwoLaTeXOCR(LaTeXOCR):
                """rapid-latex-ocr pins numpy<2 and calls int() on a 1-element array, which
                NumPy 2.x rejects. Same algorithm, safe scalar conversion."""

                def loop_image_resizer(self, img):
                    pillow_img = Image.fromarray(img)
                    pad_img = self.pre_pro.pad(pillow_img)
                    input_image = self.pre_pro.minmax_size(pad_img).convert("RGB")
                    r, w, h = 1, input_image.size[0], input_image.size[1]
                    final_img = None
                    for _ in range(10):
                        h = int(h * r)
                        final_img, pad_img = self.pre_process(input_image, r, w, h)
                        resizer_res = self.image_resizer([final_img.astype(np.float32)])[0]
                        argmax_idx = int(np.asarray(np.argmax(resizer_res, axis=-1)).reshape(-1)[0])
                        w = (argmax_idx + 1) * 32
                        if w == pad_img.size[0]:
                            break
                        r = w / pad_img.size[0]
                    return final_img

            d = WEIGHTS_DIR / "latex_ocr"
            model = _NumpyTwoLaTeXOCR(
                image_resizer_path=d / "image_resizer.onnx",
                encoder_path=d / "encoder.onnx",
                decoder_path=d / "decoder.onnx",
                tokenizer_json=d / "tokenizer.json",
            )
            _use_greedy_decoder(model)
            _latex = model
        return _latex


FORMULA_MAX_TOKENS = int(os.environ.get("AXTRACT_FORMULA_MAX_TOKENS", "160"))


def _use_greedy_decoder(model) -> None:
    """The stock decoder samples at temperature 0.25 (non-deterministic), reruns the whole
    sequence for every token and only stops at the end token or 256 steps. Greedy decoding is
    deterministic and typically at least as accurate; it also stops on degenerate repetition
    (a loop of one short pattern), which is the runaway case that costs most of the latency."""
    from rapid_latex_ocr.models import Decoder

    class _GreedyDecoder(Decoder):
        def __call__(self, start_tokens, seq_len=256, eos_token=None, temperature=1.0,
                     filter_thres=0.9, context=None):
            out = start_tokens
            mask = np.full_like(start_tokens, True, dtype=bool)
            t0 = start_tokens.shape[1]
            for _ in range(min(seq_len, FORMULA_MAX_TOKENS)):
                x = out[:, -self.max_seq_len:]
                mask = mask[:, -self.max_seq_len:]
                logits = self.session([x.astype(np.int64), mask, context])[0][:, -1, :]
                nxt = np.argmax(logits, axis=-1)[:, None]
                out = np.concatenate([out, nxt], axis=-1)
                mask = np.pad(mask, [(0, 0), (0, 1)], "constant", constant_values=True)
                if eos_token is not None and int(nxt[0, 0]) == eos_token:
                    break
                if _is_looping(out[0, t0:]):
                    break
            return out[:, t0:]

    model.encoder_decoder.decoder.__class__ = _GreedyDecoder


def _is_looping(tokens) -> bool:
    """True when the tail repeats one short pattern many times (period 1-8, >= 12 tokens)."""
    n = len(tokens)
    if n < 24:
        return False
    tail = [int(t) for t in tokens[-24:]]
    for period in range(1, 9):
        if all(tail[i] == tail[i - period] for i in range(period, 24)):
            return True
    return False


def image_to_latex(img: Image.Image) -> str:
    """Raw LaTeX guess for a cropped formula image (not validated)."""
    img = img.convert("RGB")
    pad = max(8, img.height // 8)
    canvas = Image.new("RGB", (img.width + 2 * pad, img.height + 2 * pad), "white")
    canvas.paste(img, (pad, pad))
    model = _get_latex()
    with _infer_lock:
        out = model(np.array(canvas))
    latex = out[0] if isinstance(out, tuple) else out
    return str(latex or "").strip()
