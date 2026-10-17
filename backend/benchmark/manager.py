"""Background execution of runs inside the API process, plus the 'don't compete with the official run' guard."""

from __future__ import annotations

import threading
from typing import Callable

from .procutil import official_benchmark_active
from .runner import BenchmarkRunner, RunError


class RunManager:
    """Starts, resumes and cancels runs on a background thread. One run at a time.

    Extraction happens in child processes and scoring in another, so the API process only runs this
    thread's bookkeeping: ordinary AXTRACT requests are not blocked.
    """

    def __init__(self, runner: BenchmarkRunner, official_check: Callable[[], bool] = official_benchmark_active):
        self.runner = runner
        self.official_check = official_check
        self._guard = threading.Lock()
        self._active: tuple[str, threading.Thread, threading.Event] | None = None

    def _busy(self) -> bool:
        if self._active and self._active[1].is_alive():
            return True
        return self.runner.lock.holder() is not None

    def _refuse_if_official_active(self) -> None:
        if self.official_check():
            raise RunError("OFFICIAL_BENCHMARK_ACTIVE",
                           "The official benchmark is running. Start a benchmark after it finishes.", 409)

    def _launch(self, run_id: str) -> None:
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
            self._refuse_if_official_active()
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
            self._refuse_if_official_active()
            self._launch(run_id)

    def cancel(self, run_id: str) -> None:
        """Kills extraction workers (or the scorer) now; finished documents are kept and the run is resumable."""
        with self._guard:
            if not self._active or self._active[0] != run_id or not self._active[1].is_alive():
                raise RunError("NOT_RUNNING", "This run is not running on this server.", 409)
            self.runner.request_cancel(run_id)
            self._active[2].set()

    def active_run_id(self) -> str | None:
        if self._active and self._active[1].is_alive():
            return self._active[0]
        return self.runner.active_run_id()
