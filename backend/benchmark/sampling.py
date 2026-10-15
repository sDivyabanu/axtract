"""Deterministic per-category sampling. Same data + seed + size => same documents."""

from __future__ import annotations

import random

from .dataset import Category


def sample_documents(
    categories: dict[str, Category], selected: list[str], per_category: int, seed: int
) -> list[dict]:
    """Return the manifest: one entry per selected PDF with the exact official test ids it carries.

    Each category draws from its own RNG (seeded by seed + name), so adding or removing a category
    never changes what another category picked. PDFs are de-duplicated across categories.
    """
    manifest: list[dict] = []
    seen: set[str] = set()
    for name in selected:
        cat = categories[name]
        pool = sorted(p for p in cat.tests_by_pdf if p not in seen)
        picked = sorted(random.Random(f"{seed}:{name}").sample(pool, min(per_category, len(pool))))
        for pdf in picked:
            seen.add(pdf)
            manifest.append({"pdf": pdf, "category": name, "test_ids": sorted(cat.tests_by_pdf[pdf])})
    return manifest
