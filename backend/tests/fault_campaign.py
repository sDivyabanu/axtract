"""Fault-injection campaign: corrupt a REAL, clean extraction in one specific way and see whether
AXTRACT Verify notices. A fault is "detected" when the report is no longer clean (status not VERIFIED),
it holds a blocking issue the clean report did not, and that issue carries one of the expected codes.

Silent-loss rate on injected faults = missed / injected. Faults that no independent evidence could ever
reveal (e.g. wrong OCR text on a scanned page) are listed separately: for those the correct outcome is
NOT_VERIFIABLE, which is checked instead of detection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from models.document import BlockType as T, DocumentResponse
from tests.verify_cases import Case, first, issue_set, mutate, sync_table_content, tables
from tests.verify_fixtures import (
    make_docx_campaign, make_pdf, make_pdf_numbers, make_pptx_campaign, make_xlsx_cached,
)
from verify.models import ValidationStatus

BLOCKING = ("medium", "high", "critical")


@dataclass
class Fault:
    name: str
    case: str
    expect: set[str]
    apply: Callable[[DocumentResponse], None]


@dataclass
class Result:
    fault: Fault
    detected: bool
    codes: set[str] = field(default_factory=set)
    status: str = ""
    matched_expectation: bool = False


def build_cases(tmp: Path) -> dict[str, Case]:
    return {
        "docx": Case.of(make_docx_campaign(tmp / "c.docx")),
        "pptx": Case.of(make_pptx_campaign(tmp / "c.pptx")),
        "xlsx": Case.of(make_xlsx_cached(tmp / "c.xlsx")),
        "pdf_text": Case.of(make_pdf_numbers(tmp / "t.pdf")),
        "pdf_table": Case.of(make_pdf(tmp / "tb.pdf", pages=2, image=True)),
    }


# ----------------------------------------------------------------------------------- helpers


def _text(r, needle):
    return first(r, lambda b: needle in b.content and b.type != T.TABLE)


def _sub(needle, old, new):
    def f(r):
        b = _text(r, needle)
        b.content = b.content.replace(old, new, 1)
    return f


def _cell(table_index, row, col, value):
    def f(r):
        t = tables(r)[table_index]
        t.metadata["rows"][row][col] = value
        sync_table_content(t)
    return f


def _swap_rows(table_index, i, j):
    def f(r):
        t = tables(r)[table_index]
        rows = t.metadata["rows"]
        rows[i], rows[j] = rows[j], rows[i]
        sync_table_content(t)
    return f


def _swap_cols(table_index, i, j):
    def f(r):
        t = tables(r)[table_index]
        for row in t.metadata["rows"]:
            row[i], row[j] = row[j], row[i]
        sync_table_content(t)
    return f


def _drop_last_row(table_index):
    def f(r):
        t = tables(r)[table_index]
        t.metadata["rows"].pop()
        t.metadata["row_count"] = len(t.metadata["rows"])
        sync_table_content(t)
    return f


def _merge(table_index, regions):
    def f(r):
        tables(r)[table_index].metadata["merged_cells"] = {"has_merged_cells": bool(regions), "merged_regions": regions}
    return f


def _flatten(table_index):
    def f(r):
        t = tables(r)[table_index]
        t.type, t.metadata = T.PARAGRAPH, {}
    return f


def _drop(pred):
    def f(r):
        r.blocks = [b for b in r.blocks if not pred(b)]
    return f


def _swap_blocks(needle_a, needle_b):
    def f(r):
        a, b = _text(r, needle_a), _text(r, needle_b)
        ia, ib = r.blocks.index(a), r.blocks.index(b)
        r.blocks[ia], r.blocks[ib] = b, a
        a.reading_order, b.reading_order = b.reading_order, a.reading_order
    return f


def _type(needle, to):
    def f(r):
        _text(r, needle).type = to
    return f


def _xl(row, col, value):
    def f(r):
        first(r, lambda b: b.metadata.get("sheet_name") == "Sales").metadata["rows"][row][col] = value
    return f


def _xl_swap_rows(i, j):
    def f(r):
        rows = first(r, lambda b: b.metadata.get("sheet_name") == "Sales").metadata["rows"]
        rows[i], rows[j] = rows[j], rows[i]
    return f


def _xl_swap_cols(i, j):
    def f(r):
        for row in first(r, lambda b: b.metadata.get("sheet_name") == "Sales").metadata["rows"]:
            row[i], row[j] = row[j], row[i]
    return f


def _chart_placeholder(r):
    c = first(r, lambda b: b.type == T.CHART)
    c.type, c.content, c.metadata = T.FIGURE, "[chart: no readable data]", {"media": "native_chart", "slide_number": c.page}


TEXT_CODES = {"numeric_value_mismatch"}
WORD_CODES = {"content_mismatch", "content_words_missing", "unexpected_words"}

FAULTS: list[Fault] = [
    # ---- DOCX ----
    Fault("digit changed in a paragraph ($18.2M -> $13.2M)", "docx", TEXT_CODES, _sub("Revenue was", "18.2", "13.2")),
    Fault("percentage changed (12.5% -> 15.2%)", "docx", TEXT_CODES, _sub("Revenue was", "12.5%", "15.2%")),
    Fault("word substituted (growth -> grwth)", "docx", WORD_CODES, _sub("continued growth", "growth", "grwth")),
    Fault("word dropped", "docx", WORD_CODES, _sub("continued growth", "continued ", "")),
    Fault("text invented and appended", "docx", WORD_CODES, _sub("Management expects", "region.", "region and an invented acquisition in Asia.")),
    Fault("paragraph removed", "docx", {"possible_missing_text"}, _drop(lambda b: "Operating costs" in b.content)),
    Fault("two paragraphs swapped", "docx", {"reading_order_mismatch"}, _swap_blocks("Revenue was", "Management expects")),
    Fault("heading extracted as paragraph", "docx", {"object_type_mismatch"}, _type("Annual Report", T.PARAGRAPH)),
    Fault("list item extracted as paragraph", "docx", {"object_type_mismatch"}, _type("First list entry", T.PARAGRAPH)),
    Fault("table cell digit changed", "docx", {"cell_value_mismatch"}, _cell(0, 2, 1, "31")),
    Fault("table cell text changed", "docx", {"cell_value_mismatch"}, _cell(0, 1, 0, "Nroth")),
    Fault("table rows swapped", "docx", {"table_row_order_changed"}, _swap_rows(0, 1, 3)),
    Fault("table columns swapped", "docx", {"table_column_order_changed"}, _swap_cols(0, 1, 2)),
    Fault("table last row dropped", "docx", {"table_dimensions_mismatch"}, _drop_last_row(0)),
    Fault("merged cells lost", "docx", {"merged_cells_mismatch"}, _merge(1, [])),
    Fault("merged cells corrupted (wrong span)", "docx", {"merged_cells_mismatch"}, _merge(1, [(0, 0, 1, 3), (1, 2, 2, 1)])),
    Fault("table flattened into paragraphs", "docx", {"table_flattened_into_text"}, _flatten(0)),
    Fault("table removed", "docx", {"possible_missing_table"}, lambda r: r.blocks.remove(tables(r)[0])),
    # ---- PPTX ----
    Fault("digit changed in a text shape", "pptx", TEXT_CODES, _sub("Revenue was", "18.2", "13.2")),
    Fault("text shape removed", "pptx", {"possible_missing_text"}, _drop(lambda b: "Revenue was" in b.content)),
    Fault("title extracted as paragraph", "pptx", {"object_type_mismatch"}, _type("Quarterly Results", T.PARAGRAPH)),
    Fault("table cell digit changed", "pptx", {"cell_value_mismatch"}, _cell(0, 2, 2, "41")),
    Fault("table rows swapped", "pptx", {"table_row_order_changed"}, _swap_rows(0, 1, 2)),
    Fault("table columns swapped", "pptx", {"table_column_order_changed"}, _swap_cols(0, 1, 2)),
    Fault("table last row dropped", "pptx", {"table_dimensions_mismatch"}, _drop_last_row(0)),
    Fault("merged cells lost", "pptx", {"merged_cells_mismatch"}, _merge(1, [])),
    Fault("table flattened into paragraphs", "pptx", {"table_flattened_into_text"}, _flatten(0)),
    Fault("chart reduced to an image placeholder", "pptx", {"chart_not_structured"}, _chart_placeholder),
    Fault("chart removed", "pptx", {"possible_missing_chart"}, _drop(lambda b: b.type == T.CHART)),
    # ---- XLSX ----
    Fault("numeric cell digit changed (20 -> 21)", "xlsx", {"cell_value_mismatch"}, _xl(1, 2, "21")),
    Fault("decimal changed (60.5 -> 60.6)", "xlsx", {"cell_value_mismatch"}, _xl(3, 2, "60.6")),
    Fault("text cell changed", "xlsx", {"cell_value_mismatch"}, _xl(1, 0, "Nroth")),
    Fault("text cell '007' became '7'", "xlsx", {"cell_value_mismatch"}, _xl(2, 5, "7")),
    Fault("formula's stored result changed", "xlsx", {"formula_result_mismatch"}, _xl(1, 3, "31")),
    Fault("date changed", "xlsx", {"cell_value_mismatch"}, _xl(0, 5, "2024-01-03 00:00:00")),
    Fault("boolean flipped", "xlsx", {"cell_value_mismatch"}, _xl(1, 5, "False")),
    Fault("cell value removed", "xlsx", {"possible_missing_cells"}, _xl(2, 1, "")),
    Fault("value invented in an empty cell", "xlsx", {"unexpected_cell_value"}, _xl(0, 6, "ghost")),
    Fault("sheet removed", "xlsx", {"possible_missing_sheet"}, _drop(lambda b: b.metadata.get("sheet_name") == "Costs")),
    Fault("rows swapped", "xlsx", {"table_row_order_changed"}, _xl_swap_rows(1, 2)),
    Fault("columns swapped", "xlsx", {"table_column_order_changed"}, _xl_swap_cols(1, 2)),
    Fault("merged range lost", "xlsx", {"possible_missing_merged_range"}, lambda r: first(r, lambda b: b.metadata.get("sheet_name") == "Sales").metadata.update(merged_cells={"has_merged_cells": False, "merged_regions": []})),
    Fault("merged range invented", "xlsx", {"merged_cells_mismatch"}, lambda r: first(r, lambda b: b.metadata.get("sheet_name") == "Sales").metadata["merged_cells"]["merged_regions"].append((2, 0, 1, 2))),
    # ---- PDF (text) ----
    Fault("digit changed in a paragraph", "pdf_text", TEXT_CODES, _sub("Revenue was", "18.2", "13.2")),
    Fault("percentage digit changed", "pdf_text", TEXT_CODES, _sub("Margins improved", "3.5%", "3.8%")),
    Fault("word dropped", "pdf_text", WORD_CODES, _sub("Headcount reached", "worldwide", "")),
    Fault("word substituted (employees -> employes)", "pdf_text", WORD_CODES, _sub("Headcount reached", "employees", "employes")),
    Fault("paragraph removed", "pdf_text", {"possible_missing_text", "content_words_missing"}, _drop(lambda b: "Operating costs" in b.content)),
    Fault("two paragraphs swapped", "pdf_text", {"reading_order_mismatch"}, _swap_blocks("Revenue was", "Capital spending")),
    # ---- PDF (table / image) ----
    Fault("table removed", "pdf_table", {"possible_missing_table"}, lambda r: r.blocks.remove(tables(r)[0])),
    Fault("table cell digit changed", "pdf_table", TEXT_CODES, _cell(0, 2, 1, "31")),
    Fault("table flattened into paragraphs", "pdf_table", {"table_flattened_into_text", "possible_missing_table"}, _flatten(0)),
    Fault("image removed", "pdf_table", {"possible_missing_image"}, _drop(lambda b: b.type == T.FIGURE)),
]


def run_campaign(tmp: Path) -> tuple[list[Result], dict[str, Case]]:
    cases = build_cases(tmp)
    baselines = {k: c.verify() for k, c in cases.items()}
    out = []
    for f in FAULTS:
        case = cases[f.case]
        base = baselines[f.case].report
        base_issues = issue_set(base.issues, blocking_only=True)
        rep = case.verify(mutate(case.resp, f.apply)).report
        new = issue_set(rep.issues, blocking_only=True) - base_issues
        codes = {c for c, _ in new}
        out.append(Result(f, detected=rep.status != ValidationStatus.VERIFIED and bool(new), codes=codes, status=rep.status.value,
                          matched_expectation=bool(codes & f.expect)))
    return out, cases
