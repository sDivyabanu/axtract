"""Advanced table parsing service: merged cells, cross-page merging, financial parsing."""

from __future__ import annotations

import re
from typing import Any

from models.document import BlockType, DocumentBlock


_NUM_US = re.compile(r"^[+-]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?$")
_NUM_EU = re.compile(r"^[+-]?(\d{1,3}(\.\d{3})+|\d+)(,\d+)$")
_NUM_SPACE = re.compile(r"^[+-]?\d{1,3}( \d{3})+(\.\d+)?$")


def _to_number(text: str) -> float | None:
    """Strict numeric parse: no nan/inf/underscores/exponents, thousands separators
    must be well formed. Returns None when the text is not unambiguously a number."""
    t = text.strip().replace("\u2212", "-").replace("\u00a0", " ")
    if _NUM_US.match(t):
        return float(t.replace(",", ""))
    if _NUM_EU.match(t):  # 1.234,56
        return float(t.replace(".", "").replace(",", "."))
    if _NUM_SPACE.match(t):
        return float(t.replace(" ", ""))
    return None


def parse_financial_number(value: str) -> float | str:
    """Parse financial numbers with various formats.

    Handles parenthesized negatives ``(1,234)``, thousands separators (US ``1,234.56``
    and EU ``1.234,56``), currency symbols, percentages ``50%`` -> 0.5 and
    parenthesized percentages ``(12.5%)`` -> -0.125.

    Returns the parsed float, or the original string when it is not clearly a number
    (so values are never guessed).
    """
    if not value or not isinstance(value, str):
        return value

    cleaned = re.sub(r"[$€£¥₹]", "", value.strip()).strip()
    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative = True
        cleaned = cleaned[1:-1].strip()

    percent = cleaned.endswith("%")
    if percent:
        cleaned = cleaned[:-1].strip()

    number = _to_number(cleaned)
    if number is None:
        return value
    if percent:
        number = number / 100
    number = round(number, 10)
    return -number if negative else number


def detect_merged_cells(table_data: list[list[Any]]) -> dict[str, Any]:
    """Detect merged cells from a raw extractor grid.

    PyMuPDF reports the continuation cells of a merged region as ``None`` (while truly
    empty cells are ``""``). A text cell followed by ``None`` cells on the same row is a
    column span; a text cell with ``None`` below it (and no column span) is a row span.

    Returns ``{"has_merged_cells": bool, "merged_regions": [(row, col, rowspan, colspan)]}``.
    """
    regions: list[tuple[int, int, int, int]] = []
    n_rows = len(table_data)

    # Pass 1: cells that are continuations of a column span (None right of a text cell)
    covered: set[tuple[int, int]] = set()
    for r, row in enumerate(table_data):
        for c, cell in enumerate(row):
            if cell not in (None, ""):
                k = c + 1
                while k < len(row) and row[k] is None:
                    covered.add((r, k))
                    k += 1

    for r, row in enumerate(table_data):
        c = 0
        while c < len(row):
            cell = row[c]
            if cell is None or cell == "":
                c += 1
                continue
            colspan = 1
            while c + colspan < len(row) and row[c + colspan] is None:
                colspan += 1
            rowspan = 1
            if colspan == 1:
                while (
                    r + rowspan < n_rows
                    and c < len(table_data[r + rowspan])
                    and table_data[r + rowspan][c] is None
                    and (r + rowspan, c) not in covered
                ):
                    rowspan += 1
            if colspan > 1 or rowspan > 1:
                regions.append((r, c, rowspan, colspan))
            c += colspan
    return {"has_merged_cells": bool(regions), "merged_regions": regions}


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
        filled = [c for c in row if c not in (None, "")]
        if not filled:
            break
        numeric_count = sum(1 for cell in filled if is_numeric_cell(cell))

        # A continuation header row has text labels and no numbers
        if numeric_count == 0:
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
        return not isinstance(parse_financial_number(cell), str)
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
    last_page = max(table1.metadata.get("merged_from_pages", [table1.page]))
    if last_page + 1 != table2.page:
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


def _norm_row(row: list[Any]) -> list[str]:
    return [" ".join(str(c).split()).lower() if c is not None else "" for c in row]


