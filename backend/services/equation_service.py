"""Equation blocks: one place that turns recognised math into a DocumentBlock.

LaTeX comes from one of:
  omml       Word/PowerPoint equation objects (exact conversion, no model)
  text_layer deterministic conversion of unambiguous text such as "x^2 + y^2 = r^2"
  pix2tex_onnx  local formula model on a rendered crop (validated + OCR cross-checked)
Every LaTeX string is validated; anything that fails is kept but flagged and low-confidence.
Nothing is ever guessed or filled in.
"""

from __future__ import annotations

from typing import Any

from models.document import BBox, BlockType, DocumentBlock
from services.vision.latex import validate_latex


def make_equation_block(
    block_id: str,
    page: int,
    bbox: BBox | None,
    latex: str,
    method: str,
    confidence: float | None,
    flags: list[str] | None = None,
    extractor: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> DocumentBlock:
    flags = sorted(set(flags or []))
    validation = validate_latex(latex)
    if not validation.ok and "latex_parse_failed" not in flags:
        flags.append("latex_parse_failed")
        confidence = min(confidence if confidence is not None else 0.25, 0.25)
    review = bool(flags) or (confidence is not None and confidence < 0.7)

    metadata: dict[str, Any] = {
        "latex": latex,
        "latex_method": method,
        "latex_confidence": confidence,
        "latex_validated": validation.ok,
        "flags": flags,
        "needs_review": review,
    }
    if validation.errors:
        metadata["latex_errors"] = validation.errors
    metadata.update(extra_metadata or {})

    return DocumentBlock(
        id=block_id,
        type=BlockType.EQUATION,
        content=latex,
        page=page,
        bbox=bbox,
        confidence=confidence,
        extractor=extractor or method,
        requires_review=review,
        metadata=metadata,
    )
