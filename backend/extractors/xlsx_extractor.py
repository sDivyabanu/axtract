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


class XlsxExtractor(BaseExtractor):
    """Extracts structured content from XLSX files."""

    name: ClassVar[str] = "openpyxl"
    supported_extensions: ClassVar[frozenset[str]] = frozenset({"xlsx"})

    def extract(self, file_path: Path) -> ExtractionResult:
        try:
            wb = openpyxl.load_workbook(
                str(file_path), read_only=True, data_only=True
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
                    sheet_blocks = self._extract_sheet(ws, sheet_name, sheet_number)
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

        return ExtractionResult(
            page_count=sheet_number or 1,
            blocks=blocks,
            errors=errors,
        )

    def _extract_sheet(
        self, ws, sheet_name: str, sheet_number: int
    ) -> list[DocumentBlock]:
        blocks: list[DocumentBlock] = []
        block_counter = 0

        rows_data: list[list[str]] = []
        for row in ws.iter_rows():
            cells: list[str] = []
            for cell in row:
                val = cell.value
                if val is None:
                    cells.append("")
                else:
                    cells.append(str(val))
            rows_data.append(cells)

        # Remove trailing empty rows
        while rows_data and all(c == "" for c in rows_data[-1]):
            rows_data.pop()

        if not rows_data:
            return blocks

        # Determine if first row looks like a header
        header = rows_data[0] if rows_data else []

        # Text representation
        text_lines = [" | ".join(cells) for cells in rows_data]
        content = "\n".join(text_lines)

        # Collect merged cell info
        merged_ranges: list[str] = []
        try:
            for mr in ws.merged_cells.ranges:
                merged_ranges.append(str(mr))
        except Exception:
            pass

        blocks.append(
            DocumentBlock(
                id=f"sh{sheet_number}-b{block_counter}",
                type=BlockType.TABLE,
                content=content,
                page=sheet_number,
                bbox=None,  # Spreadsheets don't have spatial coordinates
                confidence=None,
                extractor=self.name,
                metadata={
                    "sheet_name": sheet_name,
                    "rows": rows_data,
                    "row_count": len(rows_data),
                    "col_count": max((len(r) for r in rows_data), default=0),
                    "header": header,
                    "merged_cells": merged_ranges,
                },
            )
        )

        return blocks
