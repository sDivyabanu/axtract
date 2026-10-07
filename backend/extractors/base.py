from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from models.document import DocumentBlock
from models.errors import DocumentError


@dataclass
class ExtractionResult:
    page_count: int
    blocks: list[DocumentBlock] = field(default_factory=list)
    errors: list[DocumentError] = field(default_factory=list)


class BaseExtractor(ABC):
    """Contract every extractor implements.

    To add a format or a new extraction method: subclass this, implement
    `extract`, and register an instance in `extractors/registry.py`.
    """

    name: ClassVar[str]
    # Lowercase extensions without the dot, e.g. {"pdf"}.
    supported_extensions: ClassVar[frozenset[str]]

    @abstractmethod
    def extract(self, file_path: Path) -> ExtractionResult:
        """Return blocks for the file. Raise AppError for fatal problems; record per-page problems in `errors`."""
