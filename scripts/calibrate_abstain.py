"""Print the best cross-encoder score for each golden question, by type (used to set the abstention threshold)."""
import os, sys, tempfile, time
from pathlib import Path
import yaml
ROOT = Path(__file__).resolve().parent.parent
os.environ["DEALLENS_DATA_DIR"] = tempfile.mkdtemp(prefix="deallens-cal-")
sys.path.insert(0, str(ROOT / "backend"))
from fastapi.testclient import TestClient
from main import app
from rag import index, qa

c = TestClient(app)
ws = c.post("/api/workspaces", json={"name": "cal"}).json()["workspace_id"]
files = sorted((ROOT / "demo" / "project_falcon").iterdir())
c.post(f"/api/workspaces/{ws}/documents", files=[("files", (f.name, f.read_bytes())) for f in files])
while any(d["status"] not in ("ready", "failed") for d in c.get(f"/api/workspaces/{ws}").json()["documents"]): time.sleep(1)
rows = []
for q in yaml.safe_load((ROOT / "eval" / "golden_qa.yaml").read_text())["questions"]:
    hits = index.rerank_hits(ws, q["question"], index.search(ws, q["question"]))
    best = hits[0].rerank if hits else None
    qt = qa._terms(q["question"])
    ov = index.coverage(ws, q["question"], [index.chunk_row(ws, h.chunk_id)["text"] + " " + " ".join(index.chunk_row(ws, h.chunk_id)["heading_path"]) for h in hits[:3]])
    rows.append((q["type"], q["id"], best, ov, q["question"][:60]))
for r in sorted(rows, key=lambda r: (r[0], -r[2])):
    print(f"{r[0]:13s} {r[1]} best={r[2]:7.2f} coverage={r[3]:.2f}  {r[4]}")
