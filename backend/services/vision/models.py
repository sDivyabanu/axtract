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


def _get_latex():
    global _latex
    with _lock:
        if _latex is None:
            if not latex_model_available():
                raise ModelUnavailable(
                    "Formula model weights not found. Run: backend/.venv/bin/python scripts/fetch_models.py"
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
            _latex = _NumpyTwoLaTeXOCR(
                image_resizer_path=d / "image_resizer.onnx",
                encoder_path=d / "encoder.onnx",
                decoder_path=d / "decoder.onnx",
                tokenizer_json=d / "tokenizer.json",
            )
        return _latex


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
