"""Common document schema shared by all extractors.

Coordinate convention (bbox):
    [x1, y1, x2, y2] normalized to 0.0–1.0 relative to page dimensions.
    Origin: top-left of the page/slide/image.
    Formats that cannot provide meaningful coordinates use null.

Confidence policy:
    - Preserve real extractor/model confidence values.
    - Do NOT fabricate confidence for deterministic extraction (use null).
    - Low-confidence output should set requires_review=true in metadata.
    - Never hallucinate missing text.
"""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from models.errors import DocumentError

# [x1, y1, x2, y2] normalized 0.0–1.0, origin at top-left.
BBox = tuple[float, float, float, float]


class BlockType(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    FIGURE = "figure"
    CHART = "chart"
    EQUATION = "equation"
    HEADER = "header"
    FOOTER = "footer"
    UNKNOWN = "unknown"


class DocumentBlock(BaseModel):
    id: str
    type: BlockType
    content: str
    page: int = Field(ge=1)
    bbox: BBox | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    extractor: str
    reading_order: int | None = None
    requires_review: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentResponse(BaseModel):
    document_id: str
    filename: str
    file_type: str
    page_count: int
    processing_time_ms: int
    status: Literal["success", "partial"]
    blocks: list[DocumentBlock]
    markdown: str = ""
    errors: list[DocumentError] = Field(default_factory=list)
    # Optional preview info. A failed preview never fails the parse: it sets preview_error.
    preview_available: bool = False
    preview_pages: int = 0
    preview_error: str | None = None
    # AXTRACT Verify report (see verify.models.ValidationReport), as plain JSON. None when validation is
    # disabled. Validation is advisory: `status` above is the extraction's status, not a trust verdict.
    validation: dict[str, Any] | None = None
