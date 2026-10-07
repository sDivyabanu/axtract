from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from models.errors import DocumentError

# [x1, y1, x2, y2] in PDF points, origin at top-left of the page.
BBox = tuple[float, float, float, float]


class BlockType(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    FIGURE = "figure"
    CHART = "chart"
    EQUATION = "equation"
    UNKNOWN = "unknown"


class DocumentBlock(BaseModel):
    id: str
    type: BlockType
    content: str
    page: int = Field(ge=1)
    bbox: BBox | None = None
    # Left null when the extractor does not provide a real confidence value.
    confidence: float | None = Field(default=None, ge=0, le=1)
    extractor: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentResponse(BaseModel):
    document_id: str
    filename: str
    file_type: str
    page_count: int
    processing_time_ms: int
    status: Literal["success", "partial"]
    blocks: list[DocumentBlock]
    errors: list[DocumentError] = Field(default_factory=list)
