"""Deterministic matching primitives for comparing a source inventory with extracted blocks.

Matching is ONE-TO-ONE: once a block is claimed by a source object it cannot be claimed by another,
so two source tables cannot both "match" the same extracted table. Pairs are taken best-first with a
fixed tie-break (score, then ids), so the same input always yields the same assignment. Every
accepted pair keeps the reasons it was accepted for.

Text comparison here is for COMPLETENESS ("is this content represented?"), not correctness. It is
deliberately tolerant of formatting (see verify.normalize) and says nothing about whether the
extracted wording is right; that is the content layer's job.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

from Levenshtein import ratio as _lev_ratio

from verify.normalize import normalize_text

_TOKEN = re.compile(r"\w+", re.UNICODE)


@lru_cache(maxsize=2048)
def _counter(text: str) -> Counter:
    """Word multiset of a text (treat the result as read-only: it is cached)."""
    return Counter(_TOKEN.findall(normalize_text(text, case_sensitive=False, canonical_numbers=True)))


def tokens(text: str | None) -> list[str]:
    """Case-insensitive word tokens with number formatting canonicalised ("1,234" == "1234")."""
    return list(_counter(text).elements()) if text else []


def recall(source: str | None, output: str | None) -> tuple[float, list[str]]:
    """Share of the source's words (as a multiset) found in `output`, and the words that are missing.

    1.0 means every source word is present; a single dropped digit or word lowers it.
    """
    src, out = _counter(source or ""), _counter(output or "")
    total = sum(src.values())
    if not total:
        return 1.0, []
    missing = src - out
    return (total - sum(missing.values())) / total, sorted(missing.elements())


def similarity(a: str | None, b: str | None) -> float:
    na = normalize_text(a or "", case_sensitive=False, canonical_numbers=True)
    nb = normalize_text(b or "", case_sensitive=False, canonical_numbers=True)
    if not na and not nb:
        return 1.0
    return _lev_ratio(na, nb)


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def iou(a, b) -> float:
    if not a or not b:
        return 0.0
    i = _inter(a, b)
    u = _area(a) + _area(b) - i
    return i / u if u > 0 else 0.0


def containment(inner, outer) -> float:
    """Fraction of `inner`'s area that lies inside `outer`."""
    if not inner or not outer:
        return 0.0
    ai = _area(inner)
    return _inter(inner, outer) / ai if ai > 0 else 0.0


@dataclass
class Pair:
    obj_id: str
    block_id: str
    score: float
    reasons: list[str] = field(default_factory=list)


def assign(pairs: list[Pair], min_score: float) -> dict[str, Pair]:
    """Best-first one-to-one assignment. Returns {obj_id: Pair}; unmatched objects are absent."""
    taken_objs: dict[str, Pair] = {}
    used_blocks: set[str] = set()
    for p in sorted(pairs, key=lambda p: (-p.score, p.obj_id, p.block_id)):
        if p.score < min_score or p.obj_id in taken_objs or p.block_id in used_blocks:
            continue
        taken_objs[p.obj_id] = p
        used_blocks.add(p.block_id)
    return taken_objs
