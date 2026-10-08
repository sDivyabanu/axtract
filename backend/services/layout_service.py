"""Layout analysis and reading-order reconstruction.

Handles:
- Single-column pages (top-to-bottom)
- Multi-column layouts (left column first, then right)
- Header/footer detection (top/bottom 8% of page)
- Assigns reading_order index to every block
"""

from __future__ import annotations

import re
from collections import Counter

from models.document import BlockType, DocumentBlock


# Page regions (normalized coordinates)
_HEADER_THRESHOLD = 0.08  # Top 8% is header zone
_FOOTER_THRESHOLD = 0.92  # Bottom 8% is footer zone

_PAGE_NUM_RE = re.compile(
    r"(?:page\s*)?\d{1,4}(?:\s*(?:/|of)\s*\d{1,4})?", re.IGNORECASE
)


def _norm_text(text: str) -> str:
    return " ".join(text.split()).lower()


def _running_header_footer_maps(
    pages: dict[int, list[DocumentBlock]], page_count: int
) -> tuple[Counter, Counter]:
    """Count normalized texts sitting in the top/bottom zones across pages.

    Only repeated text (or lone page numbers) qualifies as a running
    header/footer — a one-off title in the top zone stays body text.
    """
    top_counts: Counter = Counter()
    bottom_counts: Counter = Counter()
    for blocks in pages.values():
        for b in blocks:
            if b.bbox is None or "route" not in b.metadata:
                continue
            if b.type != BlockType.PARAGRAPH:
                continue
            mid_y = (b.bbox[1] + b.bbox[3]) / 2
            text = _norm_text(b.content)
            if not text:
                continue
            if mid_y < _HEADER_THRESHOLD:
                top_counts[text] += 1
            elif mid_y > _FOOTER_THRESHOLD:
                bottom_counts[text] += 1
    return top_counts, bottom_counts


