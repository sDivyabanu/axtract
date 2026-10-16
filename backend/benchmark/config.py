"""Paths and limits for the benchmark runner. Everything is overridable by environment."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

MODES = ("quick", "full")
DEFAULT_MODE = "quick"
DEFAULT_PER_CATEGORY = 5
MAX_PER_CATEGORY = 50
DEFAULT_SEED = 42
DEFAULT_WORKERS = 2
MAX_WORKERS = 4
DEFAULT_TIMEOUT_S = 300
MIN_TIMEOUT_S = 10
MAX_TIMEOUT_S = 3600
LOCK_STALE_SECONDS = 120
SCORER_TIMEOUT_SECONDS = 4 * 3600
MIN_FREE_MEMORY_MB = 2048
MAX_OUTPUT_CHARS = 20_000  # cap for extraction text shown in the failure inspector
IMPORT_MAX_BYTES = 64 * 1024 * 1024  # largest single artifact file the importer will read


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default.resolve()


def _default_data_dir() -> Path:
    return REPO_ROOT / "benchmarks" / "olmOCR-bench" / "bench_data"


@dataclass(frozen=True)
class BenchmarkSettings:
    data_dir: Path = field(default_factory=lambda: _env_path("AXTRACT_BENCH_DATA_DIR", _default_data_dir()))
    workspace: Path = field(default_factory=lambda: _env_path("AXTRACT_BENCH_WORKSPACE", REPO_ROOT / "benchmark_runs"))
    scorer_python: Path = field(
        default_factory=lambda: _env_path(
            "AXTRACT_BENCH_SCORER_PYTHON", REPO_ROOT / "benchmarks" / ".venv" / "Scripts" / "python.exe"
        )
    )
    # Interpreter that runs AXTRACT extraction in worker processes. Defaults to the API server's own Python.
    extract_python: Path = field(default_factory=lambda: _env_path("AXTRACT_BENCH_EXTRACT_PYTHON", Path(sys.executable)))
    import_root: Path | None = None  # read-only folder that official-run artifacts may be imported from
    repo_root: Path = REPO_ROOT

    def resolved_import_root(self) -> Path:
        if self.import_root is not None:
            return self.import_root.resolve()
        raw = os.environ.get("AXTRACT_BENCH_IMPORT_ROOT")
        if raw:
            return Path(raw).expanduser().resolve()
        # <repo>/benchmarks/olmOCR-bench/bench_data -> <repo>/reports/benchmarks/olmocr
        return (self.data_dir.parents[2] / "reports" / "benchmarks" / "olmocr").resolve()

    def validate_isolation(self) -> None:
        """The workspace must never sit inside (or contain) the official dataset or report folders."""
        ws = self.workspace.resolve()
        protected = [
            self.data_dir.resolve(),
            (self.repo_root / "reports" / "benchmarks").resolve(),
            self.resolved_import_root(),
        ]
        for p in protected:
            if ws == p or p in ws.parents or ws in p.parents:
                raise ValueError("Benchmark workspace must be separate from the official benchmark folders.")


def allowed_users() -> set[str]:
    """Users (ids or emails) allowed to use the benchmark API. '*' = any signed-in user. Default: nobody."""
    raw = os.environ.get("AXTRACT_BENCHMARK_USERS", "")
    return {u.strip().lower() for u in raw.split(",") if u.strip()}
