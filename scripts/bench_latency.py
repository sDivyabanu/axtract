#!/usr/bin/env python3
"""Latency + accuracy benchmark against a RUNNING backend (so models are as warm as in the demo).

  backend/.venv/bin/python scripts/bench_latency.py --label before          # golden set on the Falcon room
  backend/.venv/bin/python scripts/bench_latency.py --label before --big    # also the large-report questions
Writes reports/latency_<label>.json and prints p50/p95, time-to-first-token, prompt tokens, accuracy.
"""
import argparse, json, statistics as st, sys, time
from pathlib import Path
import httpx, yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from rag import evalrun  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--label", required=True)
ap.add_argument("--base", default="http://localhost:8000")
ap.add_argument("--big", action="store_true", help="also ask the large-report questions (room named 'Welcome')")
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--skip-falcon", action="store_true", help="only the large-report questions")
a = ap.parse_args()
H = httpx.Client(base_url=a.base, timeout=600)


def room(pred):
    best = None
    for w in H.get("/api/workspaces").json():
        d = H.get(f"/api/workspaces/{w['workspace_id']}").json()["documents"]
        if d and all(x["status"] == "ready" for x in d) and pred(w, d):
            best = w["workspace_id"]
    return best


def ask(ws, q):
    t0 = time.time(); first = None; ans = None
    with H.stream("POST", f"/api/workspaces/{ws}/ask", json={"question": q}) as r:
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            d = json.loads(line[5:])
            if d.get("event") == "token" and first is None:
                first = time.time() - t0
            if d.get("event") == "answer":
                ans = d["answer"]
    total = time.time() - t0
    gen = next((s for s in ans["stages"] if s["name"] == "Generating"), {})
    return ans, {"ms": round(total * 1000), "first_token_ms": round((first if first is not None else total) * 1000),
                 "mode": ans["mode"], "prompt_tokens": gen.get("prompt_tokens"), "completion_tokens": gen.get("completion_tokens"),
                 "abstained": ans["abstained"],
                 "stages": {st["name"]: st["ms"] for st in ans["stages"]}, "ttft_ms": gen.get("ttft_ms"), "prompt_eval_ms": gen.get("prompt_eval_ms"), "gen_ms": gen.get("gen_ms")}


def pct(v, p):
    v = sorted(v); return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


out = {"label": a.label, "ts": time.strftime("%Y-%m-%d %H:%M"), "falcon": [], "big": []}
gold = yaml.safe_load((ROOT / "eval" / "golden_qa.yaml").read_text())
qs = gold["questions"][: a.limit] if a.limit else gold["questions"]
fw = room(lambda w, d: len(d) == 6 and any(x["filename"].startswith("Falcon_") for x in d))
ask(fw, "Who is the lender under the facility agreement?")  # not counted: removes one-off cache effects
for q in ([] if a.skip_falcon else qs):
    ans, m = ask(fw, q["question"]); j = evalrun.judge(q, "dealLens", ans)
    out["falcon"].append({"id": q["id"], "type": q["type"], "pass": j["pass"], **m})
    print(f"{q['id']} {m['mode']:10s} {m['ms']:6d} ms  first-token {m['first_token_ms']:6d}  prompt {m['prompt_tokens']}  {'PASS' if j['pass'] else 'fail'}", flush=True)
if a.big:
    bw = room(lambda w, d: any("annualreport" in x["filename"] for x in d))
    BIG = ["Which company is this annual report for?", "What is the net profit for the year?", "What was the total revenue for the year?",
           "Who is the chief executive officer?", "What was the dividend per share declared?", "What is the capital of France?",
           "What are the main risk factors described?", "How many employees does the firm have?"]
    for q in BIG:
        ans, m = ask(bw, q)
        out["big"].append({"q": q, "answer": ans["text"][:160], **m})
        print(f"BIG {m['mode']:10s} {m['ms']:6d} ms  first-token {m['first_token_ms']:6d}  prompt {m['prompt_tokens']}  {q[:40]}  {m['stages']} prompt-eval={m['prompt_eval_ms']}ms gen={m['gen_ms']}ms", flush=True)


def summ(rows):
    if not rows: return {}
    ms = [r["ms"] for r in rows]; ft = [r["first_token_ms"] for r in rows]
    llm = [r for r in rows if r["mode"] == "llm"]
    return {"n": len(rows), "p50_ms": pct(ms, 50), "p95_ms": pct(ms, 95), "mean_ms": round(st.mean(ms)),
            "first_token_p50_ms": pct(ft, 50), "first_token_p95_ms": pct(ft, 95),
            "llm_answers": len(llm), "llm_p50_ms": pct([r["ms"] for r in llm], 50) if llm else None,
            "prompt_tokens_mean": round(st.mean([r["prompt_tokens"] for r in llm if r["prompt_tokens"]])) if any(r["prompt_tokens"] for r in llm) else None}


out["summary"] = {"falcon": summ(out["falcon"]) if out["falcon"] else {}, "big": summ(out["big"]),
                  "falcon_accuracy": round(sum(r["pass"] for r in out["falcon"]) / max(1, len(out["falcon"])), 3)}
(ROOT / "reports" / f"latency_{a.label}.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out["summary"], indent=1))
