"""XLSX extractor using openpyxl.

Preserves sheet context, table structure, merged cells, and formulas.
Each sheet becomes a separate table block.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import openpyxl

from extractors.base import BaseExtractor, ExtractionResult
from models.document import BlockType, DocumentBlock
from models.errors import AppError, DocumentError


# Above these sizes the workbook is opened in streaming mode / not re-read for formulas.
_FULL_LOAD_BYTES = 25 * 1024 * 1024
_FORMULA_CHECK_BYTES = 5 * 1024 * 1024


class XlsxExtractor(BaseExtractor):
    """Extracts structured content from XLSX files."""

    name: ClassVar[str] = "openpyxl"
    supported_extensions: ClassVar[frozenset[str]] = frozenset({"xlsx"})

    def extract(self, file_path: Path) -> ExtractionResult:
        # Merged cells are only visible outside read-only mode. Large files fall back to
        # read-only (streaming) and say so, instead of silently dropping the merge info.
        full = file_path.stat().st_size <= _FULL_LOAD_BYTES
        self._merge_info_available = full
        try:
            wb = openpyxl.load_workbook(str(file_path), read_only=not full, data_only=True)
            wb_formulas = (
                openpyxl.load_workbook(str(file_path), read_only=False, data_only=False)
                if file_path.stat().st_size <= _FORMULA_CHECK_BYTES
                else None
            )
        except Exception as exc:
            raise AppError(
                "INVALID_FILE",
                f"Could not open XLSX file: {exc}",
                status_code=422,
            ) from exc

        blocks: list[DocumentBlock] = []
        errors: list[DocumentError] = []
        sheet_number = 0

        try:
            for sheet_name in wb.sheetnames:
                sheet_number += 1
                try:
                    ws = wb[sheet_name]
                    sheet_blocks = self._extract_sheet(
                        ws, sheet_name, sheet_number, wb_formulas[sheet_name] if wb_formulas else None
                    )
                    blocks.extend(sheet_blocks)
                except Exception as exc:
                    errors.append(
                        DocumentError(
                            code="SHEET_EXTRACTION_FAILED",
                            message=f"Sheet '{sheet_name}': {exc}",
                            page=sheet_number,
                        )
                    )
        finally:
            wb.close()
            if wb_formulas is not None:
                wb_formulas.close()

        return ExtractionResult(
            page_count=sheet_number or 1,
            blocks=blocks,
            errors=errors,
        )

    def _extract_sheet(self, ws, sheet_name: str, sheet_number: int, ws_formulas=None) -> list[DocumentBlock]:
        rows_data: list[list[str | None]] = []
        raw_rows = []
        for row in ws.iter_rows(min_row=1, min_col=1):
            raw_rows.append([c.value for c in row])
            rows_data.append(["" if c.value is None else str(c.value) for c in row])

        # Remove trailing empty rows
        while rows_data and all(c == "" for c in rows_data[-1]):
            rows_data.pop()
            raw_rows.pop()
        if not rows_data:
            return []

        # Merged cells: keep the value at the origin, None for covered cells (same convention
        # as the PDF/DOCX/PPTX extractors), and expose the regions.
        regions: list[tuple[int, int, int, int]] = []
        if self._merge_info_available:
            for mr in ws.merged_cells.ranges:
                r0, c0 = mr.min_row - 1, mr.min_col - 1
                if r0 >= len(rows_data):
                    continue
                regions.append((r0, c0, mr.max_row - mr.min_row + 1, mr.max_col - mr.min_col + 1))
                for r in range(r0, min(mr.max_row, len(rows_data))):
                    for c in range(c0, min(mr.max_col, len(rows_data[r]))):
                        if (r, c) != (r0, c0):
                            rows_data[r][c] = None
            regions.sort()

        # Formulas that have no cached value come back empty with data_only=True.
        uncached = 0
        if ws_formulas is not None:
            for r, row in enumerate(ws_formulas.iter_rows(min_row=1, min_col=1)):
                if r >= len(raw_rows):
                    break
                for c, cell in enumerate(row):
                    if isinstance(cell.value, str) and cell.value.startswith("=") and c < len(raw_rows[r]) and raw_rows[r][c] is None:
                        uncached += 1

        metadata = {
            "sheet_name": sheet_name,
            "rows": rows_data,
            "row_count": len(rows_data),
            "col_count": max((len(r) for r in rows_data), default=0),
            "header": rows_data[0],
            "merged_cells": {"has_merged_cells": bool(regions), "merged_regions": regions},
        }
        flags = []
        if not self._merge_info_available:
            flags.append("merged_cells_not_read_large_file")
        if uncached:
            metadata["uncached_formulas"] = uncached
            flags.append("formulas_without_cached_values")
        if flags:
            metadata["flags"] = flags
            metadata["needs_review"] = True
        # Spreadsheets have no page geometry; expose the sheet extent as an
        # estimated bbox so provenance consumers get a coordinate (flagged).
        metadata["bbox_estimated"] = "whole_sheet"

        return [
            DocumentBlock(
                id=f"sh{sheet_number}-b0",
                type=BlockType.TABLE,
                content="\n".join(" | ".join("" if c is None else c for c in row) for row in rows_data),
                page=sheet_number,
                bbox=(0.0, 0.0, 1.0, 1.0),
                confidence=None,
                extractor=self.name,
                requires_review=bool(flags),
                metadata=metadata,
            )
        ]
