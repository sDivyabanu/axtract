"""Background execution of runs inside the API process, plus the 'don't compete with the official run' guard."""

from __future__ import annotations

import subprocess
import sys
import threading
from typing import Callable

from .runner import BenchmarkRunner, RunError

OFFICIAL_MARKERS = ("benchmark_axtract_olmocr.py", "olmocr.bench.benchmark")


def official_benchmark_active() -> bool:
    """True when another process is running the official adapter or scorer (read-only process listing)."""
    try:
        if sys.platform == "win32":
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                   "Get-CimInstance Win32_Process | ForEach-Object { $_.CommandLine }"]
        else:
            cmd = ["ps", "-eo", "args"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return False  # cannot tell: the single-run lock still applies
    return any(m in line for line in out.splitlines() for m in OFFICIAL_MARKERS)


class RunManager:
    """Starts, resumes and cancels runs on a background thread. One run at a time."""

    def __init__(self, runner: BenchmarkRunner, official_check: Callable[[], bool] = official_benchmark_active):
        self.runner = runner
        self.official_check = official_check
        self._guard = threading.Lock()
        self._active: tuple[str, threading.Thread, threading.Event] | None = None

    def _busy(self) -> bool:
        if self._active and self._active[1].is_alive():
            return True
        return self.runner.lock.holder() is not None

    def _launch(self, run_id: str) -> None:
        if self.official_check():
            raise RunError("OFFICIAL_BENCHMARK_ACTIVE",
                           "The official benchmark is running. Start a quick run after it finishes.", 409)
        cancel = threading.Event()

        def target() -> None:
            try:
                self.runner.execute(run_id, cancel)
            except RunError:
                pass  # recorded in the run's own state/log

        thread = threading.Thread(target=target, name=f"benchmark-{run_id}", daemon=True)
        self._active = (run_id, thread, cancel)
        thread.start()

    def start(self, **config) -> str:
        with self._guard:
            if self._busy():
                raise RunError("RUN_CONFLICT", "Another benchmark run is already active.", 409)
            if self.official_check():
                raise RunError("OFFICIAL_BENCHMARK_ACTIVE",
                               "The official benchmark is running. Start a quick run after it finishes.", 409)
            run_id = self.runner.create_run(**config)
            self._launch(run_id)
            return run_id

    def resume(self, run_id: str) -> None:
        with self._guard:
            st = self.runner.status(run_id)
            if not st["resumable"]:
                raise RunError("NOT_RESUMABLE", f"A run that is {st['status']} cannot be resumed.", 409)
            if self._busy():
                raise RunError("RUN_CONFLICT", "Another benchmark run is already active.", 409)
            self._launch(run_id)

    def cancel(self, run_id: str) -> None:
        """Cooperative: in-flight documents finish, queued ones are skipped, the run becomes resumable."""
        with self._guard:
            if not self._active or self._active[0] != run_id or not self._active[1].is_alive():
                raise RunError("NOT_RUNNING", "This run is not running in this server.", 409)
            self._active[2].set()
