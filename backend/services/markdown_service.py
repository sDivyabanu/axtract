"""Markdown generation from ordered semantic blocks.

Converts the final reading-ordered DocumentBlocks into clean Markdown.
Uses the blocks' reading_order (from layout_service) when available.
"""

from __future__ import annotations

from models.document import BlockType, DocumentBlock


def blocks_to_markdown(blocks: list[DocumentBlock]) -> str:
    """Convert an ordered list of DocumentBlocks to Markdown."""
    if not blocks:
        return ""

    # Sort by reading_order if assigned, otherwise by page then block id
    sorted_blocks = sorted(
        blocks,
        key=lambda b: (
            b.reading_order if b.reading_order is not None else 999999,
            b.page,
        ),
    )

    parts: list[str] = []
    current_page = -1

    for block in sorted_blocks:
        # Page break marker
        if block.page != current_page:
            if current_page > 0:
                parts.append("")  # blank line before page break
                parts.append(f"---")
                parts.append("")
            current_page = block.page

        md = _block_to_markdown(block)
        if md:
            parts.append(md)
            parts.append("")  # blank line after each block

    return "\n".join(parts).strip() + "\n"


def _block_to_markdown(block: DocumentBlock) -> str:
    """Convert a single block to its Markdown representation."""
    content = block.content.strip()
    if not content:
        return ""

    match block.type:
        case BlockType.HEADING:
            level = block.metadata.get("heading_level", 1)
            level = max(1, min(6, level))
            return f"{'#' * level} {content}"

        case BlockType.PARAGRAPH:
            return content

        case BlockType.LIST:
            # Preserve list formatting
            lines = content.split("\n")
            result: list[str] = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                # Already has bullet/number prefix
                if line.startswith(("- ", "* ", "• ")):
                    result.append(f"- {line[2:]}")
                elif line.startswith(("– ", "— ")):
                    result.append(f"- {line[2:]}")
                elif len(line) > 2 and line[0].isdigit() and line[1] in ".)" :
                    result.append(f"- {line[2:].strip()}")
                else:
                    result.append(f"- {line}")
            return "\n".join(result)

        case BlockType.TABLE:
            return _table_to_markdown(block)

        case BlockType.FIGURE:
            caption = block.metadata.get("caption", "")
            if caption:
                return f"![{caption}](figure-p{block.page})\n\n*{caption}*"
            return f"![Figure on page {block.page}](figure-p{block.page})"

        case BlockType.CHART:
            chart_type = block.metadata.get("chart_type", "chart")
            return f"*[{chart_type}: {content}]*"

        case BlockType.EQUATION:
            # Wrap in LaTeX delimiters if it looks like LaTeX
            if "\\" in content or "{" in content:
                return f"$$\n{content}\n$$"
            return f"$${content}$$"

        case BlockType.HEADER:
            return f"*{content}*"

        case BlockType.FOOTER:
            return f"*{content}*"

        case BlockType.UNKNOWN:
            if content:
                return f"> {content}"
            return ""

        case _:
            return content


def _table_to_markdown(block: DocumentBlock) -> str:
    """Convert a table block to Markdown table format with merged cell support."""
    rows = block.metadata.get("rows")
    if not rows or not isinstance(rows, list):
        # Fallback: use pipe-delimited content
        return f"```\n{block.content}\n```"

    # Check for merged cells info
    merged_info = block.metadata.get("merged_cells", {})
    has_merged = merged_info.get("has_merged_cells", False)
    
    # Build Markdown table
    lines: list[str] = []
    
    # Track merged cell spans
    merged_spans = {}  # (row, col) -> (rowspan, colspan)
    for region in merged_info.get("merged_regions", []):
        row, col, rowspan, colspan = region
        merged_spans[(row, col)] = (rowspan, colspan)

    for i, row in enumerate(rows):
        if not isinstance(row, list):
            continue
        cells = [str(c) if c is not None else "" for c in row]
        
        # Skip cells that are covered by merged cells from previous rows
        display_cells = []
        for j, cell in enumerate(cells):
            # Check if this cell is covered by a rowspan from above
            is_covered = False
            for (mr, mc), (rowspan, colspan) in merged_spans.items():
                if mr < i <= mr + rowspan - 1 and mc <= j < mc + colspan:
                    is_covered = True
                    break
            
            if is_covered:
                display_cells.append("")
            else:
                # Escape pipe characters in cell content
                display_cells.append(cell.replace("|", "\\|"))
        
        # Add colspan indicator in content for merged cells
        for j, cell in enumerate(display_cells):
            if (i, j) in merged_spans:
                rowspan, colspan = merged_spans[(i, j)]
                if colspan > 1:
                    display_cells[j] = f"{cell} <colspan:{colspan}>"
                if rowspan > 1:
                    display_cells[j] = f"{cell} <rowspan:{rowspan}>"
        
        lines.append("| " + " | ".join(display_cells) + " |")

        # Add separator after header row
        if i == 0:
            sep = "| " + " | ".join("---" for _ in display_cells) + " |"
            lines.append(sep)

    return "\n".join(lines)
