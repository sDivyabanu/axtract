#!/usr/bin/env python3
"""Ingest demo/project_falcon into a scratch database and ask questions (backend only, no servers).

  backend/.venv/bin/python scripts/ask_demo.py "What is the total debt maturing in 2026?" ...
"""
import json, os, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("DEALLENS_DATA_DIR", tempfile.mkdtemp(prefix="deallens-demo-"))
sys.path.insert(0, str(ROOT / "backend"))
from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402

c = TestClient(app)
ws = c.post("/api/workspaces", json={"name": "Project Falcon"}).json()["workspace_id"]
files = sorted((ROOT / "demo" / "project_falcon").iterdir())
c.post(f"/api/workspaces/{ws}/documents", files=[("files", (f.name, f.read_bytes())) for f in files])
t = time.time()
while time.time() - t < 600:
    docs = c.get(f"/api/workspaces/{ws}").json()["documents"]
    if all(d["status"] in ("ready", "failed") for d in docs):
        break
    time.sleep(2)
print(f"indexed in {time.time() - t:.0f}s")
for d in docs:
    print(f'  {d["filename"]:36s} {d["status"]:6s} {d["doc_type"]:20s} pages={d["page_count"]} chunks={d["chunk_count"]} flags={d["flag_count"]} quarantined={d["quarantined_count"]} {d["error"] or ""}')


def ask(q):
    t0 = time.time(); ans = None
    with c.stream("POST", f"/api/workspaces/{ws}/ask", json={"question": q}) as r:
        ev = None
        for line in r.iter_lines():
            if line.startswith("event:"): ev = line[6:].strip()
            elif line.startswith("data:") and ev == "answer": ans = json.loads(line[5:])["answer"]
    print(f"\nQ: {q}  [{time.time() - t0:.1f}s] mode={ans['mode']} route={ans['route']} abstained={ans['abstained']} grounding={ans['grounding']}")
    print("A:", ans["text"][:400])
    for r in ans["receipts"]:
        print("   RECEIPT:", r["formula"][:230], "| warnings:", r["warnings"])
    print("   cites:", [(x["filename"], x["pages"], x["printed_pages"]) for x in ans["citations"]][:3], "badges:", [b["label"] for b in ans["badges"]])
    if ans.get("excluded_sources"): print("   excluded:", ans["excluded_sources"])
    return ans


for q in sys.argv[1:]:
    ask(q)
