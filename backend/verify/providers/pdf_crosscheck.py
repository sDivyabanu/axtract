"""Lightweight PDF cross-validation: a second, independent reader of a page's text layer and tables.

Uses pdfplumber (pdfminer), which shares no code with the extractor's PyMuPDF. It is cheap and
in-process, so it may run in the normal request path for suspicious pages. Its output is a candidate
to compare, not a replacement: two readers of the same text layer agreeing is useful evidence, and
two readers disagreeing is exactly what should send a unit to review.

Scanned pages have no text layer: this provider declines them (OCR is not an independent reader).
"""

from __future__ import annotations

import time
from pathlib import Path

from verify.inventory.models import SourceInventory
from verify.models import EvidenceKind, UnitRef, UnitType
from verify.providers.base import ProviderCandidate, ProviderError


class PdfCrossCheckProvider:
    name = "pdf-crosscheck"
    tier = "builtin"
    kind = EvidenceKind.DETERMINISTIC  # the text layer, read exactly (layout is not interpreted)

    def available(self) -> tuple[bool, str]:
        try:
            import pdfplumber  # noqa: F401
        except ImportError:
            return False, "pdfplumber is not installed"
        return True, ""

    def supports(self, inventory: SourceInventory, unit: UnitRef) -> bool:
        if inventory.file_type != "pdf" or unit.type != UnitType.PAGE:
            return False
        su = inventory.unit(unit.index)
        return bool(su and su.independent_text_available)

    def extract(self, file_path: Path, unit: UnitRef, budget_s: float) -> ProviderCandidate:
        import pdfplumber

        t0 = time.perf_counter()
        try:
            with pdfplumber.open(str(file_path)) as pdf:
                page = pdf.pages[unit.index - 1]
                text = page.extract_text() or ""
                tables = [[[(c or "").strip() for c in row] for row in t.extract()] for t in page.find_tables()[:10]]
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc
        if time.perf_counter() - t0 > budget_s:
            raise ProviderError("time budget exceeded")
        return ProviderCandidate(provider=self.name, unit=unit, text=text, kind=self.kind, structure={"tables": tables},
                                 duration_ms=round((time.perf_counter() - t0) * 1000, 3))
