"""Extractors for text-markup formats: TXT, Markdown, HTML, RTF and CSV.

All are stdlib-only except RTF (striprtf, with a regex fallback), and all
produce the same typed blocks as the binary extractors.
"""

from __future__ import annotations

import csv
import re
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path
from typing import ClassVar

from extractors.base import BaseExtractor, ExtractionResult
from models.document import BlockType, DocumentBlock

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_MD_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def _block(bid: str, btype: BlockType, content: str, page: int, **kw) -> DocumentBlock:
    return DocumentBlock(
        id=bid, type=btype, content=content.strip(), page=page,
        bbox=None, confidence=None, extractor="text", reading_order=None,
        requires_review=False, metadata={}, **kw,
    )


class TextExtractor(BaseExtractor):
    """Plain text (.txt) and Markdown (.md)."""

    name: ClassVar[str] = "text"
    supported_extensions: ClassVar[frozenset[str]] = frozenset({"txt", "md", "markdown"})

    def extract(self, file_path: Path) -> ExtractionResult:
        text = file_path.read_text(encoding="utf-8", errors="replace")
        blocks: list[DocumentBlock] = []
        list_buf: list[str] = []
        idx = 0

        def flush_list() -> None:
            nonlocal idx
            if list_buf:
                blocks.append(_block(f"b{idx}", BlockType.LIST, "\n".join(list_buf), 1))
                idx += 1
                list_buf.clear()

        for raw in text.splitlines():
            line = raw.rstrip()
            md = _MD_HEADING_RE.match(line)
            if md:
                flush_list()
                blocks.append(_block(f"b{idx}", BlockType.HEADING, md.group(2), 1))
                blocks[-1].metadata["heading_level"] = len(md.group(1))
                idx += 1
            elif _MD_LIST_RE.match(line) and line.strip():
                list_buf.append(line.strip())
            elif line.strip():
                flush_list()
                blocks.append(_block(f"b{idx}", BlockType.PARAGRAPH, line, 1))
                idx += 1
            else:
                flush_list()
        flush_list()

        if not blocks:
            blocks.append(_block("b0", BlockType.PARAGRAPH, text.strip() or "(empty file)", 1))
        return ExtractionResult(page_count=1, blocks=blocks)


class _HtmlTextParser(HTMLParser):
    _HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str, int]] = []  # (kind, text, level)
        self._kind: str | None = None
        self._level = 0
        self._buf: list[str] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self.title = ""

    def handle_starttag(self, tag, attrs) -> None:
        tag = tag.lower()
        if tag in self._HEADINGS:
            self._flush()
            self._kind, self._level = "heading", self._HEADINGS[tag]
        elif tag in ("p", "li"):
            self._flush()
            self._kind = "li" if tag == "li" else "p"
        elif tag == "table":
            self._flush()
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "title" and not self.title:
            self._kind = "title"

    def handle_endtag(self, tag) -> None:
        tag = tag.lower()
        if tag == "table" and self._table is not None:
            if self._row and self._cell is not None:
                self._row.append(" ".join("".join(self._cell).split()))
                self._cell = None
            if self._row:
                self._table.append(self._row)
                self._row = None
            self.blocks.append(("table", self._table, 0))
            self._table = None
        elif tag == "tr" and self._row is not None:
            if self._cell is not None:
                self._row.append(" ".join("".join(self._cell).split()))
                self._cell = None
            self._table.append(self._row)  # type: ignore[union-attr]
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))  # type: ignore[union-attr]
            self._cell = None
        elif self._kind:
            self._flush()

    def handle_data(self, data) -> None:
        if self._kind or self._cell is not None:
            self._buf.append(data)
            if self._cell is not None:
                self._cell.append(data)

    def _flush(self) -> None:
        text = " ".join("".join(self._buf).split())
        self._buf.clear()
        if self._kind == "title":
            self.title = text
        elif self._kind == "heading" and text:
            self.blocks.append(("heading", text, self._level))
        elif self._kind == "li" and text:
            self.blocks.append(("li", text, 0))
        elif self._kind == "p" and text:
            self.blocks.append(("p", text, 0))
        self._kind = None
        self._level = 0