def merge_cross_page_tables(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    """Merge tables that continue on the next page (chains of any length).

    Block order is preserved: a merged table takes the place of its first part and the
    continuation parts are removed. Returns a new list.
    """
    if not blocks:
        return blocks

    tables = [b for b in blocks if b.type == BlockType.TABLE]
    by_page: dict[int, list[DocumentBlock]] = {}
    for t in tables:
        by_page.setdefault(t.page, []).append(t)

    replaced: dict[str, DocumentBlock] = {}
    absorbed: set[str] = set()

    for page_num in sorted(by_page):
        for table in by_page[page_num]:
            if table.id in absorbed:
                continue
            current = replaced.get(table.id, table)
            while True:
                nxt = None
                next_page = max(current.metadata.get("merged_from_pages", [current.page])) + 1
                for cand in by_page.get(next_page, []):
                    if cand.id not in absorbed and match_table_continuation(current, cand, 1.0, 1.0):
                        nxt = cand
                        break
                if nxt is None:
                    break
                current = merge_two_tables(current, nxt)
                absorbed.add(nxt.id)
            if current is not table:
                replaced[table.id] = current

    out: list[DocumentBlock] = []
    for b in blocks:
        if b.id in absorbed:
            continue
        out.append(replaced.get(b.id, b))
    return out


def merge_two_tables(table1: DocumentBlock, table2: DocumentBlock) -> DocumentBlock:
    """Merge a continuation table into the previous one.

    The first row of the continuation is dropped only when it repeats the header of the
    first table; otherwise it is real data and is kept.
    """
    rows1 = list(table1.metadata.get("rows", []))
    rows2 = list(table2.metadata.get("rows", []))

    header_rows = table1.metadata.get("multi_row_header", {}).get("header_row_count", 1)
    header1 = [_norm_row(r) for r in rows1[:header_rows]]
    skip = 0
    while skip < len(header1) and skip < len(rows2) and _norm_row(rows2[skip]) == header1[skip]:
        skip += 1
    merged_rows = rows1 + rows2[skip:]

    # keep per-row page and per-cell boxes aligned with the merged rows
    pages1 = list(table1.metadata.get("row_pages") or [table1.page] * len(rows1))
    pages2 = list(table2.metadata.get("row_pages") or [table2.page] * len(rows2))
    boxes1 = table1.metadata.get("cell_bboxes") or []
    boxes2 = table2.metadata.get("cell_bboxes") or []
    row_pages = pages1 + pages2[skip:]
    cell_bboxes = (boxes1 + boxes2[skip:]) if (len(boxes1) == len(rows1) and len(boxes2) == len(rows2)) else []

    merged_content = table1.content + "\n" + "\n".join(
        line for i, line in enumerate(table2.content.split("\n")) if i >= skip
    )

    parts = table1.metadata.get("merged_from_pages", [table1.page]) + [table2.page]
    ids = table1.metadata.get("merged_table_ids", [table1.id]) + [table2.id]
    metadata = {
        k: v
        for k, v in table1.metadata.items()
        if k not in ("parsed_rows", "merged_cells", "multi_row_header")
    }
    metadata.update(
        {
            "rows": merged_rows,
            "row_count": len(merged_rows),
            "row_pages": row_pages,
            "cell_bboxes": cell_bboxes,
            "is_cross_page_merged": True,
            "merged_from_pages": parts,
            "merged_table_ids": ids,
            # keep each part's own location so provenance stays accurate
            "part_bboxes": table1.metadata.get("part_bboxes", [{"page": table1.page, "bbox": table1.bbox}])
            + [{"page": table2.page, "bbox": table2.bbox}],
        }
    )

    return enhance_table_block(
        DocumentBlock(
            id=table1.id if table1.id.endswith("-merged") else f"{table1.id}-merged",
            type=BlockType.TABLE,
            content=merged_content,
            page=table1.page,
            bbox=table1.bbox,
            confidence=table1.confidence,
            extractor=table1.extractor,
            reading_order=table1.reading_order,
            requires_review=table1.requires_review or table2.requires_review,
            metadata=metadata,
        )
    )


def enhance_table_block(block: DocumentBlock) -> DocumentBlock:
    """Enhance a table block with advanced parsing metadata."""
    if block.type != "table":
        return block
    
    rows = block.metadata.get("rows", [])
    if not rows:
        return block
    
    # Merged cells: trust the extractor when it supplied them (xlsx/docx/pptx know the
    # real spans); otherwise infer them from None-continuation cells.
    provided = block.metadata.get("merged_cells")
    merged_info = provided if isinstance(provided, dict) else detect_merged_cells(rows)
    
    # Detect multi-row headers
    header_info = detect_multi_row_headers(rows)
    
    # Parse financial numbers in table
    parsed_rows = []
    for row in rows:
        parsed_row = [parse_financial_number(str(cell)) if cell not in (None, "") else cell for cell in row]
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
