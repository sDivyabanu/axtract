"""Layout analysis and reading-order reconstruction.

Handles:
- Single-column pages (top-to-bottom)
- Multi-column layouts (left column first, then right)
- Header/footer detection (top/bottom 8% of page)
- Assigns reading_order index to every block
"""

from __future__ import annotations

import re

from models.document import BlockType, DocumentBlock


# Page regions (normalized coordinates)
_HEADER_THRESHOLD = 0.08  # Top 8% is header zone
_FOOTER_THRESHOLD = 0.92  # Bottom 8% is footer zone


def _normalized_text(text: str) -> str:
    return re.sub(r"\W+", " ", text.casefold()).strip()


def _bbox_iou(a: tuple[float, float, float, float] | None,
              b: tuple[float, float, float, float] | None) -> float:
    if a is None or b is None:
        return 0.0
    ix0, iy0, ix1, iy1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union else 0.0


def deduplicate_blocks(blocks: list[DocumentBlock], threshold: float = 0.8) -> list[DocumentBlock]:
    """Drop same-text blocks whose normalized bounding boxes substantially overlap."""
    unique: list[DocumentBlock] = []
    by_page_text: dict[tuple[int, str], list[DocumentBlock]] = {}
    for block in blocks:
        text = _normalized_text(block.content)
        candidates = by_page_text.get((block.page, text), []) if text else []
        match = next((prior for prior in candidates
                      if _bbox_iou(prior.bbox, block.bbox) > threshold), None)
        if match is None:
            unique.append(block)
            if text:
                by_page_text.setdefault((block.page, text), []).append(block)
            continue
        sources = list(match.metadata.get("deduped_from", []))
        sources.append(block.id)
        match.metadata["deduped_from"] = list(dict.fromkeys(sources))
    return unique


def assign_reading_order(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    """Assign reading_order to blocks and sort them.

    Groups blocks by page, detects columns, classifies headers/footers,
    and produces a natural reading order.
    """
    if not blocks:
        return blocks

    # Filter before assigning indexes so reading_order stays contiguous.
    blocks = deduplicate_blocks(blocks)

    # Group by page
    pages: dict[int, list[DocumentBlock]] = {}
    for block in blocks:
        pages.setdefault(block.page, []).append(block)

    ordered: list[DocumentBlock] = []
    reading_idx = 0

    # A positional band alone is not enough to call a paragraph a running
    # header/footer. Require the same short text to recur at nearly the same
    # normalized vertical position on multiple pages, except explicit page nums.
    repeated_regions: set[tuple[str, int]] = set()
    occurrence_pages: dict[tuple[str, int], set[int]] = {}
    for block in blocks:
        if block.bbox is None or len(block.content.split()) > 12:
            continue
        center_y = round((block.bbox[1] + block.bbox[3]) / 2, 1)
        key = (_normalized_text(block.content), center_y)
        occurrence_pages.setdefault(key, set()).add(block.page)
    repeated_regions = {key for key, seen_pages in occurrence_pages.items() if len(seen_pages) >= 2}

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
                position_key = (_normalized_text(block.content), round(mid_y, 1))
                is_page_number = bool(re.fullmatch(r"(?:page\s*)?\d{1,4}", block.content.strip(), re.I))

                if (mid_y < _HEADER_THRESHOLD and position_key in repeated_regions
                        and block.type == BlockType.PARAGRAPH):
                    block.type = BlockType.HEADER
                    header_blocks.append(block)
                elif (mid_y > _FOOTER_THRESHOLD and block.type == BlockType.PARAGRAPH
                      and (position_key in repeated_regions or is_page_number)):
                    block.type = BlockType.FOOTER
                    footer_blocks.append(block)
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

    Returns list of (x_start, x_end) column ranges, sorted left to right.
    """
    if not blocks:
        return [(0.0, 1.0)]

    blocks_with_bbox = [b for b in blocks if b.bbox is not None]
    if len(blocks_with_bbox) < 3:
        return [(0.0, 1.0)]

    # Collect x-midpoints
    x_mids = sorted((b.bbox[0] + b.bbox[2]) / 2 for b in blocks_with_bbox)

    # Simple gap-based column detection
    # Look for a significant gap in x-midpoints near the center
    page_width = 1.0
    center = page_width / 2
    min_gap = 0.05  # Minimum gap to consider a column break

    # Check if there's a clear bimodal distribution
    left_blocks = [x for x in x_mids if x < center - min_gap]
    right_blocks = [x for x in x_mids if x > center + min_gap]

    if len(left_blocks) >= 2 and len(right_blocks) >= 2:
        # Likely two columns
        left_max = max(b.bbox[2] for b in blocks_with_bbox if (b.bbox[0] + b.bbox[2]) / 2 < center)
        right_min = min(b.bbox[0] for b in blocks_with_bbox if (b.bbox[0] + b.bbox[2]) / 2 > center)

        if right_min > left_max + min_gap:
            return [
                (0.0, (left_max + right_min) / 2),
                ((left_max + right_min) / 2, 1.0),
            ]

    return [(0.0, 1.0)]


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