class HtmlExtractor(BaseExtractor):
    """HTML (.html/.htm) via the stdlib parser."""

    name: ClassVar[str] = "html"
    supported_extensions: ClassVar[frozenset[str]] = frozenset({"html", "htm"})

    def extract(self, file_path: Path) -> ExtractionResult:
        raw = file_path.read_text(encoding="utf-8", errors="replace")
        parser = _HtmlTextParser()
        parser.feed(raw)
        parser.close()

        blocks: list[DocumentBlock] = []
        list_buf: list[str] = []
        idx = 0

        def flush_list() -> None:
            nonlocal idx
            if list_buf:
                blocks.append(_block(f"b{idx}", BlockType.LIST, "\n".join(list_buf), 1))
                idx += 1
                list_buf.clear()

        for kind, payload, level in parser.blocks:
            if kind == "heading":
                flush_list()
                blocks.append(_block(f"b{idx}", BlockType.HEADING, payload, 1))
                blocks[-1].metadata["heading_level"] = max(1, level)
                idx += 1
            elif kind == "li":
                list_buf.append(f"- {payload}")
            elif kind == "p":
                flush_list()
                blocks.append(_block(f"b{idx}", BlockType.PARAGRAPH, payload, 1))
                idx += 1
            elif kind == "table":
                flush_list()
                rows = payload
                if rows:
                    content = "\n".join(" | ".join(r) for r in rows)
                    blocks.append(_block(f"b{idx}", BlockType.TABLE, content, 1))
                    blocks[-1].metadata = {
                        "rows": rows, "row_count": len(rows),
                        "col_count": max(len(r) for r in rows),
                        "header": rows[0],
                    }
                    idx += 1
        flush_list()

        if not blocks:
            blocks.append(_block("b0", BlockType.PARAGRAPH, parser.title or "(empty document)", 1))
        return ExtractionResult(page_count=1, blocks=blocks)


class RtfExtractor(BaseExtractor):
    """RTF (.rtf). Uses striprtf when available; falls back to a minimal strip."""

    name: ClassVar[str] = "rtf"
    supported_extensions: ClassVar[frozenset[str]] = frozenset({"rtf"})

    def extract(self, file_path: Path) -> ExtractionResult:
        raw = file_path.read_text(encoding="utf-8", errors="replace")
        try:
            from striprtf.striprtf import rtf_to_text
            text = rtf_to_text(raw)
        except ImportError:
            text = re.sub(r"\\[a-z]+-?\d*\s?", "\n", raw)
            text = text.replace("{", "").replace("}", "").strip()
        # striprtf loses paragraph structure; rebuild from non-empty lines
        delegate = TextExtractor()
        tmp = file_path.with_suffix(".extract-tmp.txt")
        try:
            tmp.write_text(text, encoding="utf-8")
            return delegate.extract(tmp)
        finally:
            tmp.unlink(missing_ok=True)


class CsvExtractor(BaseExtractor):
    """CSV (.csv) — one table block per file."""

    name: ClassVar[str] = "csv"
    supported_extensions: ClassVar[frozenset[str]] = frozenset({"csv"})

    def extract(self, file_path: Path) -> ExtractionResult:
        sample = file_path.read_bytes()[:4096].decode("utf-8", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel  # type: ignore[assignment]
        with file_path.open(newline="", encoding="utf-8", errors="replace") as f:
            rows = [[("" if c is None else str(c)) for c in row] for row in csv.reader(f, dialect)]

        rows = [r for r in rows if any(c.strip() for c in r)]
        if not rows:
            raise ValueError("CSV contains no data rows.")
        content = "\n".join(" | ".join(c for c in r) for r in rows)
        block = _block("csv-b0", BlockType.TABLE, content, 1)
        block.metadata = {
            "rows": rows, "row_count": len(rows),
            "col_count": max(len(r) for r in rows),
            "header": rows[0],
        }
        return ExtractionResult(page_count=1, blocks=[block])
