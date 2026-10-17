"""Process helpers: killing process trees, spotting an active official benchmark, resource budgets."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

OFFICIAL_MARKERS = ("benchmark_axtract_olmocr.py", "olmocr.bench.benchmark")
BELOW_NORMAL_PRIORITY = 0x00004000
NEW_PROCESS_GROUP = 0x00000200


def kill_tree(proc: subprocess.Popen) -> None:
    """Terminate a child and everything it spawned. Never raises."""
    try:
        if proc.poll() is not None:
            return
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=20)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass


def popen_flags_low_priority() -> dict:
    """Popen kwargs that start a child in its own group at reduced priority, so benchmarks never starve the API."""
    if sys.platform == "win32":
        return {"creationflags": BELOW_NORMAL_PRIORITY | NEW_PROCESS_GROUP}
    return {"start_new_session": True, "preexec_fn": lambda: os.nice(10)}


def _list_python_commands() -> list[tuple[int, str]]:
    """(pid, command line) of running Python processes. Read-only."""
    if sys.platform == "win32":
        ps = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
              "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        if not out:
            return []
        data = json.loads(out)
        data = [data] if isinstance(data, dict) else data
        return [(int(d["ProcessId"]), d.get("CommandLine") or "") for d in data]
    out = subprocess.run(["ps", "-eo", "pid=,comm=,args="], capture_output=True, text=True, timeout=20).stdout
    rows = []
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[1].lower().startswith("python"):
            rows.append((int(parts[0]), parts[2]))
    return rows


def official_benchmark_active() -> bool:
    """True only when a *Python* process is running the official adapter script or the official scorer module.

    Editors, git, shells and the like that merely mention those names do not count, and neither do
    this process or its own descendants (our scorer wrapper never carries the official command line).
    """
    try:
        rows = _list_python_commands()
    except (OSError, subprocess.SubprocessError, ValueError):
        return False  # cannot tell: the single-run lock still applies
    me = os.getpid()
    return any(pid != me and any(m in cmd for m in OFFICIAL_MARKERS) for pid, cmd in rows)


def free_memory_mb() -> int | None:
    """Available physical memory in MB, or None when it cannot be determined."""
    try:
        if sys.platform == "win32":
            import ctypes

            class MEMSTAT(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            stat = MEMSTAT()
            stat.dwLength = ctypes.sizeof(MEMSTAT)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
            return int(stat.ullAvailPhys // (1024 * 1024))
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, AttributeError):
        pass
    return None


def worker_budget(requested: int, hard_cap: int) -> int:
    """Workers actually used: never more than requested, the hard cap, or half the CPUs."""
    cpus = os.cpu_count() or 2
    return max(1, min(requested, hard_cap, max(1, cpus // 2)))
