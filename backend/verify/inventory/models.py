"""Generic source-inventory model.

A SourceInventory describes what exists in the ORIGINAL file, as observed by a reader that is
independent of the AXTRACT extractor wherever that is reasonably possible. It makes no claim
about AXTRACT output; comparison happens in `verify.completeness`.

The model never forces a format into fake geometry:
  PDF  -> page + bbox            XLSX -> sheet + cell range
  PPTX -> slide + object (+bbox) DOCX -> logical position in the document body
  IMAGE -> the image (+region if an independent reader exists)

Every object and every signal records HOW it was observed (EvidenceKind) and by WHICH engine.
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from models.document import BBox
from verify.models import EvidenceKind, UnitRef, bbox_problem


class SourceObjectType(StrEnum):
    TEXT = "text"
    HEADING = "heading"
    LIST_ITEM = "list_item"
    TABLE = "table"
    CHART = "chart"
    PICTURE = "picture"
    EQUATION = "equation"
    CELL_REGION = "cell_region"  # the populated cells of a spreadsheet sheet
    MERGED_RANGE = "merged_range"
    FORMULA = "formula"
    GROUP = "group"
    OTHER = "other"


class Contract(StrEnum):
    """Does AXTRACT's extraction contract represent this kind of object?"""

    EXPECTED = "expected"  # losing it is a loss of content
    NOT_IN_CONTRACT = "not_in_contract"  # intentionally not represented; never reported as missing


class SourceLocator(BaseModel):
    part: str | None = None  # package part, e.g. "xl/worksheets/sheet1.xml"
    position: int | None = None  # 1-based logical order within the unit
    path: str | None = None  # e.g. "body[12]"
    cell_range: str | None = None  # e.g. "A1:E7"
    page: int | None = Field(default=None, ge=1)
    bbox: BBox | None = None  # normalised 0..1, top-left origin; only when truly observed
    shape_name: str | None = None
    approximate: bool = False

    @field_validator("bbox")
    @classmethod
    def _valid_bbox(cls, v: BBox | None) -> BBox | None:
        problem = bbox_problem(v)
        if problem:
            raise ValueError(problem)
        return v


class SourceObject(BaseModel):
    id: str
    type: SourceObjectType
    unit: UnitRef
    locator: SourceLocator = Field(default_factory=SourceLocator)
    text: str | None = None  # content, when independently observable
    kind: EvidenceKind
    engine: str
    contract: Contract = Contract.EXPECTED
    contract_note: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceUnit(BaseModel):
    unit: UnitRef
    objects: list[SourceObject] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    # False when the only reader available for this unit's text is the engine AXTRACT itself used.
    independent_text_available: bool = True
    independence_note: str = ""


class InventorySignal(BaseModel):
    """One kind of observation a collector makes, with how it is made."""

    name: str
    kind: EvidenceKind
    engine: str
    note: str = ""
    available: bool = True


class SourceInventory(BaseModel):
    file_type: str
    units: list[SourceUnit] = Field(default_factory=list)
    signals: list[InventorySignal] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    timing_ms: float = 0.0
    truncated: bool = False
    error: str | None = None  # inventory could not be built; extraction is unaffected

    def objects(self, type_: SourceObjectType | None = None) -> Iterator[SourceObject]:
        for u in self.units:
            for o in u.objects:
                if type_ is None or o.type == type_:
                    yield o

    def unit(self, index: int) -> SourceUnit | None:
        return next((u for u in self.units if u.unit.index == index), None)
