"""Chart blocks: one place that turns a chart reading into a DocumentBlock.

The readers live in services/vision/:
  chart_native.py      Office chart XML (exact cached values)
  chart_pdf_vector.py  vector charts in PDFs (exact geometry)
  chart_raster.py      charts in pictures (OpenCV + OCR, values_estimated unless printed)
This module never produces data on its own: no placeholders, no guessed values.
"""

from __future__ import annotations

from typing import Any

from models.document import BBox, BlockType, DocumentBlock
from services.vision.chart_native import chart_summary

_METHOD_EXTRACTOR = {
    "office_chart_xml": "office_chart_xml",
    "pdf_vector_chart": "pdf_vector_chart",
    "raster_chart_cv": "raster_chart_cv",
}


def has_numeric_values(data: dict[str, Any]) -> bool:
    series = data.get("series") or []
    return any(
        isinstance(v, (int, float)) and not isinstance(v, bool)
        for s in series
        for v in s.get("values", [])
    )


def make_chart_block(
    block_id: str,
    page: int,
    bbox: BBox | None,
    data: dict[str, Any],
    confidence: float | None,
    flags: list[str] | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> DocumentBlock:
    """Build a CHART block. Low confidence or any flag -> requires_review."""
    flags = sorted(set(flags or []))
    method = data.get("extraction_method", "unknown")
    missing = any(v is None for s in data.get("series", []) for v in s.get("values", []))
    if missing and "some_values_unreadable" not in flags:
        flags.append("some_values_unreadable")
    review = bool(flags) or (confidence is not None and confidence < 0.7) or not has_numeric_values(data)

    metadata: dict[str, Any] = {
        "chart_type": data.get("chart_type"),
        "chart_title": data.get("title", ""),
        "chart_data": data,
        "data_extracted": has_numeric_values(data),
        "values_estimated": bool(data.get("values_estimated", False)),
        "flags": flags,
        "needs_review": review,
    }
    if method == "office_chart_xml":
        metadata["confidence_source"] = "deterministic_cached_values"
    metadata.update(extra_metadata or {})

    return DocumentBlock(
        id=block_id,
        type=BlockType.CHART,
        content=chart_summary(data) if has_numeric_values(data) else (data.get("title") or "Chart (values not readable)"),
        page=page,
        bbox=bbox,
        confidence=confidence,
        extractor=_METHOD_EXTRACTOR.get(method, method),
        requires_review=review,
        metadata=metadata,
    )
