"""AXTRACT Verify: the engine that runs every layer and assembles one ValidationReport.

    integrity -> source inventory -> completeness -> content -> structure -> reading order -> report

Guarantees
  * It NEVER raises and NEVER changes the extraction. If a stage fails, that is recorded as the
    report's `failure` (status FAILED: "validation could not complete") and the remaining stages
    that do not depend on it still run. The caller keeps its DocumentResponse regardless.
  * Statuses are derived by the rollup rules, never set by hand. A passing check cannot override an
    unresolved disagreement, and a unit with no independent content evidence stays NOT_VERIFIABLE.
  * Each stage is timed, so the cost of validation is visible per stage.

Not wired into parse_service yet: this module is called explicitly with the original file and the
finished extraction.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from models.document import DocumentResponse
from verify.completeness import CompletenessOutcome, check_completeness
from verify.content import check_content
from verify.ids import IdSequence
from verify.integrity import run_integrity
from verify.inventory import InventoryOptions, build_inventory
from verify.inventory.models import SourceInventory
from verify.models import ProviderInfo, ValidationFailure, ValidationReport
from verify.order import check_order
from verify.rollup import UnitChecks, build_report, merge_units
from verify.structure import check_structure

_ENGINES = {
    "pdf": [("pypdfium2", "native text, image objects"), ("pdfplumber", "word regions, table candidates")],
    "xlsx": [("ooxml", "raw sheet XML")], "pptx": [("ooxml", "raw slide XML")], "docx": [("ooxml", "raw document XML")],
    "png": [("PIL", "image properties")], "jpg": [("PIL", "image properties")], "jpeg": [("PIL", "image properties")],
}


@dataclass
class VerifyOptions:
    inventory: InventoryOptions = field(default_factory=InventoryOptions)


@dataclass
class VerifyArtifacts:
    """The report plus the intermediate evidence behind it (for audit, tests and benchmarking)."""

    report: ValidationReport
    inventory: SourceInventory | None = None
    completeness: CompletenessOutcome | None = None
    layers: dict[str, Any] = field(default_factory=dict)


def run_verification(file_path: Path, response: DocumentResponse, options: VerifyOptions | None = None) -> VerifyArtifacts:
    options = options or VerifyOptions()
    t_all = time.perf_counter()
    ids = IdSequence("iss")
    timings: dict[str, float] = {}
    failures: list[ValidationFailure] = []
    layers: dict[str, Any] = {}

    def stage(name: str, fn: Callable[[], Any]) -> Any:
        t0 = time.perf_counter()
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - validation must never take the extraction down
            failures.append(ValidationFailure(stage=name, error_type=type(exc).__name__, message=str(exc)[:300]))
            return None
        finally:
            timings[name] = round((time.perf_counter() - t0) * 1000, 3)

    integrity = stage("integrity", lambda: run_integrity(response, ids))
    inventory = stage("inventory", lambda: build_inventory(Path(file_path), response.file_type, options.inventory))
    completeness = stage("completeness", lambda: check_completeness(inventory, response, ids)) if inventory is not None else None
    matches = completeness.matches if completeness is not None else None
    content = stage("content", lambda: check_content(inventory, response, matches, ids)) if matches is not None else None
    structure = stage("structure", lambda: check_structure(inventory, response, matches, ids)) if matches is not None else None
    order = stage("reading_order", lambda: check_order(inventory, response, matches, ids)) if matches is not None else None
    layers.update(integrity=integrity, content=content, structure=structure, reading_order=order)

    outcomes = [o for o in (integrity, completeness, content, structure, order) if o is not None]
    units: list[UnitChecks] = merge_units(*(o.units for o in outcomes))
    issues = [i for o in outcomes for i in o.issues]
    providers: list[ProviderInfo] = []
    if inventory is not None and inventory.error is None:
        providers = [ProviderInfo(name=n, tier="builtin", available=True, units_checked=len(inventory.units), note=what)
                     for n, what in _ENGINES.get(inventory.file_type, [])]
    elif inventory is not None:
        providers = [ProviderInfo(name=n, tier="builtin", available=False, note=f"inventory failed: {inventory.error}")
                     for n, _ in _ENGINES.get(inventory.file_type, [])]
    timings["total"] = round((time.perf_counter() - t_all) * 1000, 3)
    failure = failures[0] if failures else None
    try:
        report = build_report(units, issues, providers=providers, timings_ms=timings, failure=failure)
    except Exception as exc:  # noqa: BLE001 - last resort: still return a well-formed report
        report = build_report([], failure=ValidationFailure(stage="report", error_type=type(exc).__name__, message=str(exc)[:300]),
                              timings_ms=timings)
    return VerifyArtifacts(report=report, inventory=inventory, completeness=completeness, layers=layers)


def verify_extraction(file_path: Path, response: DocumentResponse, options: VerifyOptions | None = None) -> ValidationReport:
    """Validate a finished extraction against its original file. Never raises; never modifies `response`."""
    try:
        return run_verification(file_path, response, options).report
    except Exception as exc:  # noqa: BLE001
        return build_report([], failure=ValidationFailure(stage="engine", error_type=type(exc).__name__, message=str(exc)[:300]))
