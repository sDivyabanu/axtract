"""Failure inspection: browse and drill into individual test cases without loading whole reports.

Everything streams from the compact per-case record (`outcomes.jsonl`); the large official HTML report is never read.
"""

from __future__ import annotations

from pathlib import Path

from . import config, store
from .runner import BenchmarkRunner, RunError
from .scoring import iter_outcomes

MAX_PAGE = 100


def describe_expected(rec: dict) -> str:
    """The benchmark condition in plain words, built from the official test fields."""
    t, f = rec.get("type"), rec.get("test") or {}
    qual = []
    if f.get("max_diffs"):
        qual.append(f"up to {f['max_diffs']} character difference(s) allowed")
    if f.get("case_sensitive") is False:
        qual.append("case-insensitive")
    if f.get("first_n"):
        qual.append(f"within the first {f['first_n']} characters")
    if f.get("last_n"):
        qual.append(f"within the last {f['last_n']} characters")
    tail = f" ({'; '.join(qual)})" if qual else ""
    if t == "present":
        return f"The text should appear: “{f.get('text')}”{tail}"
    if t == "absent":
        return f"The text should NOT appear: “{f.get('text')}”{tail}"
    if t == "order":
        return f"“{f.get('before')}” should appear before “{f.get('after')}”{tail}"
    if t == "math":
        return f"The math expression should be present: {f.get('math')}{tail}"
    if t == "table":
        rel = [f"{k.replace('_', ' ')}: “{f[k]}”" for k in ("up", "down", "left", "right", "top_heading", "left_heading") if f.get(k)]
        return f"A table cell “{f.get('cell')}”" + (f" with {', '.join(rel)}" if rel else "") + tail
    if t == "baseline":
        return "The page should contain a basic amount of readable text"
    return f"{t} check"


def classify_failure(rec: dict, doc: dict | None) -> str:
    explanation = rec.get("explanation") or ""
    if doc and doc.get("timed_out"):
        return "timeout"
    if doc and doc.get("status") == "failed":
        return "extraction_error"
    if "Missing MD files" in explanation:
        return "no_output"
    if doc and doc.get("empty"):
        return "empty_output"
    return "content_mismatch"


class CaseBrowser:
    def __init__(self, runner: BenchmarkRunner):
        self.runner = runner

    def _setup(self, run_id: str) -> tuple[Path, dict, dict[str, str]]:
        rdir = self.runner._dir(run_id)
        run = self.runner.load_run(run_id)
        return rdir, run, {j: c for c, j in run.get("category_jsonl", {}).items()}

    def list(self, run_id: str, *, outcome: str = "failed", category: str | None = None, ctype: str | None = None,
             q: str | None = None, offset: int = 0, limit: int = 50) -> dict:
        rdir, run, jsonl_to_cat = self._setup(run_id)
        limit = max(1, min(limit, MAX_PAGE))
        offset = max(0, offset)
        needle = (q or "").lower()
        total = 0
        items: list[dict] = []
        failed_total = passed_total = 0
        for rec in iter_outcomes(rdir / "outcomes.jsonl"):
            passed = bool(rec.get("passed"))
            failed_total += 0 if passed else 1
            passed_total += 1 if passed else 0
            cat = jsonl_to_cat.get(rec.get("source") or "")
            if outcome == "failed" and passed or outcome == "passed" and not passed:
                continue
            if category and cat != category or ctype and rec.get("type") != ctype:
                continue
            if needle and needle not in (rec.get("pdf") or "").lower() and needle not in (rec.get("id") or "").lower():
                continue
            total += 1
            if offset <= total - 1 < offset + limit:
                items.append({"id": rec["id"], "pdf": rec.get("pdf"), "page": rec.get("page"), "type": rec.get("type"),
                              "category": cat, "passed": passed, "expected": describe_expected(rec),
                              "explanation": (rec.get("explanation") or "")[:300]})
        return {"run_id": run_id, "total": total, "offset": offset, "limit": limit, "items": items,
                "run_totals": {"failed": failed_total, "passed": passed_total}}

    def detail(self, run_id: str, case_id: str) -> dict:
        rdir, run, jsonl_to_cat = self._setup(run_id)
        if not case_id or len(case_id) > 300:
            raise RunError("INVALID_CASE", "Invalid case id.")
        rec = next((r for r in iter_outcomes(rdir / "outcomes.jsonl") if r.get("id") == case_id), None)
        if rec is None:
            raise RunError("CASE_NOT_FOUND", "Case not found in this run.", 404)
        docs = store.read_jsonl_last(rdir / "docs.jsonl", "pdf")
        doc = docs.get(rec.get("pdf"))
        out = self._actual_output(run, rdir, rec)
        logs = [l for l in self.runner.log_tail(run_id, 2000).splitlines() if rec.get("pdf") and rec["pdf"] in l][-20:]
        return {
            "id": rec["id"], "pdf": rec.get("pdf"), "page": rec.get("page"), "type": rec.get("type"),
            "category": jsonl_to_cat.get(rec.get("source") or ""), "passed": bool(rec.get("passed")),
            "failure_reason": None if rec.get("passed") else classify_failure(rec, doc),
            "expected": describe_expected(rec), "expected_fields": rec.get("test") or {},
            "scorer_feedback": rec.get("explanation"),
            "document": {k: doc.get(k) for k in ("status", "pages", "total_ms", "per_page_ms", "empty", "timed_out", "error")} if doc else None,
            "actual_output": out, "logs": logs,
        }

    def _actual_output(self, run: dict, rdir: Path, rec: dict) -> dict:
        """The candidate Markdown for the case's page, capped so the browser never receives a huge file."""
        rel = Path(str(rec.get("pdf") or ""))
        if rel.is_absolute() or ".." in rel.parts or rel.suffix != ".pdf":
            return {"available": False, "reason": "invalid path"}
        name = f"{rel.stem}_pg{int(rec.get('page') or 1)}_repeat1.md"
        base: Path | None
        if run.get("imported"):
            cand = run["imported"].get("candidate_dir")
            base = (self.runner.settings.data_dir / cand) if cand else None
        else:
            base = rdir / "scoring" / "axtract"
        if base is None:
            return {"available": False, "reason": "output not available for this imported run"}
        path = (base / rel.parent / name).resolve()
        root = base.resolve()
        if root not in path.parents or not path.is_file():
            return {"available": False, "reason": "no output was produced for this page"}
        size = path.stat().st_size
        with path.open("rb") as fh:
            text = fh.read(config.MAX_OUTPUT_CHARS * 4).decode("utf-8", errors="replace")[: config.MAX_OUTPUT_CHARS]
        return {"available": True, "text": text, "size_bytes": size, "truncated": len(text) < size and size > config.MAX_OUTPUT_CHARS}
