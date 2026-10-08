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


_pdf = _PDFRouterWrapper()
_ocr = OCRExtractor()

# One explicit mapping: extension -> extractor. PDFs go through the adaptive router so
# scanned pages get OCR; images use OCR directly.
_BY_EXTENSION: dict[str, Extractable] = {
    "pdf": _pdf,
    "docx": DocxExtractor(),
    "pptx": PptxExtractor(),
    "xlsx": XlsxExtractor(),
    "jpg": _ocr,
    "jpeg": _ocr,
    "png": _ocr,
}


def get_extractor(extension: str) -> Extractable | None:
    """Return the extractor for a lowercase extension without the dot, or None if unsupported."""
    extractor = _BY_EXTENSION.get(extension)
    return type(extractor)() if isinstance(extractor, (DocxExtractor, PptxExtractor, XlsxExtractor)) else extractor
