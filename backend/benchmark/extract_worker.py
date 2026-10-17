"""Long-lived extraction worker process (one AXTRACT model/process warm-up for many documents).

Protocol (JSON lines): the parent writes {"id", "pdf", "rel", "out"}; the worker replies on the *original*
stdout with {"id", "meta"}. Everything the extraction code prints or logs goes to stderr.
Run as:  python -m benchmark.extract_worker <repo_root>
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import traceback
from pathlib import Path


def main() -> int:
    proto = sys.stdout
    sys.stdout = sys.stderr  # adapter logging binds to sys.stdout at import time: keep the channel clean
    repo_root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(repo_root / "backend"))
    script = repo_root / "scripts" / "benchmark_axtract_olmocr.py"
    spec = importlib.util.spec_from_file_location("_axtract_benchmark_adapter", script)
    adapter = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(adapter)  # type: ignore[union-attr]

    def send(obj: dict) -> None:
        proto.write(json.dumps(obj) + "\n")
        proto.flush()

    send({"ready": True, "pid": os.getpid()})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        t0 = time.perf_counter()
        try:
            meta = adapter.process_single_pdf(Path(req["pdf"]), Path(req["rel"]), Path(req["out"]), True)
        except BaseException as exc:  # noqa: BLE001 - report, keep the worker alive for the next document
            meta = {"success": False, "pages": 0, "error": f"{type(exc).__name__}: {exc}",
                    "total_elapsed_ms": (time.perf_counter() - t0) * 1000.0}
            traceback.print_exc()
        send({"id": req["id"], "meta": meta})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
