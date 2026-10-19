"""Run a vision-language OCR model (served by llama.cpp's llama-server) on the frozen 70-PDF diagnostic.

Each benchmark PDF is one page. The page is rendered with pypdfium2, sent to the server's OpenAI-compatible chat endpoint,
and the reply is written where the official scorer looks (<out>/candidate/<category>/<stem>_pg1_repeat1.md). Raw replies are
cached under <out>/raw keyed by the PDF hash and the model/render/sampling configuration, so an interrupted run resumes and a
configuration change never reuses stale output. Scoring is unchanged: use `frozen70_benchmark.py score --out <out>`.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

import httpx
import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from services.vlm_ocr import postprocess_markdown  # noqa: E402

MANIFEST = ROOT / "evaluation" / "frozen70" / "manifest.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render(pdf: Path, longest: int) -> bytes:
    doc = pdfium.PdfDocument(str(pdf))
    page = doc[0]
    w, h = page.get_size()
    img = page.render(scale=longest / max(w, h)).to_pil().convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    doc.close()
    return buf.getvalue()


def ocr(client: httpx.Client, url: str, png: bytes, cfg: dict) -> dict:
    body = {"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
        *([{"type": "text", "text": cfg["prompt"]}] if cfg["prompt"] else [])]}],
        "temperature": cfg["temperature"], "top_p": cfg["top_p"], "max_tokens": cfg["max_tokens"], "seed": cfg["seed"],
        "repeat_penalty": cfg["repeat_penalty"]}
    r = client.post(url.rstrip("/") + "/v1/chat/completions", json=body)
    r.raise_for_status()
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--server", default="http://127.0.0.1:8090")
    ap.add_argument("--model-id", required=True, help="recorded in the cache key, e.g. LightOnOCR-3-0.8B-Q8_0")
    ap.add_argument("--longest", type=int, default=2048)
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--top-p", type=float, default=1.0)
    ap.add_argument("--repeat-penalty", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--prompt", default="")
    ap.add_argument("--only", nargs="*", help="limit to these PDFs (relative paths) or categories")
    ap.add_argument("--manifest", default=str(MANIFEST))
    args = ap.parse_args()

    man = json.loads(Path(args.manifest).read_text(encoding="utf8"))
    data, out = Path(args.data_root).resolve(), Path(args.out).resolve()
    (out / "raw").mkdir(parents=True, exist_ok=True)
    cfg = {"model": args.model_id, "longest": args.longest, "max_tokens": args.max_tokens, "temperature": args.temperature,
           "top_p": args.top_p, "seed": args.seed, "prompt": args.prompt, "repeat_penalty": args.repeat_penalty}
    cfg_key = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]
    (out / "config.json").write_text(json.dumps({**cfg, "key": cfg_key}, indent=1), encoding="utf8")
    pdfs = list(man["pdfs"])
    if args.only:
        pdfs = [p for p in pdfs if p in args.only or p.split("/")[0] in args.only]
    rows, t_all = [], time.perf_counter()
    with httpx.Client(timeout=1800) as client:
        for i, rel in enumerate(pdfs):
            src = data / "pdfs" / rel
            raw = out / "raw" / f"{sha256(src)[:20]}_{cfg_key}.json"
            t0 = time.perf_counter()
            if raw.is_file():
                rec = json.loads(raw.read_text(encoding="utf8"))
                cached = True
            else:
                png = render(src, args.longest)
                resp = ocr(client, args.server, png, cfg)
                rec = {"pdf": rel, "config": cfg, "seconds": round(time.perf_counter() - t0, 2),
                       "text": resp["choices"][0]["message"]["content"], "finish": resp["choices"][0].get("finish_reason"),
                       "usage": resp.get("usage"), "timings": resp.get("timings")}
                tmp = raw.with_suffix(".tmp")
                tmp.write_text(json.dumps(rec), encoding="utf8")
                os.replace(tmp, raw)  # atomic: a partial reply is never mistaken for a finished one
                cached = False
            md = out / "candidate" / Path(rel).parent / (Path(rel).stem + "_pg1_repeat1.md")
            md.parent.mkdir(parents=True, exist_ok=True)
            md.write_text(postprocess_markdown(rec["text"]), encoding="utf8")
            rows.append({"pdf": rel, "seconds": rec["seconds"], "wall_seconds": rec["seconds"], "finish": rec["finish"],
                         "cached": cached, "completed": md.stat().st_size > 0,
                         "tokens": (rec.get("usage") or {}).get("completion_tokens")})
            print(f"{i + 1}/{len(pdfs)} {rel}: {rec['seconds']}s finish={rec['finish']} "
                  f"tokens={rows[-1]['tokens']}{' (cached)' if cached else ''}", flush=True)
    import subprocess
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    (out / "performance.json").write_text(json.dumps({"config": cfg, "source_head": head, "tree_dirty": True, "documents": rows,
                                                        "wall_seconds": round(time.perf_counter() - t_all, 1)}, indent=1), encoding="utf8")
    # frozen70_benchmark.py score expects the cases file written by its own extract step
    cases = []
    for name in man["dataset"]["case_source_sha256"]:
        wanted = {i for p in pdfs for i in man["cases"][p]}
        for line in (data / name).read_text(encoding="utf8").splitlines():
            row = json.loads(line) if line.strip() else None
            if row and row.get("id") in wanted:
                cases.append(row)
    (out / "selected_original_cases.json").write_text(json.dumps(cases), encoding="utf8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
