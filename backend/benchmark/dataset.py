"""Discover the official categories and test cases from the dataset on disk (nothing hardcoded)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Category:
    name: str  # top-level folder under pdfs/, e.g. "tables"
    jsonl: str  # official test file, e.g. "table_tests.jsonl"
    tests_by_pdf: dict[str, list[str]] = field(default_factory=dict)  # "tables/x.pdf" -> [test ids]

    @property
    def pdf_count(self) -> int:
        return len(self.tests_by_pdf)

    @property
    def test_count(self) -> int:
        return sum(len(v) for v in self.tests_by_pdf.values())


def discover_categories(data_dir: Path) -> dict[str, Category]:
    """Read every *.jsonl in the dataset root. A category is the pdfs/ folder its tests point at.

    Only PDFs that actually exist on disk and have at least one test case are kept.
    """
    data_dir = Path(data_dir)
    pdf_root = data_dir / "pdfs"
    if not pdf_root.is_dir():
        raise FileNotFoundError("Benchmark dataset not found (missing pdfs/ folder).")
    cats: dict[str, Category] = {}
    for jsonl in sorted(data_dir.glob("*.jsonl")):
        with jsonl.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    pdf = str(row["pdf"]).replace("\\", "/")
                    test_id = str(row["id"])
                except (ValueError, KeyError, TypeError):
                    continue
                parts = pdf.split("/")
                if len(parts) < 2 or ".." in parts or not (pdf_root / pdf).is_file():
                    continue
                cat = cats.setdefault(parts[0], Category(name=parts[0], jsonl=jsonl.name))
                if cat.jsonl != jsonl.name:
                    # one folder fed by two jsonl files would break per-category scoring: refuse loudly
                    raise ValueError(f"Category {parts[0]!r} spans multiple test files.")
                cat.tests_by_pdf.setdefault(pdf, []).append(test_id)
    return dict(sorted(cats.items()))


def resolve_categories(requested: list[str] | str | None, available: dict[str, Category]) -> list[str]:
    """'all' (or empty) -> every category; otherwise validate each name against the discovered set."""
    if requested is None or requested == "all" or requested == ["all"] or requested == []:
        return list(available)
    if isinstance(requested, str):
        requested = [requested]
    unknown = [c for c in requested if c not in available]
    if unknown:
        raise ValueError(f"Unknown categories: {', '.join(sorted(map(str, unknown)))}")
    return [c for c in available if c in set(requested)]  # canonical order, de-duplicated
