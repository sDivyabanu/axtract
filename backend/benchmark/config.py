"""Paths and limits for the benchmark runner. Everything is overridable by environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

MODES = ("quick",)  # "full" is deliberately not runnable here: full official scores come from the official run
DEFAULT_MODE = "quick"
DEFAULT_PER_CATEGORY = 5
MAX_PER_CATEGORY = 50
DEFAULT_SEED = 42
DEFAULT_WORKERS = 2
MAX_WORKERS = 4
LOCK_STALE_SECONDS = 120
SCORER_TIMEOUT_SECONDS = 1800


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default.resolve()


@dataclass(frozen=True)
class BenchmarkSettings:
    data_dir: Path = field(
        default_factory=lambda: _env_path(
            "AXTRACT_BENCH_DATA_DIR", REPO_ROOT / "benchmarks" / "olmOCR-bench" / "bench_data"
        )
    )
    workspace: Path = field(
        default_factory=lambda: _env_path("AXTRACT_BENCH_WORKSPACE", REPO_ROOT / "benchmark_runs")
    )
    scorer_python: Path = field(
        default_factory=lambda: _env_path(
            "AXTRACT_BENCH_SCORER_PYTHON",
            REPO_ROOT / "benchmarks" / ".venv" / "Scripts" / "python.exe",
        )
    )
    repo_root: Path = REPO_ROOT

    def validate_isolation(self) -> None:
        """The workspace must never sit inside (or contain) the official dataset or report folders."""
        ws = self.workspace.resolve()
        protected = [self.data_dir.resolve(), (self.repo_root / "reports" / "benchmarks").resolve()]
        for p in protected:
            if ws == p or p in ws.parents or ws in p.parents:
                raise ValueError("Benchmark workspace must be separate from the official benchmark folders.")


def allowed_users() -> set[str]:
    """Users (ids or emails) allowed to use the benchmark API. '*' = any signed-in user. Default: nobody."""
    raw = os.environ.get("AXTRACT_BENCHMARK_USERS", "")
    return {u.strip().lower() for u in raw.split(",") if u.strip()}
