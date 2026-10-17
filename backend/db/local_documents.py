"""Persistent SQLite document adapter, exclusively for the isolated development identity.

Originals remain envelope-encrypted on disk. No Supabase client or database is used.
All records (including child records addressed by ID) stay in this separate namespace.
"""
from __future__ import annotations

import base64
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from uuid import uuid4

from starlette.concurrency import run_in_threadpool
from auth.local_dev import LOCAL_USER_ID, data_dir

_BYTES = ("encryption_nonce", "encrypted_dek", "dek_wrapping_nonce")


def _async(fn):
    @wraps(fn)
    async def call(*args, **kwargs):
        return await run_in_threadpool(fn, *args, **kwargs)
    return call


def _now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _db():
    conn = sqlite3.connect(data_dir() / "documents.sqlite3", timeout=15)
    try:
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT PRIMARY KEY, document_id TEXT, payload TEXT NOT NULL)")
            yield conn
    finally:
        conn.close()


def _put(conn, kind, row, document_id=None):
    encoded = {k: base64.b64encode(v).decode() if k in _BYTES and isinstance(v, bytes) else v for k, v in row.items()}
    conn.execute("INSERT OR REPLACE INTO records VALUES (?, ?, ?, ?)",
                 (kind, row["id"], document_id, json.dumps(encoded)))
    return row


def _rows(conn, kind, document_id=None):
    sql, args = "SELECT payload FROM records WHERE kind=?", [kind]
    if document_id is not None:
        sql += " AND document_id=?"
        args.append(document_id)
    result = []
    for (payload,) in conn.execute(sql, args):
        row = json.loads(payload)
        for key in _BYTES:
            if key in row:
                row[key] = base64.b64decode(row[key])
        result.append(row)
    return result


def _get(conn, kind, identity):
    return next((row for row in _rows(conn, kind) if row["id"] == identity), None)


def _owner(user_id):
    if user_id != LOCAL_USER_ID:
        raise ValueError("Real user identities cannot access the local development repository.")


def _document(conn, identity):
    row = _get(conn, "document", identity)
    if not row or row["user_id"] != LOCAL_USER_ID:
        raise ValueError("Local document not found.")
    return row


def _run(conn, identity):
    row = _get(conn, "run", identity)
    if not row or row["user_id"] != LOCAL_USER_ID:
        raise ValueError("Local processing run not found.")
    _document(conn, row["document_id"])
    return row


@_async
def upsert_profile(user_id, full_name=None):
    _owner(user_id)
    with _db() as conn:
        row = _get(conn, "profile", user_id) or {"id": user_id, "created_at": _now(), "avatar_url": None}
        row["full_name"] = full_name or row.get("full_name", "Local development")
        return _put(conn, "profile", row)


@_async
def get_profile(user_id):
    if user_id != LOCAL_USER_ID:
        return None
    with _db() as conn:
        return _get(conn, "profile", user_id)


@_async
def create_document(user_id, original_filename, mime_type, file_size_bytes):
    _owner(user_id)
    row = dict(id=str(uuid4()), user_id=user_id, original_filename=original_filename,
               mime_type=mime_type, file_size_bytes=file_size_bytes, status="pending",
               created_at=_now(), updated_at=_now(), page_count=None, block_count=None, processing_time_ms=None)
    with _db() as conn:
        return _put(conn, "document", row)


@_async
def update_document_status(document_id, user_id, status, page_count=None, block_count=None, processing_time_ms=None):
    _owner(user_id)
    with _db() as conn:
        row = _document(conn, document_id)
        row.update(status=status, page_count=page_count, block_count=block_count,
                   processing_time_ms=processing_time_ms, updated_at=_now())
        _put(conn, "document", row)


@_async
def list_documents(user_id, limit=50, offset=0):
    if user_id != LOCAL_USER_ID:
        return []
    with _db() as conn:
        rows = [row for row in _rows(conn, "document") if row["user_id"] == user_id]
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)[max(0, offset):max(0, offset) + max(0, limit)]


@_async
def get_document(document_id, user_id):
    if user_id != LOCAL_USER_ID:
        return None
    with _db() as conn:
        row = _get(conn, "document", document_id)
        return row if row and row["user_id"] == user_id else None


