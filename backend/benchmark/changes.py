"""Change-aware testing: which benchmark categories does a code change put at risk?

Git tells us what changed; ordered rules map each file to the AXTRACT component it belongs to and to the
official categories that component can affect. Category names are the official dataset folder names and are
always intersected with the categories actually discovered in the dataset. Rules are conservative: anything in
the PDF pipeline that is not clearly scoped recommends every category.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ALL = "*"
MAX_FILES = 500
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,99}(~\d{1,3})?$")

# (path regex, categories or ALL, component, reason, confidence). First match wins.
RULES: list[tuple[str, object, str, str, str]] = [
    (r"^backend/extractors/ocr_extractor\.py$", ["old_scans", "old_scans_math", "long_tiny_text"], "ocr",
     "OCR changes affect scanned and tiny-text documents", "high"),
    (r"^backend/services/equation_service\.py$|^backend/services/vision/(formula|latex|omml|models)\.py$",
     ["arxiv_math", "old_scans_math"], "equations", "Equation and formula changes affect math documents", "high"),
    (r"^backend/services/table_service\.py$", ["tables"], "tables", "Table extraction changes affect table tests", "high"),
    (r"^backend/services/(layout_service|region_router)\.py$", ["multi_column", "headers_footers", "tables"], "layout",
     "Layout and reading-order changes affect multi-column, header/footer and table pages", "medium"),
    (r"^backend/services/markdown_service\.py$", ALL, "markdown", "Markdown rendering is shared by every category", "high"),
    (r"^backend/(extractors/(pymupdf_extractor|base|registry)|services/(parse_service|pdf_router)|models/.*|security/(pdf_guard|pipeline|output_safety|unicode_guard))\.py$|^backend/models/.*\.py$",
     ALL, "pipeline", "Core PDF pipeline code is shared by every category", "high"),
    (r"^backend/verify/", ALL, "verify", "Verify runs inside extraction and can alter output or latency", "low"),
    (r"^scripts/benchmark_axtract_olmocr\.py$", ALL, "benchmark-adapter", "The benchmark adapter itself changed", "high"),
    (r"^backend/services/(vision/chart_.*|chart_service)\.py$", [], "charts", "Chart extraction is not covered by olmOCR-bench", "high"),
    (r"^backend/extractors/(docx|pptx|xlsx)_extractor\.py$", [], "office", "Office formats are not covered by olmOCR-bench", "high"),
    (r"^(frontend/|docs/|README|FEATURES|EXPECTED_RESULTS|reports/|demo/|prisma/|sample_files/)|\.md$", [], "non-extraction",
     "Not part of PDF extraction", "high"),
    (r"^backend/((.*/)?tests/|benchmark/|routers/|rag/|auth/|db/|crypto/)", [], "non-extraction", "Does not change extraction output", "high"),
    (r"^backend/", ALL, "unclassified-backend", "Unclassified backend change: assume it can affect every category", "low"),
]
_COMPILED = [(re.compile(p), cats, comp, why, conf) for p, cats, comp, why, conf in RULES]


class ChangeError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def classify(path: str, available: list[str]) -> dict:
    for rx, cats, comp, why, conf in _COMPILED:
        if rx.search(path):
            names = list(available) if cats == ALL else [c for c in available if c in cats]  # type: ignore[operator]
            return {"path": path, "component": comp, "categories": names, "reason": why, "confidence": conf}
    return {"path": path, "component": "other", "categories": [], "reason": "Outside the extraction code", "confidence": "high"}


def _git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise ChangeError("GIT_UNAVAILABLE", "Git is not available on this server.", 503)
    if out.returncode != 0:
        raise ChangeError("GIT_ERROR", "Could not read the repository changes.", 400)
    return out.stdout


def resolve_ref(repo: Path, ref: str) -> str:
    """Validate a user-supplied ref and resolve it to a commit SHA (never reaches git as an option)."""
    if not REF_RE.match(ref or "") or ".." in ref or ref.endswith(".lock"):
        raise ChangeError("INVALID_REF", "That is not a valid git reference.")
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        raise ChangeError("GIT_UNAVAILABLE", "Git is not available on this server.", 503)
    sha = out.stdout.strip()
    if out.returncode != 0 or not sha:
        raise ChangeError("UNKNOWN_REF", "That reference does not exist in this repository.", 404)
    return sha


def default_base(repo: Path, last_run_sha: str | None) -> tuple[str, str]:
    """(ref, source): the last benchmarked revision if we still have it, else main, else the previous commit."""
    for ref, source in ((last_run_sha, "last benchmarked revision"), ("main", "main branch"), ("HEAD~1", "previous commit")):
        if ref:
            try:
                resolve_ref(repo, ref)
                return ref, source
            except ChangeError:
                continue
    raise ChangeError("NO_BASE", "No base revision could be found; pass one explicitly.", 404)


def recommend(repo: Path, available: list[str], base: str | None = None, last_run_sha: str | None = None) -> dict:
    if base:
        base_ref, source = base, "requested"
    else:
        base_ref, source = default_base(repo, last_run_sha)
    base_sha = resolve_ref(repo, base_ref)
    entries: dict[str, str] = {}
    for line in _git(repo, "diff", "--name-status", "--no-renames", base_sha, "--").splitlines():
        status, _, path = line.partition("\t")
        if path:
            entries[path.replace("\\", "/")] = status.strip()[:1]
    for path in _git(repo, "ls-files", "--others", "--exclude-standard").splitlines():
        entries.setdefault(path.replace("\\", "/"), "A")
    files = [{**classify(p, available), "status": s} for p, s in sorted(entries.items())][:MAX_FILES]
    flagged = [f for f in files if f["categories"]]
    recommended = [c for c in available if any(c in f["categories"] for f in flagged)]
    return {
        "base": {"ref": base_ref, "sha": base_sha, "source": source},
        "head": {"sha": _git(repo, "rev-parse", "HEAD").strip()},
        "changed_files": len(entries), "truncated": len(entries) > MAX_FILES,
        "files": files, "recommended_categories": recommended,
        "needs_benchmark": bool(recommended),
        "low_confidence": [f["path"] for f in flagged if f["confidence"] == "low"],
        "summary": ("No extraction code changed since the base revision; a benchmark is not needed."
                    if not recommended else
                    f"{len(flagged)} changed file(s) can affect: {', '.join(recommended)}."),
    }