def _is_running(
    text: str, counts: Counter, page_count: int, *, is_top: bool
) -> bool:
    norm = _norm_text(text)
    if not norm:
        return False
    # A lone page number (top-right or bottom-center) is always a page marker.
    if not is_top and _PAGE_NUM_RE.fullmatch(norm):
        return True
    repeats = counts.get(norm, 0)
    if page_count >= 3 and repeats >= max(2, page_count // 2):
        return True
    if 2 <= page_count < 3 and repeats == page_count:
        return True
    return False


def assign_reading_order(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    """Assign reading_order to blocks and sort them.

    Groups blocks by page, detects columns, classifies headers/footers,
    and produces a natural reading order.
    """
    if not blocks:
        return blocks

    # Group by page
    pages: dict[int, list[DocumentBlock]] = {}
    for block in blocks:
        pages.setdefault(block.page, []).append(block)

    top_counts, bottom_counts = _running_header_footer_maps(pages, len(pages))

    ordered: list[DocumentBlock] = []
    reading_idx = 0

    for page_num in sorted(pages.keys()):
        page_blocks = pages[page_num]

        # Classify headers and footers by position
        body_blocks: list[DocumentBlock] = []
        header_blocks: list[DocumentBlock] = []
        footer_blocks: list[DocumentBlock] = []

        for block in page_blocks:
            # Running headers/footers only exist on real pages. Slides, standalone
            # images and charts have no such zones, so only PDF page content (which
            # the router tags with a "route") is classified by position.
            is_page_content = "route" in block.metadata
            if block.bbox is not None and is_page_content:
                _, y1, _, y2 = block.bbox
                mid_y = (y1 + y2) / 2

                if mid_y < _HEADER_THRESHOLD and block.type == BlockType.PARAGRAPH:
                    if _is_running(block.content, top_counts, len(pages), is_top=True):
                        block.type = BlockType.HEADER
                        header_blocks.append(block)
                    else:
                        body_blocks.append(block)
                elif mid_y > _FOOTER_THRESHOLD and block.type == BlockType.PARAGRAPH:
                    if _is_running(block.content, bottom_counts, len(pages), is_top=False):
                        block.type = BlockType.FOOTER
                        footer_blocks.append(block)
                    else:
                        body_blocks.append(block)
                else:
                    body_blocks.append(block)
            else:
                body_blocks.append(block)

        header_blocks.sort(key=lambda b: (b.bbox[1], b.bbox[0]))
        footer_blocks.sort(key=lambda b: (b.bbox[1], b.bbox[0]))

        # Detect columns among body blocks
        columns = _detect_columns(body_blocks)
        sorted_body = _sort_by_columns(body_blocks, columns)

        # Final order: headers → body → footers
        for block in header_blocks:
            block.reading_order = reading_idx
            ordered.append(block)
            reading_idx += 1

        for block in sorted_body:
            block.reading_order = reading_idx
            ordered.append(block)
            reading_idx += 1

        for block in footer_blocks:
            block.reading_order = reading_idx
            ordered.append(block)
            reading_idx += 1

    return ordered


def _detect_columns(blocks: list[DocumentBlock]) -> list[tuple[float, float]]:
    """Detect column boundaries from block x-coordinates.

    Finds the widest vertical whitespace gutter between merged block extents
    in the middle of the page. A page with any full-width block (table,
    figure) is single-column. Falls back to (0, 1) when no confident gutter
    exists.
    """
    if not blocks:
        return [(0.0, 1.0)]

    blocks_with_bbox = [b for b in blocks if b.bbox is not None]
    if len(blocks_with_bbox) < 3:
        return [(0.0, 1.0)]

    # Any full-width block means the page is not multi-column.
    if any(b.bbox[2] - b.bbox[0] > 0.6 for b in blocks_with_bbox):
        return [(0.0, 1.0)]

    # Merge overlapping x-extents, then look for the widest internal gap.
    intervals = sorted((b.bbox[0], b.bbox[2]) for b in blocks_with_bbox)
    merged: list[list[float]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1] + 0.015:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    best_gap = 0.0
    best_split: float | None = None
    for i in range(len(merged) - 1):
        gap = merged[i + 1][0] - merged[i][1]
        split = (merged[i][1] + merged[i + 1][0]) / 2
        # The gutter must sit in the middle band and actually separate content.
        if gap >= 0.03 and 0.2 <= split <= 0.8:
            left = [b for b in blocks_with_bbox if (b.bbox[0] + b.bbox[2]) / 2 < split]
            right = [b for b in blocks_with_bbox if (b.bbox[0] + b.bbox[2]) / 2 >= split]
            if len(left) >= 2 and len(right) >= 2 and gap > best_gap:
                best_gap = gap
                best_split = split

    if best_split is None:
        return [(0.0, 1.0)]
    return [(0.0, best_split), (best_split, 1.0)]


def _sort_by_columns(
    blocks: list[DocumentBlock],
    columns: list[tuple[float, float]],
) -> list[DocumentBlock]:
    """Sort blocks by column (left to right), then top to bottom within each column."""
    if len(columns) <= 1:
        # Single column: sort by vertical position
        return sorted(blocks, key=lambda b: (b.bbox[1] if b.bbox else 0.0))

    # Assign blocks to columns
    column_blocks: list[list[DocumentBlock]] = [[] for _ in columns]

    for block in blocks:
        if block.bbox is None:
            column_blocks[0].append(block)
            continue

        x_mid = (block.bbox[0] + block.bbox[2]) / 2
        assigned = False
        for i, (col_start, col_end) in enumerate(columns):
            if col_start <= x_mid <= col_end:
                column_blocks[i].append(block)
                assigned = True
                break
        if not assigned:
            column_blocks[0].append(block)

    # Sort each column by vertical position, then concatenate columns
    result: list[DocumentBlock] = []
    for col in column_blocks:
        col.sort(key=lambda b: b.bbox[1] if b.bbox else 0.0)
        result.extend(col)

    return result
