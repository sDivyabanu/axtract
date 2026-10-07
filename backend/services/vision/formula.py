"""Formula region -> validated LaTeX with an honest confidence."""

from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image

from services.vision import models
from services.vision.latex import crosscheck, looks_mathy, validate_latex


@dataclass
class FormulaResult:
    latex: str
    confidence: float
    flags: list[str] = field(default_factory=list)
    ocr_text: str = ""
    valid: bool = False
    mathy: bool = False
    agree: float = 0.0  # overlap with an independent OCR read of the same pixels

    @property
    def looks_like_prose(self) -> bool:
        """The OCR read contains ordinary words: this is text, not a formula."""
        import re

        words = re.findall(r"[A-Za-z]{4,}", self.ocr_text)
        return len(words) >= 2

    @property
    def requires_review(self) -> bool:
        return self.confidence < 0.7 or bool(self.flags)


def _ocr_text(img: Image.Image) -> str:
    import numpy as np

    from extractors.ocr_extractor import _get_ocr

    w, h = img.size
    pad = Image.new("RGB", (w + 40, h + 40), "white")
    pad.paste(img.convert("RGB"), (20, 20))
    res, _ = _get_ocr()(np.array(pad))
    return " ".join(str(t) for _, t, _c in (res or []))


def recognize_formula(img: Image.Image) -> FormulaResult:
    """Run the local formula model, validate, and cross-check against an independent OCR read.

    Confidence is never higher than the evidence: the model itself reports none, so it is
    derived from (a) strict LaTeX validation and (b) agreement with the OCR text.
    """
    try:
        latex = models.image_to_latex(img)
    except models.ModelUnavailable:
        return FormulaResult("", 0.0, ["formula_model_unavailable"])
    except Exception as exc:  # noqa: BLE001 - one bad crop must not fail the document
        return FormulaResult("", 0.0, [f"formula_model_error:{type(exc).__name__}"])

    v = validate_latex(latex)
    mathy = looks_mathy(latex)
    try:
        text = _ocr_text(img)
    except Exception:  # noqa: BLE001
        text = ""
    if not v.ok:
        return FormulaResult(latex, 0.25, ["latex_parse_failed"], text, valid=False, mathy=mathy)

    agree = crosscheck(latex, text) if text else 0.0
    if agree >= 0.75:
        return FormulaResult(latex, 0.75, [], text, True, mathy, agree)
    if agree >= 0.45:
        return FormulaResult(latex, 0.6, ["ocr_crosscheck_weak"], text, True, mathy, agree)
    return FormulaResult(latex, 0.45, ["unverified_by_ocr"], text, True, mathy, agree)
