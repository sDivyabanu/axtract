"""Pluggable secondary validators (layer 7).

A provider re-reads one validation unit of the ORIGINAL file with a different engine and returns a
CANDIDATE. A candidate is evidence to compare, never a result to trust: it is compared with the
original extraction and with the independent source evidence, kept alongside both, and only promoted
through an explicit decision (see verify.recovery).

Two tiers, so AXTRACT never depends on one engine and never blocks a normal parse on a heavy model:
  builtin   cheap, in-process (e.g. a second PDF text reader); may run in the request path
  heavy     slow / optional / separately installed (e.g. Docling); only ever run on suspicious units,
            on explicit escalation, outside the normal /api/parse path
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from verify.inventory.models import SourceInventory
from verify.models import EvidenceKind, UnitRef


class ProviderError(Exception):
    """The provider could not read this unit (reported, never fatal)."""


@dataclass
class ProviderCandidate:
    provider: str
    unit: UnitRef
    text: str
    kind: EvidenceKind
    structure: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0


@runtime_checkable
class ValidationProvider(Protocol):
    name: str
    tier: str  # "builtin" | "heavy"
    kind: EvidenceKind  # how the candidate was produced

    def available(self) -> tuple[bool, str]:
        """(usable here, reason if not)."""

    def supports(self, inventory: SourceInventory, unit: UnitRef) -> bool:
        """Can this provider read this kind of unit of this kind of file?"""

    def extract(self, file_path: Path, unit: UnitRef, budget_s: float) -> ProviderCandidate:
        """Read the unit. Must respect `budget_s` and raise ProviderError on failure."""
