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
    5. The first table reaches the bottom of its page and the next starts near the top
    """
    last_page = max(table1.metadata.get("merged_from_pages", [table1.page]))
    if last_page + 1 != table2.page:
        return False
    
    # Check column count similarity
    col_count1 = table1.metadata.get("col_count", 0)
    col_count2 = table2.metadata.get("col_count", 0)
    
    if col_count1 == 0 or col_count2 == 0:
        return False
    
    # A change in parsed column count is a strong sign that the next page
    # contains another table, even when it shares the same page margins.
    if col_count1 != col_count2:
        return False
    
    # Check horizontal alignment similarity
    bbox1 = table1.bbox
    bbox2 = table2.bbox
    
    if not bbox1 or not bbox2:
        return False

    # Tables on unrelated pages often reuse the same column count and page
    # margins. A real cross-page continuation should touch both page edges;
    # otherwise preserving separate table blocks is safer.
    if bbox1[3] < 0.75 or bbox2[1] > 0.25:
        return False
    
    # Check if horizontal positions are similar (within 5% of page width)
    x1_diff = abs(bbox1[0] - bbox2[0])
    x2_diff = abs(bbox1[2] - bbox2[2])
    
    if x1_diff > 0.05 or x2_diff > 0.05:
        return False

    rows1 = table1.metadata.get("rows", [])
    rows2 = table2.metadata.get("rows", [])
    if rows1 and rows2:
        header1 = [str(c).strip().lower() for c in rows1[0] if c not in (None, "")]
        header2 = [str(c).strip().lower() for c in rows2[0] if c not in (None, "")]
        # When both first rows look like labels, changed headers distinguish a
        # new table. A continuation with no repeated header remains eligible.
        header1_like = len(header1) >= 2 and all(not is_numeric_cell(c) for c in header1)
        header2_like = len(header2) >= 2 and all(not is_numeric_cell(c) for c in header2)
        if header1_like and header2_like and header1 != header2:
            return False

        # Repeated row labels are evidence of two parallel tables (for example,
        # the same course list with a different measure), not a continued list
        # split at a page break.
        body_labels1 = {
            " ".join(str(row[0]).lower().split())
            for row in rows1[1:] if row and row[0] not in (None, "")
        }
        body_labels2 = {
            " ".join(str(row[0]).lower().split())
            for row in rows2[1:] if row and row[0] not in (None, "")
        }
        smaller_label_set = min(len(body_labels1), len(body_labels2))
        if smaller_label_set >= 2 and len(body_labels1 & body_labels2) / smaller_label_set >= 0.5:
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


def reconstruct_table_from_ocr(
    blocks: list["DocumentBlock"], page: int
) -> tuple["DocumentBlock", list["DocumentBlock"]] | None:
    """Rebuild a table from OCR text blocks on one page using box geometry.

    Groups OCR blocks into visual lines (y) and columns (x), then keeps the
    structure only when it genuinely looks tabular: at least two lines that
    align into at least two consistent columns. Prose (one full-width block
    per line) never qualifies. Returns ``(table_block, consumed_blocks)``
    or ``None`` when no confident table exists.
    """
    ocr = [
        b for b in blocks
        if b.page == page and b.bbox and b.extractor == "rapidocr"
        and b.type == BlockType.PARAGRAPH
    ]
    if len(ocr) < 4:
        return None

    def yc(b) -> float:
        return (b.bbox[1] + b.bbox[3]) / 2

    heights = sorted(b.bbox[3] - b.bbox[1] for b in ocr)
    med_h = max(heights[len(heights) // 2], 0.004)

    # 1. visual lines: y-centers within 0.6 * median height
    lines: list[list] = []
    for b in sorted(ocr, key=yc):
        if lines and abs(yc(b) - sum(yc(x) for x in lines[-1]) / len(lines[-1])) <= 0.6 * med_h:
            lines[-1].append(b)
        else:
            lines.append([b])
    for ln in lines:
        ln.sort(key=lambda b: b.bbox[0])

    multi = [ln for ln in lines if len(ln) >= 2]
    if len(multi) < 2:
        return None

    # 2. column clusters from left edges of blocks in multi-cell lines
    #    (left edges are stable for left-aligned columns; numbers under a
    #    header still start near the same x)
    edges = sorted(b.bbox[0] for ln in multi for b in ln)
    clusters: list[list[float]] = []
    for e in edges:
        if clusters and e - clusters[-1][-1] <= 0.045:
            clusters[-1].append(e)
        else:
            clusters.append([e])
    col_lefts = [sum(c) / len(c) for c in clusters]
    if len(col_lefts) < 2:
        return None

    # 3. assign blocks to columns; a line is a table row only when every
    #    block maps cleanly to a distinct column
    tol = 0.055
    grid: list[tuple[list, list[str]]] = []
    for ln in multi:
        row: list[str | None] = [None] * len(col_lefts)
        ok = True
        for b in ln:
            ci = min(range(len(col_lefts)), key=lambda i: abs(b.bbox[0] - col_lefts[i]))
            if abs(b.bbox[0] - col_lefts[ci]) > tol or row[ci] is not None:
                ok = False
                break
            row[ci] = " ".join(b.content.split())
        if ok:
            grid.append((ln, [" ".join(r.split()) if r else "" for r in row]))  # type: ignore[union-attr]

    if len(grid) < 2:
        return None

    rows = [r for _, r in grid]
    n_cols = len(col_lefts)
    cells = sum(1 for r in rows for c in r if c)
    if n_cols < 2 or len(rows) < 2 or cells < 6 or cells / (len(rows) * n_cols) < 0.55:
        return None
    strong_cols = sum(
        1 for c in range(n_cols) if sum(1 for r in rows if r[c]) >= 2
    )
    if strong_cols < 2:
        return None

    # 4. build the block
    consumed = [b for ln, _ in grid for b in ln]
    x0 = min(b.bbox[0] for b in consumed)
    y0 = min(b.bbox[1] for b in consumed)
    x1 = max(b.bbox[2] for b in consumed)
    y1 = max(b.bbox[3] for b in consumed)
    conf = min(b.confidence or 0.0 for b in consumed) or None

    content = "\n".join(" | ".join(r) for r in rows)
    block = DocumentBlock(
        id=f"p{page}-ocr-table",
        type=BlockType.TABLE,
        content=content,
        page=page,
        bbox=(round(x0, 6), round(y0, 6), round(x1, 6), round(y1, 6)),
        confidence=round(conf, 4) if conf else None,
        extractor="rapidocr",
        reading_order=None,
        requires_review=False,
        metadata={
            "rows": rows,
            "row_count": len(rows),
            "col_count": n_cols,
            "header": rows[0],
            "source": "ocr_reconstruction",
        },
    )
    return block, consumed


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
