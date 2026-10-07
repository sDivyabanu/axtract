"""Extractor registry: maps file extensions to extraction handlers.

Register new extractors here. Each one declares the extensions it handles.
The parse service calls get_extractor() with the file extension to find the
appropriate handler.

PDF files use the PDFRouter (adaptive routing) instead of the raw PyMuPDF
extractor, so they get OCR support for scanned pages automatically.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from pathlib import Path

from extractors.base import BaseExtractor, ExtractionResult
from extractors.docx_extractor import DocxExtractor
from extractors.ocr_extractor import OCRExtractor
from extractors.pptx_extractor import PptxExtractor
from extractors.pymupdf_extractor import PyMuPDFExtractor
from extractors.xlsx_extractor import XlsxExtractor


@runtime_checkable
class Extractable(Protocol):
    """Anything that has an extract(file_path) -> ExtractionResult method."""

    name: str

    def extract(self, file_path: Path) -> ExtractionResult: ...


class _PDFRouterWrapper:
    """Lazy wrapper so the PDFRouter is imported only when needed."""

    name = "pdf-router"

    def extract(self, file_path: Path) -> ExtractionResult:
        from services.pdf_router import PDFRouter
        return PDFRouter().extract(file_path)


# Registered extractors (order doesn't matter; lookup is by extension).
_EXTRACTORS: list[Extractable] = [
    _PDFRouterWrapper(),  # PDF → adaptive routing (digital + OCR)
    DocxExtractor(),
    PptxExtractor(),
    XlsxExtractor(),
    OCRExtractor(),  # standalone images (jpg/jpeg/png)
]

_BY_EXTENSION: dict[str, Extractable] = {
    ext: extractor
    for extractor in _EXTRACTORS
    for ext in (
        getattr(extractor, "supported_extensions", frozenset())
        if isinstance(extractor, BaseExtractor)
        else frozenset()
    )
}

# Register PDF router for "pdf" extension
_BY_EXTENSION["pdf"] = _EXTRACTORS[0]  # _PDFRouterWrapper

# Register image extensions for OCR
_ocr = next(e for e in _EXTRACTORS if isinstance(e, OCRExtractor))
for ext in ("jpg", "jpeg", "png"):
    _BY_EXTENSION[ext] = _ocr

# Register office formats
_BY_EXTENSION["docx"] = next(e for e in _EXTRACTORS if isinstance(e, DocxExtractor))
_BY_EXTENSION["pptx"] = next(e for e in _EXTRACTORS if isinstance(e, PptxExtractor))
_BY_EXTENSION["xlsx"] = next(e for e in _EXTRACTORS if isinstance(e, XlsxExtractor))


def get_extractor(extension: str) -> Extractable | None:
    """Return the extractor for a lowercase extension without the dot, or None if unsupported."""
    return _BY_EXTENSION.get(extension)
