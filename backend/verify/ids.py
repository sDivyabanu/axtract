"""Deterministic, readable ids for issues and recoveries ("iss-0001")."""

from __future__ import annotations


class IdSequence:
    """Hands out sequential ids. Share one instance across layers so ids never collide."""

    def __init__(self, prefix: str = "iss", start: int = 1):
        self.prefix = prefix
        self._n = start - 1

    def next(self) -> str:
        self._n += 1
        return f"{self.prefix}-{self._n:04d}"
