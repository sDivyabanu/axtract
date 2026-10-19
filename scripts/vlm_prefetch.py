"""Batch page transcription into the VLM reply cache: the scalable front half of a large VLM run.

Each PDF page is classified exactly as the AXTRACT router classifies it, and the pages that belong to the chosen engine
profile ("general" = LightOnOCR-3-0.8B for born-digital pages, "scan" = the scan model for pictures of text) are sent to
that profile's server with bounded concurrency. Replies land in AXTRACT_VLM_CACHE_DIR, keyed by page image and request
settings, so:
  * the run is resumable page by page (a finished page is never sent again, an interrupted one is simply redone);
  * only one model server has to be running at a time (run the two profiles one after the other);
  * the AXTRACT pipeline then runs from the cache (frozen70_benchmark.py / full_benchmark.py with --vlm all) and produces
    exactly what it would have produced talking to the servers directly.
A page that fails is recorded in <log-dir>/failed.jsonl and left to the pipeline, which retries it or falls back.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))


def pdf_list(args) -> list[Path]:
    data = Path(args.data_root)
    if args.frozen70:
        man = json.loads((ROOT / "evaluation" / "frozen70" / "manifest.json").read_text(encoding="utf8"))
        rels = list(man["pdfs"])
    else:
        from full_benchmark import assertion_pdfs

        rels = assertion_pdfs(data, args.category)
    if args.only:
        rels = [r for r in rels if r in args.only or r.split("/")[0] in args.only]
    return [data / "pdfs" / r for r in rels]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--profile", choices=["general", "scan"], required=True)
    ap.add_argument("--concurrency", type=int, default=4, help="match the server's -np")
    ap.add_argument("--frozen70", action="store_true", help="the frozen 70-PDF sample instead of every assertion PDF")
    ap.add_argument("--category")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--log-dir", required=True)
    args = ap.parse_args()
    if not os.environ.get("AXTRACT_VLM_CACHE_DIR"):
        raise SystemExit("ERROR: set AXTRACT_VLM_CACHE_DIR; the cache is where this tool puts its results.")
    os.environ.setdefault("AXTRACT_VLM_OCR", "all")

    import pymupdf

    from extractors.pymupdf_extractor import PyMuPDFExtractor
    from services import vlm_engine
    from services.pdf_router import PDFRouter

    cfg = vlm_engine.settings(args.profile)
    if args.profile == "scan" and cfg["profile"] != "scan":
        raise SystemExit("ERROR: --profile scan needs AXTRACT_VLM_SCAN_MODEL_ID (and AXTRACT_VLM_SCAN_URL if it is another server).")
    router, jobs = PDFRouter(), []
    for pdf in pdf_list(args):
        with pymupdf.open(str(pdf)) as doc:
            for i, page in enumerate(doc, start=1):
                analysis = PyMuPDFExtractor.analyze_page(page)
                route = router._decide_route(analysis)
                if vlm_engine.enabled_for(route, analysis) and vlm_engine.profile_for(route, analysis) == args.profile:
                    jobs.append((pdf, i))
    log = Path(args.log_dir)
    log.mkdir(parents=True, exist_ok=True)
    lock, done, t0 = threading.Lock(), {"sent": 0, "cached": 0, "failed": 0, "tokens": 0}, time.perf_counter()
    print(f"{len(jobs)} page(s) for profile {args.profile} -> {cfg['model']} at {cfg['url']}", flush=True)

    def one(pdf: Path, page: int) -> dict:
        png = vlm_engine.render_page_png(pdf, page - 1, cfg["longest"])
        reply = vlm_engine.transcribe(png, cfg)
        return {"pdf": str(pdf), "page": page, "cached": reply["cached"], "seconds": reply["seconds"],
                "finish": reply["finish"], "tokens": (reply.get("usage") or {}).get("completion_tokens")}

    with ThreadPoolExecutor(args.concurrency) as pool, (log / "pages.jsonl").open("a", encoding="utf8") as ok_log, \
            (log / "failed.jsonl").open("a", encoding="utf8") as bad_log:
        futures = {pool.submit(one, pdf, page): (pdf, page) for pdf, page in jobs}
        for n, fut in enumerate(as_completed(futures), start=1):
            pdf, page = futures[fut]
            try:
                row = fut.result()
            except Exception as exc:  # noqa: BLE001 - one failed page never stops the batch
                with lock:
                    done["failed"] += 1
                bad_log.write(json.dumps({"pdf": str(pdf), "page": page, "error": f"{type(exc).__name__}: {exc}"}) + "\n")
                bad_log.flush()
                continue
            with lock:
                done["cached" if row["cached"] else "sent"] += 1
                done["tokens"] += row["tokens"] or 0
            ok_log.write(json.dumps(row) + "\n")
            ok_log.flush()
            if n % 10 == 0 or n == len(jobs):
                el = time.perf_counter() - t0
                print(f"{n}/{len(jobs)} pages, {done}, {el:.0f}s, {done['sent'] / max(el, 1e-9) * 3600:.1f} new pages/h", flush=True)
    summary = {"profile": args.profile, "model": cfg["model"], "pages": len(jobs), **done,
               "wall_seconds": round(time.perf_counter() - t0, 1)}
    (log / f"summary_{args.profile}.json").write_text(json.dumps(summary, indent=1), encoding="utf8")
    print(json.dumps(summary))
    return 1 if done["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
