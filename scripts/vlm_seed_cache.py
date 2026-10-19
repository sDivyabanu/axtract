"""Load model replies recorded by vlm_frozen70.py / vlm_throughput.py into the engine's reply cache (AXTRACT_VLM_CACHE_DIR).

Only replies whose recorded request settings are exactly the ones the engine sends (grounding prompt, greedy decoding, same
max tokens, same render size, same model id) are loaded, and each is keyed by the page image re-rendered the way the engine
renders it. A run of the full AXTRACT pipeline then reuses those model outputs instead of recomputing them; anything that
does not match is simply not cached and is sent to the model.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from services import vlm_engine  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="run folder with raw/*.json")
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--cache-dir", required=True)
    args = ap.parse_args()
    os.environ["AXTRACT_VLM_CACHE_DIR"] = args.cache_dir
    cfg = vlm_engine.settings()
    loaded = skipped = 0
    for raw in sorted(Path(args.run, "raw").glob("*.json")):
        rec = json.loads(raw.read_text(encoding="utf8"))
        c = rec["config"]
        if (c["prompt"], c["temperature"], c["top_p"], c["seed"], c.get("repeat_penalty", 1.0), c["max_tokens"], c["longest"],
                c["model"]) != ("grounding", 0.0, 1.0, 0, 1.0, cfg["max_tokens"], cfg["longest"], cfg["model"]):
            skipped += 1
            continue
        png = vlm_engine.render_page_png(Path(args.data_root) / "pdfs" / rec["pdf"], 0, cfg["longest"])
        path = vlm_engine._cache_path(png, vlm_engine.request_body(png, cfg), cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({k: rec[k] for k in ("text", "finish", "usage", "seconds")}), encoding="utf8")
        loaded += 1
    print(f"loaded {loaded} replies, skipped {skipped} with other settings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
