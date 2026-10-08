"""Real processing progress for one parse job.

The pipeline calls `report()` / `report_engine()` at the points where a stage truly starts or
finishes. When nobody is listening (the normal `/api/parse` path) these are no-ops, so reporting
costs nothing and can never change a result.

Seven user-facing stages, in the order they really run:

    security -> extraction -> inventory -> checks -> content -> structure -> verdict

`checks` (integrity + completeness) and `structure` (structure + reading order) each aggregate two
engine layers into one stage. A stage is `running` when its first layer starts and ends when its last
layer ends; nothing here is estimated: no timers, no percentages.

What may leave the server: stage ids, states, counts, severities, issue codes and a short fixed-vocabulary
`reason`. Never document text, block ids, file paths, exception messages or filenames; `_clean_detail`
enforces that whitelist for every event.
"""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

STAGES: list[dict[str, str]] = [
    {"id": "security", "label": "Security scanning",
     "description": "Checks the file for hidden content, active links, embedded objects and unsafe markup."},
    {"id": "extraction", "label": "Document extraction",
     "description": "Reads text, tables, figures and charts into structured blocks."},
    {"id": "inventory", "label": "Source inventory",
     "description": "Independently counts what the original file contains, without using the extractor."},
    {"id": "checks", "label": "Integrity & completeness",
     "description": "Confirms the output is well formed and that nothing in the source went missing."},
    {"id": "content", "label": "Content correctness",
     "description": "Compares extracted words, numbers and table cells with the source."},
    {"id": "structure", "label": "Structure & reading order",
     "description": "Checks tables, headings and the order the content is read in."},
    {"id": "verdict", "label": "Validation verdict",
     "description": "Combines the evidence into a verdict and report."},
]
STAGE_IDS = [s["id"] for s in STAGES]
STATES = ("pending", "running", "completed", "warning", "failed", "skipped")

# Verify engine layers -> user-facing stage, and which layer closes a multi-layer stage.
_ENGINE_STAGE = {"inventory": "inventory", "integrity": "checks", "completeness": "checks",
                 "content": "content", "structure": "structure", "reading_order": "structure"}
_ENGINE_LAST = {"checks": "completeness", "structure": "reading_order"}
_ENGINE_MEMBERS = {"checks": ("integrity", "completeness"), "structure": ("structure", "reading_order"),
                   "inventory": ("inventory",), "content": ("content",)}
VERIFY_STAGES = ("inventory", "checks", "content", "structure", "verdict")

# The only detail keys that may be emitted, and the shape each must have.
_DETAIL_KEYS = {"findings", "issues", "blocks", "pages", "errors", "by_severity", "codes", "reason", "error_type", "status"}
_SEVERITIES = {"critical", "high", "medium", "low", "info"}
_REASONS = {"disabled", "time_budget", "upstream_failed", "not_run", "cancelled", "unavailable"}
_STATUSES = {"verified", "recovered", "review_required", "failed", "not_verifiable"}


class Cancelled(BaseException):
    """Raised inside the worker when the client went away. BaseException so no `except Exception` swallows it."""


def _clean_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (detail or {}).items():
        if key not in _DETAIL_KEYS or value is None:
            continue
        if key in ("findings", "issues", "blocks", "pages", "errors"):
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                out[key] = value
        elif key in ("by_severity", "codes"):
            if isinstance(value, dict):
                allowed = _SEVERITIES if key == "by_severity" else None
                clean = {str(k)[:48]: int(v) for k, v in value.items()
                         if isinstance(v, int) and not isinstance(v, bool) and (allowed is None or k in allowed)
                         and (allowed is not None or str(k).replace("_", "").replace(".", "").isalnum())}
                if clean:
                    out[key] = dict(list(clean.items())[:20])
        elif key == "reason":
            if value in _REASONS:
                out[key] = value
        elif key == "status":
            if value in _STATUSES:
                out[key] = value
        elif key == "error_type":
            if isinstance(value, str) and value.replace("_", "").isalnum():
                out[key] = value[:48]
    return out


@dataclass
class _Group:
    started: bool = False
    ran: set[str] = field(default_factory=set)
    skipped: set[str] = field(default_factory=set)
    issues: int = 0
    by_severity: dict[str, int] = field(default_factory=dict)
    codes: dict[str, int] = field(default_factory=dict)
    failed: bool = False
    error_type: str | None = None
    closed: bool = False


