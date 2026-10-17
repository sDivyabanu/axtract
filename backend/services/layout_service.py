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
        if block.bbox is None or len(block.content.split()) > 40 or len(block.content) > 300:
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
                font_size = block.metadata.get("font_size")
                body_font_size = block.metadata.get("body_font_size")
                small_margin_line = (
                    isinstance(font_size, (int, float))
                    and isinstance(body_font_size, (int, float))
                    and body_font_size > 0
                    and font_size <= body_font_size * 0.85
                    and len(block.content.split()) <= 40
                    and len(block.content) <= 300
                )

                # Detached compact bottom furniture needs spatial evidence,
                # including when its font is slightly larger than dense body text.
                prior_bottoms = [b.bbox[3] for b in page_blocks if b is not block and b.bbox
                                 and b.type not in (BlockType.FIGURE, BlockType.HEADER, BlockType.FOOTER)
                                 and b.bbox[3] <= y1
                                 and min(b.bbox[2],block.bbox[2])>max(b.bbox[0],block.bbox[0])]
                detached = bool(prior_bottoms) and y1-max(prior_bottoms)>=.035
                safe_prose = not any(block.metadata.get(k) for k in
                                     ("text_layer_math","formula_candidate","footnote_refs"))
                extended_small_footer = safe_prose and y1>=.89 and detached and small_margin_line
                extreme_folio = (safe_prose and y1>=.96 and detached and y2-y1<=.025
                                 and block.bbox[2]-block.bbox[0]<=.65
                                 and 3<=len(block.content.split())<=20
                                 and isinstance(font_size,(int,float))
                                 and isinstance(body_font_size,(int,float))
                                 and 0<font_size<=1.25*body_font_size
                                 and re.search(r"^\d|\d[.)]?$",block.content.strip()))

                if (mid_y < _HEADER_THRESHOLD and (position_key in repeated_regions or small_margin_line)
                        and block.type == BlockType.PARAGRAPH):
                    block.type = BlockType.HEADER
                    header_blocks.append(block)
                elif (block.type == BlockType.PARAGRAPH and
                      ((mid_y > _FOOTER_THRESHOLD and (position_key in repeated_regions or is_page_number or small_margin_line))
                       or extended_small_footer or extreme_folio)):
                    block.type = BlockType.FOOTER
                    if extended_small_footer or extreme_folio:
                        block.metadata["margin_footer_evidence"] = "compact_detached_bottom_region"
                        block.requires_review = True
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

    # Full-width titles and callouts should not fill the gutter or bias the
    # column bounds. Most text columns occupy substantially less than 70% of
    # the page width, even when the two columns have different widths.
    column_blocks = [b for b in blocks_with_bbox if b.bbox[2] - b.bbox[0] < 0.70]
    if len(column_blocks) < 4:
        return [(0.0, 1.0)]
    paragraphs = [b for b in column_blocks if b.type == BlockType.PARAGRAPH
                  and len(b.content.split())>=40 and not b.metadata.get("text_layer_math")]
    if len(paragraphs)>=4:
        column_blocks = paragraphs  # author/address grids cannot fill a body-text gutter


    # Examine possible gutters throughout the middle 70% of the page. A
    # small normalized threshold admits narrow gutters while the requirement
    # for multiple blocks on each side avoids splitting an isolated paragraph.
    min_gap = 0.015
    candidates: list[tuple[float, float]] = []
    for left in column_blocks:
        cut = left.bbox[2] + min_gap
        if not 0.15 <= cut <= 0.85:
            continue
        left_side = [b for b in column_blocks if (b.bbox[0] + b.bbox[2]) / 2 < cut]
        right_side = [b for b in column_blocks if (b.bbox[0] + b.bbox[2]) / 2 > cut]
        if len(left_side) < 2 or len(right_side) < 2:
            continue
        left_max = max(b.bbox[2] for b in left_side)
        right_min = min(b.bbox[0] for b in right_side)
        gap = right_min - left_max
        if gap >= min_gap:
            candidates.append((gap, (left_max + right_min) / 2))

    if candidates:
        _, split = max(candidates, key=lambda candidate: candidate[0])
        return [(0.0, split), (split, 1.0)]

    return [(0.0, 1.0)]


def _sort_by_columns(
    blocks: list[DocumentBlock],
    columns: list[tuple[float, float]],
) -> list[DocumentBlock]:
    """Sort blocks by column (left to right), then top to bottom within each column."""
    if len(columns) <= 1:
        # Single column: sort by vertical position
        return sorted(blocks, key=lambda b: (b.bbox[1] if b.bbox else 0.0))

    split = columns[0][1]
    spanning = [
        b for b in blocks
        if b.bbox is not None and b.bbox[0] < split < b.bbox[2]
        and b.bbox[2] - b.bbox[0] >= 0.65
    ]
    spanning.sort(key=lambda b: (b.bbox[1], b.bbox[0]))
    spanning_ids = {id(b) for b in spanning}

    # A page-wide heading divides the column flow into sections. Within each
    # section, read the left column before the right, then continue below the
    # spanning block.
    result: list[DocumentBlock] = []
    section_blocks = [b for b in blocks if id(b) not in spanning_ids]
    previous_bottom = 0.0
    for span in spanning:
        section = [
            b for b in section_blocks
            if (b.bbox[1] if b.bbox else 0.0) >= previous_bottom
            and (b.bbox[1] if b.bbox else 0.0) < span.bbox[1]
        ]
        result.extend(_sort_column_section(section, split))
        result.append(span)
        previous_bottom = span.bbox[3]

    section = [
        b for b in section_blocks
        if (b.bbox[1] if b.bbox else 0.0) >= previous_bottom
    ]
    result.extend(_sort_column_section(section, split))
    return result


def _sort_column_section(blocks: list[DocumentBlock], split: float) -> list[DocumentBlock]:
    """Read a vertical section of a two-column page, left column first."""
    left: list[DocumentBlock] = []
    right: list[DocumentBlock] = []
    for block in blocks:
        midpoint = (block.bbox[0] + block.bbox[2]) / 2 if block.bbox else 0.0
        (left if midpoint < split else right).append(block)
    key = lambda b: (b.bbox[1] if b.bbox else 0.0, b.bbox[0] if b.bbox else 0.0)
    return sorted(left, key=key) + sorted(right, key=key)
