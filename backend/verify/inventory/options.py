"""Knobs shared by the inventory collectors. All defaults are conservative and cheap."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InventoryOptions:
    max_cells: int = 300_000  # per workbook; beyond this a sheet is marked truncated
    max_listed: int = 1000  # merged ranges / formula coordinates kept per sheet
    max_pdf_pages_detailed: int = 60  # pdfplumber (words/tables) only runs on this many pages
    time_budget_s: float = 20.0  # soft budget for the slow collectors; they stop and say so
    pdf_image_min_area: float = 0.005  # image objects below this fraction of the page are decorative
