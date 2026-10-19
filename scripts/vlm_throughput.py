"""Throughput experiment for the VLM OCR server: send pages concurrently and measure wall time and output.

Same inputs, outputs and caching as vlm_frozen70.py (whose behaviour this does not change), plus --concurrency. With
llama-server started with `-np N` the server decodes N pages as one batch. The run records wall time, pages/hour, and the
server process's peak working set (Windows), so configurations can be compared on accuracy, latency and memory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vlm_frozen70 import MANIFEST, ROOT, ocr, postprocess_markdown, render, sha256  # noqa: E402


def server_peak_mib(port: int) -> float | None:
    """Peak working set of the llama-server listening on `port` (Windows only)."""
    if os.name != "nt":
        return None
    ps = (f"$p=(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1)"
          ".OwningProcess; if ($p) { (Get-Process -Id $p).PeakWorkingSet64 }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.strip()
    return round(int(out) / 2 ** 20, 1) if out.isdigit() else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--server", default="http://127.0.0.1:8091")
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--longest", type=int, default=2048)
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--prompt", default="grounding")
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()

    man = json.loads(Path(MANIFEST).read_text(encoding="utf8"))
    data, out = Path(args.data_root).resolve(), Path(args.out).resolve()
    (out / "raw").mkdir(parents=True, exist_ok=True)
    cfg = {"model": args.model_id, "longest": args.longest, "max_tokens": args.max_tokens, "temperature": 0.0,
           "top_p": 1.0, "seed": 0, "prompt": args.prompt, "repeat_penalty": 1.0}
    key = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]
    pdfs = [p for p in man["pdfs"] if not args.only or p in args.only or p.split("/")[0] in args.only]
    render_lock = threading.Lock()
    client = httpx.Client(timeout=3600, limits=httpx.Limits(max_connections=args.concurrency + 2))

    def one(rel: str) -> dict:
        src = data / "pdfs" / rel
        raw = out / "raw" / f"{sha256(src)[:20]}_{key}.json"
        if raw.is_file():
            rec = json.loads(raw.read_text(encoding="utf8"))
        else:
            t0 = time.perf_counter()
            with render_lock:  # PDFium is not thread-safe
                png = render(src, args.longest)
            resp = ocr(client, args.server, png, cfg)
            rec = {"pdf": rel, "config": cfg, "seconds": round(time.perf_counter() - t0, 2),
                   "text": resp["choices"][0]["message"]["content"], "finish": resp["choices"][0].get("finish_reason"),
                   "usage": resp.get("usage"), "timings": resp.get("timings")}
            tmp = raw.with_suffix(".tmp")
            tmp.write_text(json.dumps(rec), encoding="utf8")
            os.replace(tmp, raw)
        md = out / "candidate" / Path(rel).parent / (Path(rel).stem + "_pg1_repeat1.md")
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(postprocess_markdown(rec["text"]), encoding="utf8")
        print(f"{rel}: {rec['seconds']}s finish={rec['finish']} tokens={(rec.get('usage') or {}).get('completion_tokens')}", flush=True)
        return {"pdf": rel, "seconds": rec["seconds"], "wall_seconds": rec["seconds"], "finish": rec["finish"],
                "completed": md.stat().st_size > 0, "tokens": (rec.get("usage") or {}).get("completion_tokens")}

    t0 = time.perf_counter()
    with ThreadPoolExecutor(args.concurrency) as pool:
        rows = list(pool.map(one, pdfs))
    wall = time.perf_counter() - t0
    port = int(args.server.rsplit(":", 1)[1].split("/")[0])
    summary = {"config": cfg, "concurrency": args.concurrency, "pages": len(rows), "wall_seconds": round(wall, 1),
               "pages_per_hour": round(len(rows) / wall * 3600, 1), "output_tokens": sum(r["tokens"] or 0 for r in rows),
               "server_peak_working_set_mib": server_peak_mib(port), "finish_length": sum(r["finish"] == "length" for r in rows)}
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    (out / "performance.json").write_text(json.dumps({**summary, "source_head": head, "tree_dirty": True, "documents": rows},
                                                     indent=1), encoding="utf8")
    wanted = {i for p in pdfs for i in man["cases"][p]}
    cases = [json.loads(line) for name in man["dataset"]["case_source_sha256"]
             for line in (data / name).read_text(encoding="utf8").splitlines() if line.strip()]
    (out / "selected_original_cases.json").write_text(json.dumps([c for c in cases if c["id"] in wanted]), encoding="utf8")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
