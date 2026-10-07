#!/usr/bin/env python3
"""One-time download of the formula-recognition weights (dev/build time only).

The server itself never downloads anything: it loads these files from
backend/model_weights/latex_ocr/ and reports `formula_model_unavailable` if they are missing.

Model: RapidLaTeXOCR (ONNX export of pix2tex / LaTeX-OCR). Code: Apache-2.0 (rapid-latex-ocr);
original pix2tex project: MIT. Weights come from the RapidAI release below; verify the weight
licence for your commercial use before shipping.

Usage: backend/.venv/bin/python scripts/fetch_models.py
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

BASE = "https://github.com/RapidAI/RapidLaTeXOCR/releases/download/v0.0.0"
DEST = Path(__file__).resolve().parent.parent / "backend" / "model_weights" / "latex_ocr"
FILES = {
    "image_resizer.onnx": "e0b075c39700f64d50400f39c8fc186bbb3b5d84d31864008313f376603aca9d",
    "encoder.onnx": "01bf5dc25539ca0cd5b1bd29296ea495977a6ba5f629dc4178277809d26e5e7d",
    "decoder.onnx": "bd695497bf1b22279b7626f5916c79226e1e244c84355f8da7edfd2d921d0072",
    "tokenizer.json": "1dc27b18d6a518d0d5ff3f4bb7bd98521fe80ad39e5b2a246d4109f1bb9d5019",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    for name, digest in FILES.items():
        target = DEST / name
        if target.exists() and sha256(target) == digest:
            print(f"ok       {name}")
            continue
        print(f"fetching {name} ...")
        urllib.request.urlretrieve(f"{BASE}/{name}", target)
        if sha256(target) != digest:
            target.unlink(missing_ok=True)
            print(f"CHECKSUM MISMATCH for {name}; file removed", file=sys.stderr)
            return 1
    print("all model files present and verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