@_async
def delete_document(document_id, user_id):
    _owner(user_id)
    with _db() as conn:
        _document(conn, document_id)
        conn.execute("DELETE FROM records WHERE id=? OR document_id=?", (document_id, document_id))
    return True


@_async
def create_document_version(document_id, version_number, storage_path, encryption_nonce, encrypted_dek,
                            dek_wrapping_nonce, key_version, ciphertext_size_bytes, content_hash=None):
    with _db() as conn:
        _document(conn, document_id)
        row = dict(id=str(uuid4()), document_id=document_id, version_number=version_number, storage_path=storage_path,
                   encryption_nonce=encryption_nonce, encrypted_dek=encrypted_dek, dek_wrapping_nonce=dek_wrapping_nonce,
                   key_version=key_version, ciphertext_size_bytes=ciphertext_size_bytes, content_hash=content_hash, created_at=_now())
        return _put(conn, "version", row, document_id)


@_async
def get_latest_version(document_id):
    with _db() as conn:
        _document(conn, document_id)
        return max(_rows(conn, "version", document_id), key=lambda row: row["version_number"], default=None)


@_async
def create_processing_run(document_id, document_version_id, user_id):
    _owner(user_id)
    with _db() as conn:
        _document(conn, document_id)
        version = _get(conn, "version", document_version_id)
        if not version or version["document_id"] != document_id:
            raise ValueError("Version does not belong to this local document.")
        row = dict(id=str(uuid4()), document_id=document_id, document_version_id=document_version_id,
                   user_id=user_id, status="processing", started_at=_now())
        return _put(conn, "run", row, document_id)


@_async
def complete_processing_run(run_id, status, processing_time_ms=None, page_count=None, block_count=None,
                            error_code=None, error_message=None):
    with _db() as conn:
        row = _run(conn, run_id)
        row.update(status=status, completed_at=_now(), processing_time_ms=processing_time_ms,
                   page_count=page_count, block_count=block_count, error_code=error_code, error_message=error_message)
        _put(conn, "run", row, row["document_id"])


@_async
def save_document_output(processing_run_id, response_json, markdown):
    with _db() as conn:
        run = _run(conn, processing_run_id)
        row = dict(id=str(uuid4()), processing_run_id=processing_run_id, response_json=json.loads(response_json),
                   markdown=markdown, created_at=_now(), schema_version="1.0")
        return _put(conn, "output", row, run["document_id"])


def _output(conn, run_id):
    return next((row for row in _rows(conn, "output") if row["processing_run_id"] == run_id), None)


def _latest(conn, document_id):
    runs = sorted(_rows(conn, "run", document_id), key=lambda row: row["started_at"], reverse=True)
    return next((row for run in runs if run["status"] == "completed" and (row := _output(conn, run["id"]))), None)


@_async
def get_processing_history(document_id, user_id, limit=20):
    _owner(user_id)
    with _db() as conn:
        _document(conn, document_id)
        rows = sorted(_rows(conn, "run", document_id), key=lambda row: row["started_at"], reverse=True)[:limit]
        return [dict(row, schema_version=(_output(conn, row["id"]) or {}).get("schema_version")) for row in rows]


@_async
def get_latest_output(document_id, user_id):
    if user_id != LOCAL_USER_ID:
        return None
    with _db() as conn:
        _document(conn, document_id)
        return _latest(conn, document_id)


@_async
def get_output_by_run(run_id, user_id):
    if user_id != LOCAL_USER_ID:
        return None
    with _db() as conn:
        _run(conn, run_id)
        return _output(conn, run_id)


@_async
def get_version_storage_paths(document_id):
    with _db() as conn:
        _document(conn, document_id)
        return [row["storage_path"] for row in _rows(conn, "version", document_id)]


@_async
def update_latest_output_json(document_id, user_id, response_json):
    _owner(user_id)
    with _db() as conn:
        _document(conn, document_id)
        row = _latest(conn, document_id)
        if row is None:
            return False
        row["response_json"] = json.loads(response_json)
        _put(conn, "output", row, document_id)
        return True
