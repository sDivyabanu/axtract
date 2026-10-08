#!/usr/bin/env python3
"""Cold-start measurement: unload the Ollama model, restart the backend, then time the FIRST LLM-answered question.
Usage: backend/.venv/bin/python scripts/cold_start.py <label> [wait_s_after_start]"""
import json, os, subprocess, sys, time
import httpx
label = sys.argv[1]; wait = float(sys.argv[2]) if len(sys.argv) > 2 else 0
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data = os.environ["DEALLENS_DATA_DIR"]
httpx.post("http://127.0.0.1:11434/api/generate", json={"model": "qwen3:4b-instruct", "keep_alive": 0}, timeout=60)
subprocess.run(["pkill", "-f", "uvicorn main:app"]); time.sleep(2)
env = {**os.environ, "DEALLENS_DATA_DIR": data}
subprocess.Popen([f"{ROOT}/backend/.venv/bin/python", "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"], cwd=f"{ROOT}/backend", env=env,
                 stdout=open(f"{data}/../uvicorn_{label}.log", "w"), stderr=subprocess.STDOUT)
while True:
    try:
        httpx.get("http://127.0.0.1:8000/api/rag/status", timeout=1); break
    except Exception: time.sleep(0.3)
t_up = time.time()
if wait < 0:  # wait for the background warm-up to finish, report how long it took
    while httpx.get("http://127.0.0.1:8000/api/rag/status", timeout=5).json().get("warmup", {}).get("status") != "ready": time.sleep(0.5)
    print("warm-up finished", round(time.time() - t_up, 1), "s after the API came up", flush=True)
else:
    time.sleep(wait)
H = httpx.Client(base_url="http://127.0.0.1:8000", timeout=600)
ws = next(w["workspace_id"] for w in H.get("/api/workspaces").json()
          if len(H.get(f"/api/workspaces/{w['workspace_id']}").json()["documents"]) == 6)
t0 = time.time(); first = None
with H.stream("POST", f"/api/workspaces/{ws}/ask", json={"question": "Does Falcon Industries have any debt?"}) as r:
    for line in r.iter_lines():
        if line.startswith("data:"):
            d = json.loads(line[5:])
            if d.get("event") == "token" and first is None: first = time.time() - t0
            if d.get("event") == "answer": a = d["answer"]
g = next(s for s in a["stages"] if s["name"] == "Generating")
print(json.dumps({"label": label, "first_token_s": round(first or 0, 1), "total_s": round(time.time() - t0, 1), "mode": a["mode"],
                  "model_load_ms": g.get("load_ms"), "prompt_tokens": g.get("prompt_tokens")}))
