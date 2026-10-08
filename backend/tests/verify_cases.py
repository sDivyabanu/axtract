"""A document under test: the original file, AXTRACT's real extraction of it, and its source inventory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from models.document import BlockType as T, DocumentResponse
from tests.verify_fixtures import extract
from verify.completeness import check_completeness
from verify.content import check_content
from verify.engine import VerifyArtifacts, run_verification
from verify.inventory import build_inventory
from verify.order import check_order
from verify.structure import check_structure


@dataclass
class Case:
    path: Path
    ext: str
    resp: DocumentResponse
    inv: object

    @classmethod
    def of(cls, path: Path) -> "Case":
        ext = path.suffix.lstrip(".").lower()
        return cls(path, ext, extract(path), build_inventory(path, ext))

    def verify(self, resp: DocumentResponse | None = None) -> VerifyArtifacts:
        return run_verification(self.path, resp or self.resp)

    def layers(self, resp: DocumentResponse | None = None):
        """(completeness, content, structure, order) outcomes for a possibly corrupted extraction."""
        r = resp or self.resp
        comp = check_completeness(self.inv, r)
        return comp, check_content(self.inv, r, comp.matches), check_structure(self.inv, r, comp.matches), check_order(self.inv, r, comp.matches)


def mutate(resp: DocumentResponse, fn) -> DocumentResponse:
    """A deep copy of the extraction with `fn` applied: a simulated corruption."""
    out = resp.model_copy(deep=True)
    fn(out)
    return out


def first(resp: DocumentResponse, pred):
    return next(b for b in resp.blocks if pred(b))


def tables(resp: DocumentResponse):
    return [b for b in resp.blocks if b.type == T.TABLE]


def sync_table_content(block) -> None:
    """Keep a table block's text consistent with its (edited) rows, as a real extraction would be."""
    block.content = "\n".join(" | ".join("" if c is None else str(c) for c in row) for row in block.metadata["rows"])


def issue_set(issues, *, blocking_only=False):
    """{(code, unit)} of issues, optionally only those that change a status."""
    return {(i.code, i.locator.unit.key) for i in issues if not blocking_only or i.severity.value in ("medium", "high", "critical")}
