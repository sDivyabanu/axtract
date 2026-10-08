"""Workspaces (= deals / data rooms) and their documents."""

from __future__ import annotations

import time
from typing import Any

from models.errors import AppError
from rag import db


def create(name: str) -> dict[str, Any]:
    name = " ".join((name or "").split())[:120]
    if not name:
        raise AppError("INVALID_REQUEST", "Workspace name is required.", status_code=422)
    wid = db.new_id()
    with db.connect() as c:
        c.execute("INSERT INTO workspaces (id, name, created_at) VALUES (?,?,?)", (wid, name, time.time()))
    db.audit("workspace.create", wid)
    return {"workspace_id": wid, "name": name}


def require(workspace_id: str) -> dict[str, Any]:
    with db.connect() as c:
        row = c.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
    if row is None:
        raise AppError("WORKSPACE_NOT_FOUND", "Unknown workspace.", status_code=404)
    return {"workspace_id": row["id"], "name": row["name"], "created_at": row["created_at"]}


def list_all() -> list[dict[str, Any]]:
    with db.connect() as c:
        rows = c.execute(
            "SELECT w.id, w.name, w.created_at, (SELECT COUNT(*) FROM documents d WHERE d.workspace_id=w.id) AS docs "
            "FROM workspaces w ORDER BY w.created_at DESC").fetchall()
    return [{"workspace_id": r["id"], "name": r["name"], "created_at": r["created_at"], "documents": r["docs"]} for r in rows]


def doc_view(r) -> dict[str, Any]:
    return {
        "doc_id": r["id"], "workspace_id": r["workspace_id"], "filename": r["filename"], "sha256": r["sha256"],
        "doc_type": r["doc_type"], "file_type": r["file_type"], "page_count": r["page_count"], "status": r["status"],
        "stage": r["stage"], "progress": r["progress"], "error": r["error"], "parse_status": r["parse_status"],
        "flags": db.jload(r["flags_json"], []), "flag_count": r["flag_count"], "block_count": r["block_count"],
        "chunk_count": r["chunk_count"], "quarantined_count": r["quarantined_count"],
        "preview_available": bool(r["preview_available"]), "preview_pages": r["preview_pages"],
        "preview_error": r["preview_error"], "parse_ms": r["parse_ms"], "created_at": r["created_at"],
        "indexed_at": r["indexed_at"],
    }


def documents(workspace_id: str) -> list[dict[str, Any]]:
    with db.connect() as c:
        rows = c.execute("SELECT * FROM documents WHERE workspace_id=? ORDER BY created_at", (workspace_id,)).fetchall()
    return [doc_view(r) for r in rows]


def document(workspace_id: str, doc_id: str) -> dict[str, Any]:
    with db.connect() as c:
        r = c.execute("SELECT * FROM documents WHERE id=? AND workspace_id=?", (doc_id, workspace_id)).fetchone()
    if r is None:
        raise AppError("DOCUMENT_NOT_FOUND", "Unknown document in this workspace.", status_code=404)
    return doc_view(r)


def get(workspace_id: str) -> dict[str, Any]:
    ws = require(workspace_id)
    return {**ws, "documents": documents(workspace_id)}
