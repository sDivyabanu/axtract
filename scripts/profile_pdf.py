"""Profile one benchmark PDF through the real parse_path (cumulative top functions). Local diagnostic."""
import cProfile
import faulthandler
import os
import pstats
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("AXTRACT_VERIFY", "1")
faulthandler.dump_traceback_later(int(os.environ.get("PROFILE_DUMP_AFTER", "70")), exit=False)

from services.parse_service import parse_path  # noqa: E402

pdf = Path(sys.argv[1])
profiler = cProfile.Profile()
t0 = time.perf_counter()
profiler.enable()
try:
    response = parse_path(pdf, pdf.name, budget_seconds=55, persistent_preview=False)
    status = f"ok blocks={len(response.blocks or [])} errors={[e.code for e in response.errors]}"
except Exception as exc:  # noqa: BLE001
    status = f"EXC {type(exc).__name__}: {exc}"
profiler.disable()
print("wall", round(time.perf_counter() - t0, 1), status)
stats = pstats.Stats(profiler)
stats.sort_stats("cumulative").print_stats(r"(extractors|services|verify|utils)", 25)
