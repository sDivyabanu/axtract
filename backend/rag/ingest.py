"""Data-room ingestion: store -> parse -> shield -> chunk -> embed -> index.

One worker thread processes documents sequentially (an 8 GB laptop cannot parse and embed
several large reports at once). Status/progress are written to SQLite and polled by the UI.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from models.document import DocumentBlock
from models.errors import AppError
from rag import chunker, config, db, embed, facts as facts_mod, index, meta, security
from services.parse_service import parse_path
from utils.files import (
    MAX_UPLOAD_BYTES,
    get_extension,
    sanitize_filename,
    validate_magic_bytes,
)

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="deallens-ingest")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def register(workspace_id: str, tmp_path: Path, filename: str) -> dict[str, Any]:
    """Validate an uploaded temp file, move it into the data room and enqueue indexing.

    Returns the document record. A byte-identical file already in the workspace is reused.
    """
    filename = sanitize_filename(filename)
    ext = get_extension(filename)
    from extractors.registry import get_extractor

    if get_extractor(ext) is None:
        raise AppError("UNSUPPORTED_FORMAT", f"'.{ext}' files are not supported.", status_code=415)
    size = tmp_path.stat().st_size
    if size == 0:
        raise AppError("EMPTY_FILE", "The uploaded file is empty.", status_code=400)
    if size > MAX_UPLOAD_BYTES:
        raise AppError("FILE_TOO_LARGE", "File exceeds the size limit.", status_code=413)
    with tmp_path.open("rb") as fh:
        if not validate_magic_bytes(fh.read(16), ext):
            raise AppError("INVALID_FILE", "File content does not match the expected format.", status_code=422)

    sha = sha256_file(tmp_path)
    with db.connect() as c:
        dup = c.execute("SELECT id FROM documents WHERE workspace_id=? AND sha256=?", (workspace_id, sha)).fetchone()
    if dup:
        from rag import workspaces

        return {**workspaces.document(workspace_id, dup["id"]), "duplicate": True}

    doc_id = db.new_id()
    dest_dir = config.FILES_DIR / workspace_id / doc_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"source.{ext}"
    shutil.move(str(tmp_path), dest)
    with db.connect() as c:
        c.execute(
            "INSERT INTO documents (id, workspace_id, filename, sha256, file_type, status, stage, created_at, source_path)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (doc_id, workspace_id, filename, sha, ext, "queued", "Queued", time.time(), str(dest)),
        )
    db.audit("document.upload", workspace_id, doc_id, {"sha256": sha, "bytes": size, "type": ext})
    _executor.submit(_run, workspace_id, doc_id, dest, filename)
    from rag import workspaces

    return {**workspaces.document(workspace_id, doc_id), "duplicate": False}


def _set(doc_id: str, **cols: Any) -> None:
    sets = ", ".join(f"{k}=?" for k in cols)
    with db.connect() as c:
        c.execute(f"UPDATE documents SET {sets} WHERE id=?", (*cols.values(), doc_id))


def _run(workspace_id: str, doc_id: str, path: Path, filename: str) -> None:
    t0 = time.time()
    try:
        _set(doc_id, status="parsing", stage="Parsing", progress=0.1)
        resp = parse_path(path, filename, document_id=doc_id, budget_seconds=config.PARSE_BUDGET_S)
        _set(doc_id, status="indexing", stage="Indexing", progress=0.5, parse_status=resp.status,
             page_count=resp.page_count, parse_ms=resp.processing_time_ms,
             preview_available=int(resp.preview_available), preview_pages=resp.preview_pages,
             preview_error=resp.preview_error)
        index_document(workspace_id, doc_id, path, filename, resp.blocks, resp.file_type)
        db.audit("document.indexed", workspace_id, doc_id, {"seconds": round(time.time() - t0, 1)})
    except AppError as exc:
        logger.warning("ingest failed doc=%s code=%s", doc_id, exc.code)
        _set(doc_id, status="failed", stage="Failed", error=f"[{exc.code}] {exc.message}")
        db.audit("document.failed", workspace_id, doc_id, {"code": exc.code})
    except Exception as exc:  # noqa: BLE001
        logger.exception("ingest crashed doc=%s", doc_id)
        _set(doc_id, status="failed", stage="Failed", error=f"[INTERNAL_ERROR] {type(exc).__name__}")
        db.audit("document.failed", workspace_id, doc_id, {"code": "INTERNAL_ERROR"})


def index_document(workspace_id: str, doc_id: str, path: Path | None, filename: str,
                   blocks: list[DocumentBlock], file_type: str) -> None:
    """(Re)build everything derived from a parsed document. Safe to call again after a correction."""
    # 1. persist parsed blocks (the source of truth for citations and corrections)
    with db.connect() as c:
        c.execute("DELETE FROM blocks WHERE doc_id=?", (doc_id,))
        c.executemany(
            "INSERT INTO blocks (doc_id, block_id, ord, json) VALUES (?,?,?,?)",
            [(doc_id, b.id, i, db.jdump(b.model_dump(mode="json"))) for i, b in enumerate(blocks)],
        )

    # 2. document type from a text sample
    sample = " ".join(b.content for b in blocks if b.type.value in ("heading", "paragraph") and b.content)[:8000]
    doc_type = meta.guess_doc_type(filename, sample, file_type)

    # 3. injection / hidden-content shield (block level, before chunking)
    quarantined: dict[str, list[security.Finding]] = {}
    for b in blocks:
        if b.type.value in ("heading", "paragraph", "list", "table", "figure"):
            text = b.content or ""
            if b.type.value == "table":
                text = " ".join(str(c) for r in (b.metadata.get("rows") or []) for c in r if c)
            found = security.scan_text(text)
            if found:
                quarantined[b.id] = found
            if security.homoglyph_suspected(text):  # flagged for review, not quarantined
                b.metadata.setdefault("flags", []).append("homoglyph_suspected")
                b.requires_review = True
    extra_findings: list[tuple[str, str, int | None, list | None, str]] = []  # reason, kind, page, bbox, snippet
    hidden_spans: list = []
    active: list = []
    try:
        from rag import hidden

        hidden_spans = hidden.scan(path, file_type, blocks)
        active = hidden.active_findings(path, file_type)
    except ImportError:
        pass
    for hs in hidden_spans:
        if hs.block_id:
            quarantined.setdefault(hs.block_id, []).append(security.Finding(hs.reason, hs.text[:160]))
        else:
            extra_findings.append((hs.reason, "hidden", hs.page, hs.bbox, hs.text[:300]))
    for af in active:
        extra_findings.append((af.reason, "finding", None, None, f"{af.detail} (action: {af.action_taken})"))
    security_flags = sorted({f.reason for fs in quarantined.values() for f in fs} | {e[0] for e in extra_findings})
    clean_blocks = [b for b in blocks if b.id not in quarantined]

    # 4. chunk (quarantined blocks never enter normal chunks)
    built = chunker.build_chunks(clean_blocks, doc_type, filename)
    q_built = chunker.build_chunks([b for b in blocks if b.id in quarantined], doc_type, filename)

    # 5. embed (skip chunks whose content hash is unchanged)
    with db.connect() as c:
        old = {r["content_hash"]: r["embedding"] for r in
               c.execute("SELECT content_hash, embedding FROM chunks WHERE doc_id=? AND embedding IS NOT NULL", (doc_id,))}
    all_chunks = [(ch, "normal") for ch in built.chunks] + [(ch, "quarantined") for ch in q_built.chunks]
    todo = [i for i, (ch, _) in enumerate(all_chunks) if ch.content_hash not in old]
    _set(doc_id, stage="Embedding", progress=0.7)
    vectors: dict[int, bytes] = {}
    if todo:
        mat = embed.embed_texts([all_chunks[i][0].embed_text for i in todo])
        for i, v in zip(todo, mat):
            vectors[i] = np.asarray(v, dtype=np.float32).tobytes()

    # 6. store
    flags_all: set[str] = set()
    flagged_blocks = 0
    for b in blocks:
        f = set(b.metadata.get("flags") or [])
        if b.requires_review:
            f.add("needs_review")
        if f:
            flagged_blocks += 1
            flags_all |= f
    with db.connect() as c:
        c.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        c.execute("DELETE FROM tables_store WHERE doc_id=?", (doc_id,))
        c.execute("DELETE FROM quarantine WHERE doc_id=?", (doc_id,))
        for i, (ch, trust) in enumerate(all_chunks):
            emb = vectors.get(i) or old.get(ch.content_hash)
            c.execute(
                "INSERT INTO chunks (chunk_id, doc_id, workspace_id, kind, text, embed_text, heading_json, block_ids_json,"
                " pages_json, printed_json, bboxes_json, min_confidence, flags_json, meta_json, trust, table_ref,"
                " content_hash, embedding, ord) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"{doc_id}:{i}", doc_id, workspace_id, ch.kind, security.clean_for_index(ch.text), ch.embed_text,
                 db.jdump(ch.heading_path), db.jdump(ch.block_ids), db.jdump(ch.pages), db.jdump(ch.printed_pages),
                 db.jdump(ch.bboxes), ch.min_confidence, db.jdump(ch.flags), db.jdump(ch.meta), trust, ch.table_ref,
                 ch.content_hash, emb, i),
            )
        for t in built.tables:
            c.execute(
                "INSERT INTO tables_store (table_id, doc_id, workspace_id, block_id, title, page, unit, scale, currency,"
                " statement, grid_json, printed_json, conf, estimated, kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"{doc_id}:{t.table_ref}", doc_id, workspace_id, t.table_ref, t.title, t.page, t.unit, t.scale,
                 t.currency, t.statement, db.jdump(t.grid), db.jdump(t.printed), t.conf, int(t.estimated), t.kind),
            )
        by_id = {b.id: b for b in blocks}
        block_to_chunk = {bid: f"{doc_id}:{i}" for i, (ch, trust) in enumerate(all_chunks) if trust == "quarantined" for bid in ch.block_ids}
        for reason, kind, page, bbox, snippet in extra_findings:
            c.execute(
                "INSERT INTO quarantine (id, workspace_id, doc_id, chunk_id, block_id, reason, kind, page, bbox_json, snippet, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (db.new_id(), workspace_id, doc_id, None, None, reason, kind, page, db.jdump(bbox), snippet, time.time()))
        for bid, findings in quarantined.items():
            b = by_id[bid]
            lc = chunker.loc(b)
            for f in findings:
                c.execute(
                    "INSERT INTO quarantine (id, workspace_id, doc_id, chunk_id, block_id, reason, kind, page, bbox_json,"
                    " snippet, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (db.new_id(), workspace_id, doc_id, block_to_chunk.get(bid), bid, f.reason, b.type.value, lc["page"],
                     db.jdump(lc["bbox"]), f.detail[:300], time.time()),
                )
        fact_rows = []
        for t in built.tables:
            fact_rows += facts_mod.facts_from_table(t, doc_type)
        for ch in built.chunks:
            if ch.kind == "section" and ch.bboxes:
                fact_rows += facts_mod.facts_from_text(ch.text, ch.pages[0] if ch.pages else 1, ch.bboxes[0].get("bbox"),
                                                       ch.block_ids[0] if ch.block_ids else "", " ".join(ch.heading_path))
        c.execute("DELETE FROM facts WHERE doc_id=?", (doc_id,))
        seen_f = set()
        for f in fact_rows:
            k = (f["concept"], f["period"], round(f["value"], 6), f["page"])
            if k in seen_f:
                continue
            seen_f.add(k)
            c.execute(
                "INSERT INTO facts (workspace_id, doc_id, concept, value, unit, scale, currency, period, block_id, page, bbox_json,"
                " confidence, label, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (workspace_id, doc_id, f["concept"], f["value"], f["unit"], f["scale"], f["currency"], f["period"], f["block_id"],
                 f["page"], db.jdump(f["bbox"]), f["confidence"], f["label"], f["raw"]))
        c.execute(
            "UPDATE documents SET doc_type=?, status='ready', stage='Ready', progress=1.0, indexed_at=?, block_count=?,"
            " chunk_count=?, quarantined_count=?, flag_count=?, flags_json=?, error=NULL WHERE id=?",
            (doc_type, time.time(), len(blocks), len(built.chunks), len(quarantined) + len(extra_findings), flagged_blocks,
             db.jdump(sorted(flags_all | {f"security:{r}" for r in security_flags})), doc_id),
        )
    index.invalidate(workspace_id)
