"""Structure-aware chunking of parsed blocks.

Sections follow headings (target ~450 tokens). Tables, lists and equations are never split.
Each table yields a markdown chunk, a deterministic summary chunk and a structured grid.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from models.document import DocumentBlock
from rag import config, meta as M
from rag.tables import build_grid, table_summary

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])")


def tokens(text: str) -> int:
    return int(len(text.split()) * 1.3) + 1


@dataclass
class ChunkOut:
    kind: str
    text: str
    embed_text: str
    heading_path: list[str]
    block_ids: list[str]
    pages: list[int]
    printed_pages: list[str]
    bboxes: list[dict]
    min_confidence: float | None
    flags: list[str]
    meta: dict[str, Any]
    table_ref: str | None = None
    ord: int = 0

    @property
    def content_hash(self) -> str:
        h = hashlib.sha256()
        h.update(self.kind.encode())
        h.update(self.embed_text.encode())
        h.update(repr(sorted(self.meta.items())).encode())
        return h.hexdigest()


@dataclass
class TableOut:
    table_ref: str  # block id
    title: str
    page: int
    unit: str | None
    scale: float | None
    currency: str | None
    statement: str | None
    grid: dict[str, Any]
    printed: dict[str, str] = field(default_factory=dict)
    conf: float | None = None
    estimated: bool = False
    kind: str = "table"


@dataclass
class Built:
    chunks: list[ChunkOut] = field(default_factory=list)
    tables: list[TableOut] = field(default_factory=list)


def loc(b: DocumentBlock) -> dict:
    """Where to draw this block in the preview pages (DOCX/XLSX: metadata.preview)."""
    prev = b.metadata.get("preview") or {}
    return {
        "page": int(prev.get("page") or b.page),
        "bbox": list(prev["bbox"]) if prev.get("bbox") else (list(b.bbox) if b.bbox else None),
        "block_id": b.id,
    }


def _flags(blocks: list[DocumentBlock]) -> list[str]:
    out: list[str] = []
    for b in blocks:
        if b.requires_review:
            out.append("needs_review")
        for f in b.metadata.get("flags") or []:
            out.append(str(f))
        if b.metadata.get("values_estimated"):
            out.append("values_estimated")
        if b.confidence is not None and b.confidence < 0.6 and b.extractor == "rapidocr":
            out.append("low_ocr_confidence")
    return sorted(set(out))


def _min_conf(blocks: list[DocumentBlock]) -> float | None:
    vals = [b.confidence for b in blocks if b.confidence is not None]
    return round(min(vals), 4) if vals else None


def _page_map(blocks: list[DocumentBlock]) -> dict[int, str]:
    """Printed page numbers from running headers/footers."""
    found: dict[int, str] = {}
    for b in blocks:
        if b.type.value in ("footer", "header") or (b.bbox and (b.bbox[1] > 0.9 or b.bbox[3] < 0.1)):
            label = M.printed_page(b.content)
            if label:
                found.setdefault(int(b.metadata.get("preview", {}).get("page") or b.page), label)
    return found


def _split_long(text: str, limit: int) -> list[str]:
    """Split an over-long paragraph on sentence boundaries (never mid-sentence)."""
    pieces, cur = [], ""
    for sent in _SENTENCE.split(text):
        if cur and tokens(cur + " " + sent) > limit:
            pieces.append(cur)
            cur = sent
        else:
            cur = (cur + " " + sent).strip()
    if cur:
        pieces.append(cur)
    return pieces


def chart_table(b: DocumentBlock, data: dict) -> tuple[dict, str | None, float | None, str | None] | None:
    """A chart's series as a queryable grid: categories are rows, series are columns."""
    series = data.get("series") or []
    if not series or not any(isinstance(v, (int, float)) for s in series for v in s.get("values", [])):
        return None
    cats = data.get("categories") or []
    scatter = any(s.get("x_values") for s in series)
    n = max(len(s["values"]) for s in series)
    loc_ = loc(b)
    unit = M.detect_unit(" ".join([str(data.get("title", "")), str(data.get("y_label", "")), str(data.get("x_label", ""))]))
    rows = []
    for i in range(n):
        if scatter:
            xs = series[0].get("x_values") or []
            label = f"x={xs[i]:g}" if i < len(xs) and xs[i] is not None else f"point {i + 1}"
        else:
            label = str(cats[i]) if i < len(cats) and cats[i] else f"item {i + 1}"
        cells = [{"raw": label, "v": None, "pct": False, "page": loc_["page"], "bbox": loc_["bbox"], "exact_cell": False, "conf": b.confidence}]
        for s in series:
            v = s["values"][i] if i < len(s["values"]) else None
            cells.append({"raw": None if v is None else f"{v:g}", "v": None if v is None else float(v), "pct": False,
                          "page": loc_["page"], "bbox": loc_["bbox"], "exact_cell": False, "conf": b.confidence})
        rows.append({"ridx": i + 1, "label": label, "is_total": False, "cells": cells})
    def series_name(k: int, s: dict) -> str:
        name = s.get("name") or f"Series {k + 1}"
        if len(series) == 1 and re.fullmatch(r"Series \d+", name):
            return str(data.get("y_label") or data.get("title") or name)  # what the chart measures
        return name

    paths = ["Category"] + [series_name(k, s) for k, s in enumerate(series)]
    grid = {"header_rows": 1, "col_paths": paths, "col_periods": [None] * len(paths), "rows": rows, "merged": [],
            "n_cols": len(paths)}
    return grid, unit[0], unit[1], unit[2]


