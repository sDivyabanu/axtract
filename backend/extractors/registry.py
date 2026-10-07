from extractors.base import BaseExtractor
from extractors.pymupdf_extractor import PyMuPDFExtractor

# Register new extractors here. Each one declares the extensions it handles.
_EXTRACTORS: list[BaseExtractor] = [
    PyMuPDFExtractor(),
]

_BY_EXTENSION: dict[str, BaseExtractor] = {
    ext: extractor for extractor in _EXTRACTORS for ext in extractor.supported_extensions
}


def get_extractor(extension: str) -> BaseExtractor | None:
    """Return the extractor for a lowercase extension without the dot, or None if unsupported."""
    return _BY_EXTENSION.get(extension)
