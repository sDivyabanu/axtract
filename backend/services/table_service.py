"""Advanced table parsing service: merged cells, cross-page merging, financial parsing."""

from __future__ import annotations

import re
from typing import Any

from models.document import BlockType, DocumentBlock


def parse_financial_number(value: str) -> float | str:
    """Parse financial numbers with various formats.
    
    Handles:
    - Parenthesized negatives: (1,234) -> -1234
    - Comma separators: 1,234.56 -> 1234.56
    - Currency symbols: $1,234 -> 1234
    - Percentages: 50% -> 0.5
    
    Returns parsed float if successful, original string otherwise.
    """
    if not value or not isinstance(value, str):
        return value
    
    # Remove currency symbols and whitespace
    cleaned = value.strip()
    cleaned = re.sub(r'[$€£¥₹]', '', cleaned)
    
    # Handle percentages
    if cleaned.endswith('%'):
        cleaned = cleaned[:-1]
        try:
            return float(cleaned.replace(',', '')) / 100
        except ValueError:
            return value
    
    # Handle parenthesized negatives
    if cleaned.startswith('(') and cleaned.endswith(')'):
        cleaned = cleaned[1:-1]
        try:
            return -float(cleaned.replace(',', ''))
        except ValueError:
            return value
    
    # Handle regular numbers with commas
    try:
        return float(cleaned.replace(',', ''))
    except ValueError:
        return value


def detect_merged_cells(table_data: list[list[Any]]) -> dict[str, Any]:
    """Detect merged cells in table data.
    
    Returns metadata with:
    - has_merged_cells: bool
    - merged_regions: list of (row, col, rowspan, colspan) tuples
    """
    merged_regions = []
    
    for row_idx, row in enumerate(table_data):
        for col_idx, cell in enumerate(row):
            if cell is None:
                continue
            
            # Check if this cell spans multiple columns (PyMuPDF indicates this)
            # This is a simplified detection - actual merged cell detection
            # depends on the extractor's capability
            if isinstance(cell, str) and "colspan" in cell.lower():
                # Parse colspan if encoded in text
                match = re.search(r'colspan[=:](\d+)', cell, re.IGNORECASE)
                if match:
                    colspan = int(match.group(1))
                    merged_regions.append((row_idx, col_idx, 1, colspan))
    
    return {
        "has_merged_cells": len(merged_regions) > 0,
        "merged_regions": merged_regions,
    }


def detect_multi_row_headers(table_data: list[list[Any]]) -> dict[str, Any]:
    """Detect multi-row headers in table data.
    
    Returns metadata with:
    - has_multi_row_header: bool
    - header_row_count: int
    - header_rows: list of header row indices
    """
    if not table_data or len(table_data) < 2:
        return {"has_multi_row_header": False, "header_row_count": 1, "header_rows": [0]}
    
    # Heuristic: first 1-2 rows are headers if they contain non-numeric text
    header_rows = [0]
    potential_header_count = 0
    
    for row_idx in range(1, min(3, len(table_data))):
        row = table_data[row_idx]
        numeric_count = sum(1 for cell in row if is_numeric_cell(cell))
        
        # If row has mostly non-numeric cells, it's likely a header
        if numeric_count / len(row) < 0.5:
            header_rows.append(row_idx)
            potential_header_count += 1
        else:
            break
    
    return {
        "has_multi_row_header": len(header_rows) > 1,
        "header_row_count": len(header_rows),
        "header_rows": header_rows,
    }


def is_numeric_cell(cell: Any) -> bool:
    """Check if a cell contains numeric data."""
    if cell is None:
        return False
    if isinstance(cell, (int, float)):
        return True
    if isinstance(cell, str):
        # Try to parse as number
        try:
            float(cell.replace(',', '').replace('$', '').replace('%', ''))
            return True
        except ValueError:
            return False
    return False


def match_table_continuation(
    table1: DocumentBlock, table2: DocumentBlock, page_width: float, page_height: float
) -> bool:
    """Determine if table2 is a continuation of table1 (cross-page table).
    
    Criteria:
    1. Similar column count (allow small variance)
    2. Similar column widths/alignment
    3. table2 is on the next page
    4. Similar bbox horizontal position
    """
    if table1.page + 1 != table2.page:
        return False
    
    # Check column count similarity
    col_count1 = table1.metadata.get("col_count", 0)
    col_count2 = table2.metadata.get("col_count", 0)
    
    if col_count1 == 0 or col_count2 == 0:
        return False
    
    # Allow ±1 column variance
    if abs(col_count1 - col_count2) > 1:
        return False
    
    # Check horizontal alignment similarity
    bbox1 = table1.bbox
    bbox2 = table2.bbox
    
    if not bbox1 or not bbox2:
        return False
    
    # Check if horizontal positions are similar (within 5% of page width)
    x1_diff = abs(bbox1[0] - bbox2[0])
    x2_diff = abs(bbox1[2] - bbox2[2])
    
    if x1_diff > 0.05 or x2_diff > 0.05:
        return False
    
    return True


