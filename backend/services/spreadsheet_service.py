"""Spreadsheet intelligence service: formula detection and hidden content discovery.

Implements:
1. Formula vs hardcoded value detection
2. Hidden rows/columns/sheets discovery
3. Financial line flagging for hardcoded overrides
"""

from __future__ import annotations

import logging
import re
from typing import Any

from models.document import DocumentBlock

logger = logging.getLogger(__name__)


def detect_formulas_in_sheet(rows_data: list[list[str]], sheet_name: str) -> dict[str, Any]:
    """Detect which cells contain formulas vs hardcoded values.
    
    This is a heuristic detection since we're working with extracted text.
    In a real implementation, you would use openpyxl with data_only=False
    to get both formula and value.
    
    Returns:
    - formula_cells: list of (row, col, formula) tuples
    - hardcoded_cells: list of (row, col, value) tuples
    - formula_keywords: list of detected formula keywords
    """
    formula_keywords = ["sum", "avg", "if", "vlookup", "index", "match", "count", "max", "min"]
    formula_cells = []
    hardcoded_cells = []
    
    for row_idx, row in enumerate(rows_data):
        for col_idx, cell in enumerate(row):
            if not cell or not isinstance(cell, str):
                continue
            
            # Look for formula-like patterns
            cell_lower = cell.lower()
            
            # Check if it looks like a formula result (e.g., contains formula keywords)
            if any(keyword in cell_lower for keyword in formula_keywords):
                formula_cells.append((row_idx, col_idx, cell))
            
            # Check for numeric values that might be hardcoded
            # Financial indicators
            financial_keywords = ["revenue", "ebitda", "net income", "gross profit", "operating income"]
            if any(keyword in cell_lower for keyword in financial_keywords):
                # Look at the next cell for the value
                if col_idx + 1 < len(row):
                    next_cell = row[col_idx + 1]
                    if next_cell and re.match(r'^[\d,]+\.?\d*$', str(next_cell).replace(',', '')):
                        hardcoded_cells.append((row_idx, col_idx + 1, next_cell))
    
    return {
        "formula_cells": formula_cells,
        "hardcoded_cells": hardcoded_cells,
        "formula_keywords_detected": formula_keywords,
    }


def detect_hidden_content(sheet_data: dict[str, Any]) -> dict[str, Any]:
    """Detect hidden rows, columns, and sheets.
    
    This is a placeholder for actual hidden content detection.
    In a real implementation with openpyxl, you would check:
    - ws.row_dimensions[row].hidden
    - ws.column_dimensions[col].hidden
    - wb.sheetnames vs visible sheets
    
    Returns:
    - hidden_rows: list of row indices
    - hidden_columns: list of column letters
    - hidden_sheets: list of sheet names
    """
    # Placeholder implementation
    logger.warning("Hidden content detection requires full openpyxl access - using placeholder")
    
    return {
        "hidden_rows": [],
        "hidden_columns": [],
        "hidden_sheets": [],
        "detection_method": "placeholder",
    }


def flag_hardcoded_financial_overrides(
    formula_info: dict[str, Any], sheet_name: str
) -> list[dict[str, Any]]:
    """Flag hardcoded values in key financial lines.
    
    Key financial lines: Revenue, EBITDA, Net Income, etc.
    Hardcoded values in these lines should be flagged for review.
    
    Returns list of flagged cells with context.
    """
    flagged = []
    
    financial_lines = ["revenue", "ebitda", "net income", "gross profit", "operating income"]
    
    for row_idx, col_idx, value in formula_info.get("hardcoded_cells", []):
        # Check if this is in a financial line
        # Look at the previous cell for the label
        if col_idx > 0:
            # This is simplified - in real implementation, check row context
            flagged.append({
                "row": row_idx,
                "col": col_idx,
                "value": value,
                "sheet": sheet_name,
                "reason": "hardcoded_value_in_financial_line",
                "severity": "warning",
            })
    
    return flagged


def enhance_spreadsheet_block(block: DocumentBlock) -> DocumentBlock:
    """Enhance a spreadsheet block with formula and hidden content detection."""
    if block.type != "table" or block.extractor != "openpyxl":
        return block
    
    rows_data = block.metadata.get("rows", [])
    sheet_name = block.metadata.get("sheet_name", "unknown")
    
    if not rows_data:
        return block
    
    # Detect formulas
    formula_info = detect_formulas_in_sheet(rows_data, sheet_name)
    
    # Detect hidden content
    hidden_info = detect_hidden_content(block.metadata)
    
    # Flag hardcoded financial overrides
    flagged_cells = flag_hardcoded_financial_overrides(formula_info, sheet_name)
    
    # Update metadata
    enhanced_metadata = block.metadata.copy()
    enhanced_metadata.update({
        "formula_detection": formula_info,
        "hidden_content": hidden_info,
        "flagged_cells": flagged_cells,
        "has_formulas": len(formula_info.get("formula_cells", [])) > 0,
        "has_hardcoded_overrides": len(flagged_cells) > 0,
    })
    
    # Flag for review if there are hardcoded overrides
    requires_review = block.requires_review or len(flagged_cells) > 0
    
    return DocumentBlock(
        id=block.id,
        type=block.type,
        content=block.content,
        page=block.page,
        bbox=block.bbox,
        confidence=block.confidence,
        extractor=block.extractor,
        reading_order=block.reading_order,
        requires_review=requires_review,
        metadata=enhanced_metadata,
    )
