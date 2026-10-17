"""Pool of warm extraction worker processes with hard per-document timeouts and instant cancellation."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from . import procutil

_EOF = object()


class _Worker:
    def __init__(self, cmd: list[str], cwd: Path, env: dict, stderr_path: Path):
        self.stderr_fh = open(stderr_path, "ab")  # noqa: SIM115 - closed in close()
        self.proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr_fh,
            text=True, encoding="utf-8", bufsize=1, **procutil.popen_flags_low_priority(),
        )
        self.lines: queue.Queue = queue.Queue()
        self.seq = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        try:
            for line in self.proc.stdout:  # type: ignore[union-attr]
                self.lines.put(line)
        except (ValueError, OSError):
            pass  # stream closed by close()
        self.lines.put(_EOF)

    def close(self) -> None:
        procutil.kill_tree(self.proc)
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                stream.close()  # type: ignore[union-attr]
            except OSError:
                pass
        self.stderr_fh.close()


class ExtractorPool:
    """`extract(pdf, rel, out)` is thread-safe; at most `workers` documents run at once.

    A worker that times out, crashes or is cancelled is killed (with its children) and replaced lazily,
    so one stuck PDF can never wedge the run. The worker process stays warm between documents.
    """

    def __init__(self, python: Path, repo_root: Path, workers: int, timeout_s: float, log_dir: Path,
                 on_event: Callable[[str], None] | None = None, startup_timeout_s: float = 180.0,
                 command: list[str] | None = None):
        self.python, self.repo_root, self.timeout_s = python, repo_root, timeout_s
        self.command = command  # test hook: replaces the default worker command line
        self.startup_timeout_s = startup_timeout_s
        self.log_dir = log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        self.on_event = on_event or (lambda _m: None)
        self.cancelled = threading.Event()
        self._slots: queue.Queue = queue.Queue()
        self._all: list[_Worker] = []
        self._all_lock = threading.Lock()
        self._counter = 0
        for _ in range(workers):
            self._slots.put(None)
        per = max(1, (os.cpu_count() or 2) // max(1, workers * 2))
        self._env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                     "OMP_NUM_THREADS": str(per), "MKL_NUM_THREADS": str(per), "OPENBLAS_NUM_THREADS": str(per)}

    def _spawn(self) -> _Worker | None:
        with self._all_lock:
            self._counter += 1
            n = self._counter
        cmd = self.command or [str(self.python), "-m", "benchmark.extract_worker", str(self.repo_root)]
        w = _Worker(cmd, self.repo_root / "backend", self._env, self.log_dir / f"worker-{n}.stderr.log")
        with self._all_lock:
            self._all.append(w)
        try:
            first = w.lines.get(timeout=self.startup_timeout_s)
        except queue.Empty:
            w.close()
            self.on_event(f"worker {n} did not start within {self.startup_timeout_s:.0f}s")
            return None
        if first is _EOF or not json.loads(first).get("ready"):
            w.close()
            self.on_event(f"worker {n} exited during startup (see worker-{n}.stderr.log)")
            return None
        return w

    def _fail(self, error: str, started: float, **extra) -> dict:
        return {"success": False, "pages": 0, "error": error,
                "total_elapsed_ms": (time.perf_counter() - started) * 1000.0, **extra}

    def extract(self, pdf: Path, rel: Path, out: Path) -> dict:
        started = time.perf_counter()
        slot = self._slots.get()
        worker = slot
        try:
            if self.cancelled.is_set():
                return {"cancelled": True}
            if worker is None or worker.proc.poll() is not None:
                worker = self._spawn()
                if worker is None:
                    return self._fail("WorkerStartError: extraction worker failed to start", started)
            worker.seq += 1
            rid = worker.seq
            try:
                worker.proc.stdin.write(json.dumps({"id": rid, "pdf": str(pdf), "rel": str(rel), "out": str(out)}) + "\n")  # type: ignore[union-attr]
                worker.proc.stdin.flush()  # type: ignore[union-attr]
            except OSError:
                worker.close()
                worker = None
                return self._fail("WorkerCrashed: could not send the document to its worker", started)
            deadline = time.monotonic() + self.timeout_s
            while True:
                if self.cancelled.is_set():
                    worker.close()
                    worker = None
                    return {"cancelled": True}
                try:
                    line = worker.lines.get(timeout=min(0.5, max(0.01, deadline - time.monotonic())))
                except queue.Empty:
                    if time.monotonic() >= deadline:
                        worker.close()
                        worker = None
                        self.on_event(f"timeout after {self.timeout_s:.0f}s: {rel}")
                        return self._fail(f"TimeoutError: no result within {self.timeout_s:.0f}s", started, timed_out=True)
                    continue
                if line is _EOF:
                    if self.cancelled.is_set():  # the worker was killed by cancel(), not by a crash
                        worker.close()
                        worker = None
                        return {"cancelled": True}
                    code = worker.proc.poll()
                    worker.close()
                    worker = None
                    return self._fail(f"WorkerCrashed: extraction process exited (code {code})", started)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue  # stray output on the protocol channel: ignore
                if msg.get("id") == rid:
                    return msg["meta"]
        finally:
            self._slots.put(worker)

    def cancel(self) -> None:
        """Kill every worker now; in-flight `extract` calls return {"cancelled": True}."""
        self.cancelled.set()
        with self._all_lock:
            workers = list(self._all)
        for w in workers:
            w.close()

    def close(self) -> None:
        with self._all_lock:
            workers, self._all = list(self._all), []
        for w in workers:
            w.close()