class Tracker:
    """Collects stage events for one job and hands each to `sink` (thread-safe by construction)."""

    def __init__(self, sink: Callable[[dict[str, Any]], None]):
        self._sink = sink
        self._lock = threading.Lock()
        self._t0 = time.perf_counter()
        self._seq = 0
        self.cancelled = threading.Event()
        self.states: dict[str, str] = {s: "pending" for s in STAGE_IDS}
        self._groups: dict[str, _Group] = {g: _Group() for g in ("inventory", "checks", "content", "structure")}

    # ------------------------------------------------------------------ plain stages
    def emit(self, stage: str, state: str, detail: dict[str, Any] | None = None) -> None:
        if self.cancelled.is_set():
            raise Cancelled()
        if stage not in self.states or state not in STATES:
            return
        with self._lock:
            self._seq += 1
            self.states[stage] = state
            event = {"id": stage, "state": state, "seq": self._seq,
                     "elapsed_ms": round((time.perf_counter() - self._t0) * 1000)}
            clean = _clean_detail(detail)
            if clean:
                event["detail"] = clean
        self._sink(event)

    # ------------------------------------------------------------------ Verify engine layers
    def engine(self, layer: str, state: str, issues: Iterable[Any] = (), error_type: str | None = None) -> None:
        """One Verify layer started/finished. `state`: running | completed | failed | skipped."""
        stage = _ENGINE_STAGE.get(layer)
        if stage is None:
            return
        g = self._groups[stage]
        if not g.started:
            g.started = True
            self.emit(stage, "running")
        if state == "running":
            return
        if state == "failed":
            g.failed, g.error_type = True, error_type
        elif state == "skipped":
            g.skipped.add(layer)
        else:
            g.ran.add(layer)
        for issue in issues:
            sev = getattr(issue, "severity", "")
            sev = str(getattr(sev, "value", sev))
            code = str(getattr(issue, "code", ""))
            g.issues += 1
            g.by_severity[sev] = g.by_severity.get(sev, 0) + 1
            if code:
                g.codes[code] = g.codes.get(code, 0) + 1
        if layer == _ENGINE_LAST.get(stage, layer):
            self._close_group(stage)

    def _close_group(self, stage: str, reason: str | None = None) -> None:
        g = self._groups[stage]
        if g.closed:
            return
        g.closed = True
        members = set(_ENGINE_MEMBERS[stage])
        detail: dict[str, Any] = {"issues": g.issues, "by_severity": g.by_severity, "codes": g.codes}
        if g.failed:
            state, detail["error_type"] = "failed", g.error_type
        elif not g.ran:
            state, detail["reason"] = "skipped", reason or "time_budget"
        elif (g.skipped or (members - g.ran)) and reason != "none":
            state, detail["reason"] = "warning", reason or "time_budget"  # part of this stage never ran
        else:
            # informational notes alone are not a warning; any real finding is
            state = "warning" if g.issues - g.by_severity.get("info", 0) > 0 else "completed"
        self.emit(stage, state, detail)

    def finish_engine(self, reason: str = "upstream_failed") -> None:
        """Close whatever the engine never reached: unreached layers are skipped, honestly, not faked."""
        for stage, g in self._groups.items():
            if g.closed:
                continue
            if g.started:  # some layers ran, the rest never did (an upstream layer failed or the budget ended)
                self._close_group(stage, reason)
            else:
                g.closed = True
                self.emit(stage, "skipped", {"reason": reason})

    def skip_verify(self, reason: str) -> None:
        for stage in VERIFY_STAGES:
            if self.states[stage] == "pending":
                if stage in self._groups:
                    self._groups[stage].closed = True
                self.emit(stage, "skipped", {"reason": reason})


_tracker: ContextVar[Tracker | None] = ContextVar("progress_tracker", default=None)


def install(tracker: Tracker | None) -> None:
    _tracker.set(tracker)


def current() -> Tracker | None:
    return _tracker.get()


def report(stage: str, state: str, **detail: Any) -> None:
    t = _tracker.get()
    if t is not None:
        t.emit(stage, state, detail)


def report_engine(layer: str, state: str, issues: Iterable[Any] = (), error_type: str | None = None) -> None:
    t = _tracker.get()
    if t is not None:
        t.engine(layer, state, issues, error_type)


def severity_counts(findings: Iterable[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        sev = str(getattr(f, "severity", "") or "")
        out[sev] = out.get(sev, 0) + 1
    return out


def finish_engine(reason: str = "upstream_failed") -> None:
    t = _tracker.get()
    if t is not None:
        t.finish_engine(reason)
