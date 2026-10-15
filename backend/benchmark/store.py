"""Run directories, atomic JSON writes and the single-active-run lock."""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import LOCK_STALE_SECONDS

RUN_ID_RE = re.compile(r"^qk-\d{8}T\d{6}Z-[0-9a-f]{8}$")
_write_lock = threading.Lock()


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"qk-{stamp}-{secrets.token_hex(4)}"


def atomic_write_json(path: Path, data: Any) -> None:
    """Write to a temp file in the same folder, then replace: readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(3)}.tmp")
    with _write_lock:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        for attempt in range(5):  # Windows can briefly deny replace while a reader has the file open
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))
        os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def run_dir(workspace: Path, run_id: str) -> Path:
    """Resolve a run folder; the id is validated so it can never escape the workspace."""
    if not RUN_ID_RE.match(run_id or ""):
        raise ValueError("Invalid run id.")
    root = (workspace / "runs").resolve()
    path = (root / run_id).resolve()
    if root not in path.parents:
        raise ValueError("Invalid run id.")
    return path


def list_run_ids(workspace: Path) -> list[str]:
    root = workspace / "runs"
    if not root.is_dir():
        return []
    return sorted((p.name for p in root.iterdir() if p.is_dir() and RUN_ID_RE.match(p.name)), reverse=True)


class ActiveRunLock:
    """Only one benchmark run at a time. A heartbeat file marks liveness; no PID probing
    (os.kill on Windows can terminate the target), so a lock is stale once its heartbeat is old."""

    def __init__(self, workspace: Path):
        self.path = workspace / ".active_run.lock"

    def holder(self) -> dict | None:
        info = read_json(self.path)
        if not info:
            return None
        if time.time() - float(info.get("heartbeat", 0)) > LOCK_STALE_SECONDS:
            return None
        return info

    def acquire(self, run_id: str) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        current = self.holder()
        if current:
            return current.get("run_id") == run_id
        if self.path.exists():
            try:
                self.path.unlink()  # stale lock from a crashed run
            except OSError:
                return False
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_id": run_id, "heartbeat": time.time()}))
        return True

    def beat(self, run_id: str) -> None:
        info = read_json(self.path)
        if info and info.get("run_id") == run_id:
            atomic_write_json(self.path, {"run_id": run_id, "heartbeat": time.time()})

    def release(self, run_id: str) -> None:
        info = read_json(self.path)
        if info and info.get("run_id") == run_id:
            try:
                self.path.unlink()
            except OSError:
                pass