def build_chunks(blocks: list[DocumentBlock], doc_type: str, filename: str) -> Built:
    blocks = sorted(blocks, key=lambda b: (b.reading_order if b.reading_order is not None else 10**9))
    pmap = _page_map(blocks)
    built = Built()
    stack: list[tuple[int, str]] = []  # (level, title)
    cur_unit: tuple[str | None, float | None, str | None] = (None, None, None)
    cur_period: str | None = None
    caption = ""
    section: list[DocumentBlock] = []
    section_tokens = 0
    n = 0

    def heading_path() -> list[str]:
        return [t for _, t in stack]

    def base_meta(extra: dict | None = None) -> dict[str, Any]:
        m: dict[str, Any] = {"doc_type": doc_type, "filename": filename}
        if cur_period:
            m["period"] = cur_period
        if cur_unit[0]:
            m["unit"] = " ".join(x for x in (cur_unit[2], cur_unit[0]) if x)
        m.update(extra or {})
        return m

    def emit(kind: str, text: str, embed: str, blks: list[DocumentBlock], extra: dict | None = None,
             table_ref: str | None = None) -> None:
        nonlocal n
        locs = [loc(b) for b in blks]
        if kind in ("table", "table_summary") and blks and blks[0].metadata.get("part_bboxes"):
            # a table merged across pages: one box per page it occupies
            locs = [{"page": int(p["page"]), "bbox": list(p["bbox"]) if p.get("bbox") else None, "block_id": blks[0].id}
                    for p in blks[0].metadata["part_bboxes"]]
        pages = sorted({l["page"] for l in locs})
        n += 1
        hp = heading_path()
        built.chunks.append(ChunkOut(
            kind=kind, text=text, embed_text=(" > ".join(hp) + "\n" if hp else "") + embed[: config.EMBED_MAX_CHARS],
            heading_path=hp, block_ids=[b.id for b in blks], pages=pages,
            printed_pages=[pmap[p] for p in pages if p in pmap], bboxes=locs,
            min_confidence=_min_conf(blks), flags=_flags(blks), meta=base_meta(extra), table_ref=table_ref, ord=n,
        ))

    def flush_section() -> None:
        nonlocal section, section_tokens
        if not section:
            return
        text = "\n".join(b.metadata.get("markdown") or b.content for b in section if (b.content or "").strip())
        if text.strip():
            emit("section", text, text, section)
        section, section_tokens = [], 0

    for b in blocks:
        t = b.type.value
        content = (b.content or "").strip()

        if t in ("header", "footer"):
            continue

        if t == "heading":
            flush_section()
            level = int(b.metadata.get("heading_level") or 1)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, content[:160]))
            caption = ""
            u = M.unit_statement(content) if len(content) > 40 else M.detect_unit(content)
            if u[0]:
                cur_unit = u
            p = M.normalize_period(content) if re.search(r"(?:FY|Q[1-4]|20\d\d)", content, re.I) else None
            if p and len(content) < 80:
                cur_period = p
            continue

        if t in ("paragraph", "list"):
            if b.metadata.get("inline") and b.metadata.get("parent_block_id"):
                continue
            u = M.unit_statement(content)
            if u[0]:
                cur_unit = u
            if tokens(content) > config.MAX_CHUNK_TOKENS:
                flush_section()
                for piece in _split_long(content, config.TARGET_CHUNK_TOKENS):
                    emit("section", piece, piece, [b])
                continue
            if section and section_tokens + tokens(content) > config.TARGET_CHUNK_TOKENS:
                flush_section()
            section.append(b)
            section_tokens += tokens(content)
            if len(content) < 200 and t == "paragraph":
                caption = content
            continue

        if t == "table":
            flush_section()
            grid = build_grid(b)
            md = b.metadata.get("markdown") or b.content
            hp = heading_path()
            title = (hp[-1] if hp else "") or caption or str(b.metadata.get("sheet_name") or "")
            header_text = " ".join(grid["col_paths"]) if grid else ""
            unit = M.detect_unit(" ".join([caption, title, header_text, content[:300]]))
            unit = unit if unit[0] or unit[2] else cur_unit
            statement = M.detect_statement(" ".join(hp + [caption, header_text]))
            if grid:
                periods = [p for p in grid["col_periods"] if p]
                tpages = [int(p["page"]) for p in (b.metadata.get("part_bboxes") or [])] or [loc(b)["page"]]
                built.tables.append(TableOut(b.id, title, loc(b)["page"], unit[0], unit[1], unit[2], statement, grid,
                                             {str(p): pmap[p] for p in tpages if p in pmap}, b.confidence))
                summary = table_summary(grid, title, unit[0], unit[2], loc(b)["page"])
            else:
                periods, summary = [], f"Table on page {loc(b)['page']}."
            extra = {"unit": " ".join(x for x in (unit[2], unit[0]) if x) or None, "statement": statement,
                     "period": periods[0] if periods else cur_period, "table_id": b.id}
            extra = {k: v for k, v in extra.items() if v}
            emit("table", md, (title + "\n" if title else "") + md, [b], extra, table_ref=b.id)
            emit("table_summary", summary, summary, [b], extra, table_ref=b.id)
            caption = ""
            continue

        if t == "chart":
            flush_section()
            md = b.metadata.get("markdown") or content
            data = b.metadata.get("chart_data") or {}
            ct = chart_table(b, data)
            if ct is not None:
                built.tables.append(TableOut(b.id, data.get("title") or "", loc(b)["page"], ct[1], ct[2], ct[3], None, ct[0],
                                             {str(p): pmap[p] for p in [loc(b)["page"]] if p in pmap}, b.confidence,
                                             bool(data.get("values_estimated")), "chart"))
            emit("chart", md, md, [b], {"chart_type": b.metadata.get("chart_type"), "table_id": b.id,
                                         "estimated": bool(data.get("values_estimated"))}, table_ref=b.id if ct else None)
            continue

        if t == "equation":
            if b.metadata.get("inline") and b.metadata.get("parent_block_id"):
                continue
            flush_section()
            latex = b.metadata.get("latex") or content
            text = f"Equation: ${latex}$" + (f" (context: {caption})" if caption else "")
            emit("equation", text, text, [b], {"latex": latex})
            continue

        if t == "figure" and content and content not in ("[image]", "[embedded image]"):
            alt = b.metadata.get("alt_text")
            if alt:
                section.append(b)
            continue

    flush_section()
    return built
