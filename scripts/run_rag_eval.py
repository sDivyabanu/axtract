#!/usr/bin/env python3
"""Run the golden questions through the naive baseline and DealLens and write reports/rag_eval.{md,json}.

  backend/.venv/bin/python scripts/run_rag_eval.py            # with the local LLM (several minutes: the baseline is slow)
  backend/.venv/bin/python scripts/run_rag_eval.py --no-llm   # fast: both pipelines in extractive mode
  backend/.venv/bin/python scripts/run_rag_eval.py --limit 5
"""
import argparse, json, os, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("DEALLENS_DATA_DIR", tempfile.mkdtemp(prefix="deallens-eval-"))
sys.path.insert(0, str(ROOT / "backend"))
import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from rag import baseline, config, evalrun, llm, qa  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--no-llm", action="store_true")
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--out", default=str(ROOT / "reports"))
args = ap.parse_args()
if args.no_llm:
    llm.status = lambda force=False: {"available": False, "model": "none", "reason": "disabled by --no-llm"}  # type: ignore

gold = yaml.safe_load((ROOT / "eval" / "golden_qa.yaml").read_text())
questions = gold["questions"][: args.limit] if args.limit else gold["questions"]

c = TestClient(app)
ws = c.post("/api/workspaces", json={"name": "eval"}).json()["workspace_id"]
files = sorted((ROOT / gold["data_room"]).iterdir())
c.post(f"/api/workspaces/{ws}/documents", files=[("files", (f.name, f.read_bytes())) for f in files])
t0 = time.time()
while any(d["status"] not in ("ready", "failed") for d in c.get(f"/api/workspaces/{ws}").json()["documents"]):
    time.sleep(1)
print(f"indexed {len(files)} documents in {time.time() - t0:.0f}s; LLM: {llm.status()['model'] if llm.status()['available'] else 'offline (extractive)'}", flush=True)

rows_b, rows_d, per_q = [], [], []
for i, q in enumerate(questions, 1):
    tb = time.time(); b = baseline.answer(ws, q["question"]); b_ms = (time.time() - tb) * 1000
    td = time.time(); d = None
    for ev in qa.ask_stream(ws, q["question"], mode="dealLens"):
        if ev["event"] == "answer":
            d = ev["answer"]
    d_ms = (time.time() - td) * 1000
    jb, jd = evalrun.judge(q, "baseline", b), evalrun.judge(q, "dealLens", d)
    rows_b.append({"q": q, "judge": jb, "ms": b_ms}); rows_d.append({"q": q, "judge": jd, "ms": d_ms})
    per_q.append({"id": q["id"], "type": q["type"], "question": q["question"],
                  "baseline": {"judge": jb, "ms": round(b_ms), "text": b["text"][:400]},
                  "dealLens": {"judge": jd, "ms": round(d_ms), "text": d["text"][:400], "mode": d["mode"], "abstained": d["abstained"]}})
    print(f"[{i:2d}/{len(questions)}] {q['id']} {q['type']:12s} baseline {'PASS' if jb['pass'] else 'fail'} ({b_ms/1000:5.1f}s) | dealLens {'PASS' if jd['pass'] else 'fail'} ({d_ms/1000:4.1f}s)  {q['question'][:50]}", flush=True)

result = {
    "data_room": gold["data_room"], "n_questions": len(questions), "ts": time.time(),
    "ts_iso": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    "llm": config.LLM_MODEL if not args.no_llm and llm.status()["available"] else "none (extractive mode)",
    "summary": {"baseline": evalrun.summarise(rows_b), "dealLens": evalrun.summarise(rows_d)},
    "questions": per_q,
    "limitations": [
        "One synthetic data room (6 files) and 27 questions: thresholds for abstention were calibrated on this same room, so the DealLens numbers are optimistic until a second room is evaluated.",
        "Judging is mechanical (number within tolerance, expected phrases present, refusal wording). A correct answer phrased unusually can be marked wrong, for either pipeline.",
        "The baseline uses the same local LLM and is deliberately naive; a production RAG with tuned prompts would score higher on abstention and lookups.",
        "With a small local model the baseline does not hallucinate on unanswerable questions as often as larger-context demos suggest; its main failures here are the hidden instruction, exact arithmetic over a split table, and the lack of provenance.",
    ],
}
out = Path(args.out); out.mkdir(exist_ok=True)
tag = "rag_eval" if not args.no_llm else "rag_eval_nollm"
(out / f"{tag}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
(out / f"{tag}.md").write_text(evalrun.to_markdown(result))
s = result["summary"]
print("\nBaseline :", {k: v for k, v in s["baseline"].items() if k != "by_type"})
print("DealLens :", {k: v for k, v in s["dealLens"].items() if k != "by_type"})
print(f"wrote reports/{tag}.md and .json")
