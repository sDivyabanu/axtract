#!/usr/bin/env python3
"""Download the embedding + reranker weights once and record/verify their SHA-256 digests.

Runtime is offline (HF_HUB_OFFLINE=1). The first run records the digests in
backend/model_weights/rag/MANIFEST.sha256 (commit it); later runs verify against it.

Models: BAAI/bge-small-en-v1.5 (MIT), Xenova/ms-marco-MiniLM-L-6-v2 (Apache-2.0).
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

DEST = Path(__file__).resolve().parent.parent / "backend" / "model_weights" / "rag"
MANIFEST = DEST / "MANIFEST.sha256"
EMBED = "BAAI/bge-small-en-v1.5"
RERANK = "Xenova/ms-marco-MiniLM-L-6-v2"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    from fastembed import TextEmbedding
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    print(f"embedding model {EMBED} ...")
    list(TextEmbedding(EMBED, cache_dir=str(DEST)).embed(["warm up"]))
    print(f"reranker {RERANK} ...")
    list(TextCrossEncoder(RERANK, cache_dir=str(DEST)).rerank("q", ["a document"]))

    files = sorted(p for p in DEST.rglob("*") if p.is_file() and p.suffix in (".onnx", ".json", ".txt", ".model")
                   and "blobs" not in p.parts and p.name != "MANIFEST.sha256")
    # snapshot symlinks resolve to blobs; hash the content once per relative path
    current = {str(p.relative_to(DEST)): sha(p) for p in files}
    if not MANIFEST.exists():
        MANIFEST.write_text("".join(f"{d}  {rel}\n" for rel, d in sorted(current.items())))
        print(f"recorded {len(current)} digests in {MANIFEST.name} (first run: commit it)")
        return 0
    pinned = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in MANIFEST.read_text().splitlines() if "  " in line}
    bad = [rel for rel, d in current.items() if rel in pinned and pinned[rel] != d]
    if bad:
        print("CHECKSUM MISMATCH:", *bad, sep="\n  ", file=sys.stderr)
        return 1
    print(f"verified {len(current)} files against {MANIFEST.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
