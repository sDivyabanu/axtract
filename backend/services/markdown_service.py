"""Markdown generation from ordered semantic blocks.

Converts the final reading-ordered DocumentBlocks into clean Markdown.
Uses the blocks' reading_order (from layout_service) when available.
"""

from __future__ import annotations

import re
from html import escape

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

    # Expose each block's Markdown so clients (filtered view) never re-implement rendering.
    for block in sorted_blocks:
        block.metadata["markdown"] = _block_to_markdown(block)

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
    if ((block.extractor == "rapidocr" or block.metadata.get("invisible_ocr_source")) and block.type == BlockType.PARAGRAPH
            and block.metadata.get("math_replaced_in_markdown")):
        return ""  # exact OCR content is retained in the structured source block
    content = block.content.strip()
    if not content and block.type != BlockType.TABLE:
        return ""
    if block.metadata.get("markdown_verbatim"):
        # VLM blocks already hold Markdown (LaTeX math, HTML tables); headers/footers stay out of the body.
        if block.type in (BlockType.HEADER, BlockType.FOOTER):
            return ""
        if block.type == BlockType.HEADING:
            return f"{'#' * max(1, min(6, block.metadata.get('heading_level', 2)))} {content}"
        return content
    if block.extractor == "rapidocr" and block.type == BlockType.PARAGRAPH:
        from extractors.ocr_extractor import restore_punctuation_spacing

        # applied to the written text only, so equation-candidate selection still sees the raw OCR line
        content = restore_punctuation_spacing(content)
    # A PDF can contain isolated dollar signs as glyphs or OCR noise. Emitting
    # those as Markdown math delimiters can make downstream renderers treat a
    # large span of ordinary text as an equation. The character reference
    # renders as a literal dollar while keeping the Markdown source unambiguous.
    content = re.sub(r"(?m)^[ \t]*\$[ \t]*$", "&#36;", content)

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
            return _chart_to_markdown(block)

        case BlockType.EQUATION:
            if block.metadata.get("inline") and block.metadata.get("parent_block_id"):
                return ""  # already part of its paragraph's text
            latex = block.metadata.get("latex") or content
            suffix = "  <!-- needs review -->" if block.requires_review else ""
            return f"$$\n{latex}\n$${suffix}"

        case BlockType.HEADER:
            # Keep page furniture in the structured response, but omit it from
            # body Markdown so running headers do not contaminate extracted text.
            return ""

        case BlockType.FOOTER:
            return ""

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

    # Each cell must occupy one physical line; keep missing column positions.
    rows = [row for row in rows if isinstance(row, list)]
    width = max((len(row) for row in rows), default=0)
    if not width:
        return block.content
    rows = [[" ".join(str(cell).split()) if cell is not None else ""
             for cell in row] + [""] * (width - len(row)) for row in rows]

    # Only explicit extractor spans are structural evidence. Missing cells can
    # also be genuine blanks and must not change their neighbours' coordinates.
    info = block.metadata.get("merged_cells") or {}
    if not isinstance(info, dict):
        info = {}
    spans: dict[tuple[int, int], tuple[int, int]] = {}
    covered: set[tuple[int, int]] = set()
    if info.get("source") == "extractor":
        for region in info.get("merged_regions", []):
            if not isinstance(region, (list, tuple)) or len(region) != 4:
                continue
            r, c, rs, cs = region
            if (not all(isinstance(v, int) for v in region) or min(r, c) < 0
                    or min(rs, cs) < 1 or r+rs > len(rows) or c+cs > width):
                continue
            cells = {(rr, cc) for rr in range(r, r+rs) for cc in range(c, c+cs)}
            if cells & covered or cells & spans.keys():
                continue
            rest = cells - {(r, c)}
            if any(rows[rr][cc] for rr, cc in rest):
                continue  # Preserve content even if span metadata is inconsistent.
            spans[(r, c)] = (rs, cs)
            covered.update(rest)

    # HTML is standard Markdown content and expresses real spans and literal
    # pipes without inventing suffixes such as <colspan:2> in the cell value.
    # Leading header rows: 1 unless the extractor reported more (multi-row headers from an HTML source).
    header_rows = block.metadata.get("header_rows")
    header_rows = header_rows if isinstance(header_rows, int) and 1 <= header_rows < len(rows) else 1
    if spans or header_rows > 1 or any("|" in cell for row in rows for cell in row):
        lines = ["<table>"]
        for r, row in enumerate(rows):
            cells = []
            for c, cell in enumerate(row):
                if (r, c) in covered:
                    continue
                tag = "th" if r < header_rows or c == 0 else "td"
                rs, cs = spans.get((r, c), (1, 1))
                attrs = (f' rowspan="{rs}"' if rs > 1 else "") + (f' colspan="{cs}"' if cs > 1 else "")
                cells.append(f"<{tag}{attrs}>{escape(cell)}</{tag}>")
            lines.append("<tr>" + "".join(cells) + "</tr>")
        return "\n".join(lines + ["</table>"])

    lines = []
    for r, row in enumerate(rows):
        lines.append("| " + " | ".join(row) + " |")
        if r == 0:
            lines.append("| " + " | ".join("---" for _ in row) + " |")
    return "\n".join(lines)



def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _chart_to_markdown(block: DocumentBlock) -> str:
    """A chart as its title plus the data table (values are what downstream models need)."""
    data = block.metadata.get("chart_data") or {}
    series = data.get("series") or []
    title = data.get("title") or "Chart"
    kind = data.get("chart_type") or "chart"
    note = ""
    if data.get("values_estimated"):
        note = " (values estimated from the image)"
    if block.requires_review:
        note += " (needs review)"
    head = f"**{title}** — {kind} chart{note}"
    if not series:
        return head

    cats = data.get("categories") or []
    lines = [head, ""]
    if any(s.get("x_values") for s in series):  # scatter: x / y pairs
        for s in series:
            lines += [f"| x | {s['name']} |", "| --- | --- |"]
            for x, y in zip(s.get("x_values") or [], s["values"]):
                lines.append(f"| {_fmt(x)} | {_fmt(y)} |")
            lines.append("")
        return "\n".join(lines).rstrip()

    n = max(len(s["values"]) for s in series)
    labels = [cats[i] if i < len(cats) and cats[i] else str(i + 1) for i in range(n)]
    lines.append("| | " + " | ".join(s["name"] for s in series) + " |")
    lines.append("| --- |" + " --- |" * len(series))
    for i in range(n):
        row = [_fmt(s["values"][i]) if i < len(s["values"]) else "" for s in series]
        lines.append(f"| {labels[i]} | " + " | ".join(row) + " |")
    return "\n".join(lines)
