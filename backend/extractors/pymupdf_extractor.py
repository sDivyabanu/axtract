from pathlib import Path
from typing import ClassVar

import pymupdf

from extractors.base import BaseExtractor, ExtractionResult
from models.document import BlockType, DocumentBlock
from models.errors import AppError, DocumentError

# PyMuPDF block type codes returned by page.get_text("blocks").
TEXT_BLOCK = 0


class PyMuPDFExtractor(BaseExtractor):
    """Extracts digital (text-layer) content from PDFs. Does not perform OCR."""

    name: ClassVar[str] = "pymupdf"
    supported_extensions: ClassVar[frozenset[str]] = frozenset({"pdf"})

    def extract(self, file_path: Path) -> ExtractionResult:
        # Open from bytes: on Windows a failed path-based open keeps the file locked,
        # which would stop the service from deleting the temporary upload.
        data = file_path.read_bytes()
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise AppError("INVALID_FILE", "The PDF could not be opened.", status_code=422) from exc

        with doc:
            if doc.needs_pass:
                raise AppError(
                    "ENCRYPTED_FILE",
                    "Password-protected PDFs are not supported yet.",
                    status_code=422,
                )

            result = ExtractionResult(page_count=doc.page_count)
            for page_number, page in enumerate(doc, start=1):
                try:
                    result.blocks.extend(self._extract_page(page, page_number))
                except Exception as exc:
                    result.errors.append(
                        DocumentError(
                            code="PAGE_EXTRACTION_FAILED",
                            message=str(exc) or "Unknown error while reading the page.",
                            page=page_number,
                        )
                    )
            return result

    def _extract_page(self, page: pymupdf.Page, page_number: int) -> list[DocumentBlock]:
        blocks: list[DocumentBlock] = []
        # Each tuple: (x0, y0, x1, y1, text, block_no, block_type)
        for x0, y0, x1, y1, text, block_no, block_type in page.get_text("blocks"):
            if block_type != TEXT_BLOCK:
                continue
            content = text.strip()
            if not content:
                continue

            blocks.append(
                DocumentBlock(
                    id=f"p{page_number}-b{block_no}",
                    type=BlockType.PARAGRAPH,
                    content=content,
                    page=page_number,
                    bbox=(x0, y0, x1, y1),
                    confidence=None,
                    extractor=self.name,
                    metadata={
                        "pymupdf_block_no": block_no,
                        "page_width": page.rect.width,
                        "page_height": page.rect.height,
                    },
                )
            )
        return blocks
