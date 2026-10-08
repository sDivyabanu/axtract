"""Optional Docling provider: a heavy, model-based second opinion for suspicious units only.

Docling is NOT a dependency of AXTRACT. It is imported lazily, reports itself unavailable when it is
not installed, and is never run in the normal /api/parse path: it is reachable only through explicit
escalation (verify.recovery.escalate), on units that Verify has already flagged. It converts the whole
file once per run and reuses that conversion for every suspicious unit.

Its output is a model's reading of the page, so its evidence kind is MODEL_BASED and it is never
treated as ground truth: it is compared with the original extraction and the independent source
evidence like any other candidate.
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from typing import Any

from verify.inventory.models import SourceInventory
from verify.models import EvidenceKind, UnitRef, UnitType
from verify.providers.base import ProviderCandidate, ProviderError


class DoclingProvider:
    name = "docling"
    tier = "heavy"
    kind = EvidenceKind.MODEL_BASED

    def __init__(self) -> None:
        self._doc: Any = None
        self._path: Path | None = None

    def available(self) -> tuple[bool, str]:
        if importlib.util.find_spec("docling") is None:
            return False, "docling is not installed (optional, heavy: pip install docling)"
        return True, ""

    def supports(self, inventory: SourceInventory, unit: UnitRef) -> bool:
        return inventory.file_type in ("pdf", "docx", "pptx", "xlsx") and unit.type in (UnitType.PAGE, UnitType.SLIDE, UnitType.SHEET)

    def _convert(self, file_path: Path):
        if self._doc is None or self._path != file_path:
            try:
                from docling.document_converter import DocumentConverter

                self._doc = DocumentConverter().convert(str(file_path)).document
                self._path = file_path
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(f"docling conversion failed: {type(exc).__name__}: {exc}") from exc
        return self._doc

    def extract(self, file_path: Path, unit: UnitRef, budget_s: float) -> ProviderCandidate:
        t0 = time.perf_counter()
        doc = self._convert(file_path)
        page = unit.index
        try:
            try:
                text = doc.export_to_markdown(page_no=page)
            except TypeError:  # older docling-core: collect the page's items by provenance
                text = "\n".join(
                    getattr(item, "text", "") or ""
                    for item, _level in doc.iterate_items()
                    if any(getattr(p, "page_no", None) == page for p in (getattr(item, "prov", None) or [])))
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"docling export failed: {type(exc).__name__}: {exc}") from exc
        if time.perf_counter() - t0 > budget_s:
            raise ProviderError("time budget exceeded")
        return ProviderCandidate(provider=self.name, unit=unit, text=text or "", kind=self.kind,
                                 duration_ms=round((time.perf_counter() - t0) * 1000, 3))