def merge_cross_page_tables(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    """Merge tables that span across multiple pages.
    
    Returns a new list of blocks with cross-page tables merged.
    """
    if not blocks:
        return blocks
    
    # Group tables by page
    tables_by_page: dict[int, list[DocumentBlock]] = {}
    for block in blocks:
        if block.type == "table":
            tables_by_page.setdefault(block.page, []).append(block)
    
    # Find and merge cross-page tables
    merged_blocks = []
    processed_tables = set()
    
    for page_num in sorted(tables_by_page.keys()):
        page_tables = tables_by_page[page_num]
        
        for table in page_tables:
            if table.id in processed_tables:
                continue
            
            # Check if this table continues to next page
            next_page = page_num + 1
            if next_page in tables_by_page:
                for next_table in tables_by_page[next_page]:
                    if next_table.id in processed_tables:
                        continue
                    
                    if match_table_continuation(table, next_table, 1.0, 1.0):
                        # Merge the tables
                        merged_table = merge_two_tables(table, next_table)
                        merged_blocks.append(merged_table)
                        processed_tables.add(table.id)
                        processed_tables.add(next_table.id)
                        break
                else:
                    # No continuation found
                    merged_blocks.append(table)
                    processed_tables.add(table.id)
            else:
                # No next page
                merged_blocks.append(table)
                processed_tables.add(table.id)
    
    # Add non-table blocks
    for block in blocks:
        if block.type != "table" or block.id not in processed_tables:
            merged_blocks.append(block)
    
    return merged_blocks


def merge_two_tables(table1: DocumentBlock, table2: DocumentBlock) -> DocumentBlock:
    """Merge two table blocks into one."""
    rows1 = table1.metadata.get("rows", [])
    rows2 = table2.metadata.get("rows", [])
    
    # Merge rows (skip header from table2 if it's a duplicate)
    # Simple heuristic: if first row of table2 looks like header, skip it
    merged_rows = rows1[:]
    
    if len(rows2) > 1:
        # Skip potential header row
        merged_rows.extend(rows2[1:])
    else:
        merged_rows.extend(rows2)
    
    # Merge content
    content1 = table1.content
    content2 = table2.content
    merged_content = content1 + "\n" + content2
    
    # Update metadata
    merged_metadata = table1.metadata.copy()
    merged_metadata.update({
        "rows": merged_rows,
        "row_count": len(merged_rows),
        "is_cross_page_merged": True,
        "merged_from_pages": [table1.page, table2.page],
        "merged_table_ids": [table1.id, table2.id],
    })
    
    # Create merged block
    return DocumentBlock(
        id=f"{table1.id}-merged",
        type=BlockType.TABLE,
        content=merged_content,
        page=table1.page,
        bbox=table1.bbox,  # Keep original bbox
        confidence=table1.confidence,
        extractor=table1.extractor,
        reading_order=table1.reading_order,
        requires_review=table1.requires_review or table2.requires_review,
        metadata=merged_metadata,
    )


def enhance_table_block(block: DocumentBlock) -> DocumentBlock:
    """Enhance a table block with advanced parsing metadata."""
    if block.type != "table":
        return block
    
    rows = block.metadata.get("rows", [])
    if not rows:
        return block
    
    # Detect merged cells
    merged_info = detect_merged_cells(rows)
    
    # Detect multi-row headers
    header_info = detect_multi_row_headers(rows)
    
    # Parse financial numbers in table
    parsed_rows = []
    for row in rows:
        parsed_row = [parse_financial_number(str(cell) if cell else "") for cell in row]
        parsed_rows.append(parsed_row)
    
    # Update metadata
    enhanced_metadata = block.metadata.copy()
    enhanced_metadata.update({
        "merged_cells": merged_info,
        "multi_row_header": header_info,
        "parsed_rows": parsed_rows,
    })
    
    return DocumentBlock(
        id=block.id,
        type=block.type,
        content=block.content,
        page=block.page,
        bbox=block.bbox,
        confidence=block.confidence,
        extractor=block.extractor,
        reading_order=block.reading_order,
        requires_review=block.requires_review,
        metadata=enhanced_metadata,
    )
