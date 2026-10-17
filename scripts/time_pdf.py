"""Time one PDF through the real parse_path against a chosen backend tree (read-only on that tree).
usage: time_pdf.py BACKEND_DIR PDF [verify=1|0]   (use PYTHONDONTWRITEBYTECODE=1 to avoid writing into BACKEND_DIR)
"""
import os
import sys
import time
from pathlib import Path

backend = Path(sys.argv[1]).resolve()
pdf = Path(sys.argv[2])
os.environ["AXTRACT_VERIFY"] = sys.argv[3] if len(sys.argv) > 3 else "1"
sys.path.insert(0, str(backend))
from services.parse_service import parse_path  # noqa: E402
import services.parse_service as ps  # noqa: E402

stages = {}
for name in ("pre_scan", "route_regions", "_attach_validation"):
    fn = getattr(ps, name)

    def timed(*a, _f=fn, _n=name, **kw):
        t = time.perf_counter()
        try:
            return _f(*a, **kw)
        finally:
            stages[_n] = round(time.perf_counter() - stages.get(_n, 0) * 0 - t, 2) * -1 if False else round(time.perf_counter() - t, 2)
    setattr(ps, name, timed)

t0 = time.perf_counter()
try:
    r = parse_path(pdf, pdf.name, budget_seconds=55, persistent_preview=False)
    status = f"ok blocks={len(r.blocks or [])} errors={[e.code for e in r.errors]}"
except Exception as exc:  # noqa: BLE001
    status = f"EXC {type(exc).__name__}: {exc}"
print(f"{backend.parent.name}: wall={time.perf_counter() - t0:.1f}s stages={stages} {status}")
