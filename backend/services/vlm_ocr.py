"""Vision-language OCR engine output handling.

The model (LightOnOCR-3) transcribes a rendered page image into Markdown. With the "grounding" prompt it prefixes every layout
block with a placeholder `![label](x0,y0,x1,y1)` whose box is predicted by the model on a 0-1000 grid of the page image. These
helpers parse that reply into labelled blocks and turn it into the Markdown AXTRACT emits. Running headers, footers and page
numbers are kept as blocks (for the JSON output and provenance) but left out of the body Markdown, as the rest of AXTRACT does.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_FENCE = re.compile(r"^```(?:markdown|md)?\s*\n(.*)\n```\s*$", re.S)
_PLACEHOLDER = re.compile(r"^!\[([A-Za-z_ -]+)\]\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)[ \t]*", re.M)

MARGIN_LABELS = {"header", "page_header", "footer", "page_footer", "page_number", "page-header", "page-footer", "page-number"}


@dataclass
class GroundedBlock:
    label: str
    bbox: tuple[float, float, float, float]  # fractions of page width/height, as predicted by the model
    content: str


def _strip_fence(text: str) -> str:
    text = (text or "").strip()
    m = _FENCE.match(text)
    return m.group(1).strip() if m else text


def parse_grounded(text: str) -> list[GroundedBlock] | None:
    """Split a grounding-mode reply into blocks; None when the reply carries no placeholders."""
    text = _strip_fence(text)
    marks = list(_PLACEHOLDER.finditer(text))
    if not marks:
        return None
    blocks = []
    lead = text[: marks[0].start()].strip()
    if lead:
        blocks.append(GroundedBlock("text", (0.0, 0.0, 1.0, 1.0), lead))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        x0, y0, x1, y1 = (min(max(int(v), 0), 1000) / 1000 for v in m.groups()[1:])
        blocks.append(GroundedBlock(m.group(1).strip().lower(), (x0, y0, x1, y1), text[m.end():end].strip()))
    return blocks


def is_margin(block: GroundedBlock) -> bool:
    return block.label in MARGIN_LABELS


def blocks_to_body_markdown(blocks: list[GroundedBlock]) -> str:
    parts = []
    for b in blocks:
        if is_margin(b) or not b.content:
            continue
        parts.append(b.content)
    return "\n\n".join(parts)


def postprocess_markdown(text: str) -> str:
    blocks = parse_grounded(text)
    body = blocks_to_body_markdown(blocks) if blocks is not None else _strip_fence(text)
    body = body.strip()
    return body + "\n" if body else ""


class _TableParser:
    """Minimal HTML table reader: rows of cell text plus explicit row/column spans (no other markup is trusted)."""

    def __init__(self):
        from html.parser import HTMLParser

        outer = self

        class P(HTMLParser):
            def handle_starttag(self, tag, attrs):
                outer.start(tag, dict(attrs))

            def handle_endtag(self, tag):
                outer.end(tag)

            def handle_data(self, data):
                if outer.cell is not None:
                    outer.cell.append(data)

        self.parser = P(convert_charrefs=True)
        self.rows: list[list[tuple[str, int, int]]] = []
        self.cell: list[str] | None = None
        self.span = (1, 1)
        self.tables = 0
        self.in_head = False
        self.head_row: list[bool] = []  # per row: inside <thead> or made only of <th> cells
        self.th_only: list[bool] = []

    def start(self, tag, attrs):
        if tag == "table":
            self.tables += 1
        elif tag == "thead":
            self.in_head = True
        elif tag in ("tbody", "tfoot"):
            self.in_head = False
        elif tag == "tr":
            self.rows.append([])
            self.head_row.append(self.in_head)
            self.th_only.append(True)
        elif tag in ("td", "th"):
            if not self.rows:
                self.rows.append([])
                self.head_row.append(self.in_head)
                self.th_only.append(True)
            self.th_only[-1] &= tag == "th"
            self.cell = []

            def num(key):
                try:
                    return max(1, min(100, int(attrs.get(key) or 1)))
                except ValueError:
                    return 1
            self.span = (num("rowspan"), num("colspan"))
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def end(self, tag):
        if tag == "thead":
            self.in_head = False
        if tag in ("td", "th") and self.cell is not None:
            self.rows[-1].append((" ".join("".join(self.cell).split()), *self.span))
            self.cell = None


def html_table_to_grid(html: str) -> tuple[list[list[str]], list[list[int]], int] | None:
    """Expand one HTML table into a rectangular grid of cell texts, its merged regions [row, col, rowspan, colspan], and the
    number of leading header rows (rows in <thead> or made only of <th> cells).

    Returns None when the text holds no table or more than one table."""
    tp = _TableParser()
    try:
        tp.parser.feed(html)
        tp.parser.close()
    except Exception:  # noqa: BLE001 - malformed markup: let the caller keep the text as it is
        return None
    if tp.tables != 1 or not any(tp.rows):
        return None
    grid: dict[tuple[int, int], str] = {}
    merged: list[list[int]] = []
    for r, row in enumerate(tp.rows):
        c = 0
        for text, rs, cs in row:
            while (r, c) in grid:
                c += 1
            for rr in range(r, r + rs):
                for cc in range(c, c + cs):
                    grid[(rr, cc)] = ""
            grid[(r, c)] = text
            if rs > 1 or cs > 1:
                merged.append([r, c, rs, cs])
            c += cs
    n_rows = max(r for r, _ in grid) + 1
    n_cols = max(c for _, c in grid) + 1
    rows = [[grid.get((r, c), "") for c in range(n_cols)] for r in range(n_rows)]
    merged = [m for m in merged if m[0] + m[2] <= n_rows and m[1] + m[3] <= n_cols]
    header_rows = 0
    for is_head, only_th in zip(tp.head_row, tp.th_only):
        if not (is_head or only_th):
            break
        header_rows += 1
    return rows, merged, min(header_rows, n_rows)
