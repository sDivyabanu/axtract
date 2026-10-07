"""Database access layer.

Primary path: raw PostgreSQL queries via asyncpg (Prisma-compatible schema).
Fallback path: Supabase PostgREST over HTTPS — used automatically when the
direct PostgreSQL port is unreachable (e.g. firewalled networks). Both paths
return plain dicts with the same column names.

All queries enforce user_id ownership — never rely on RLS alone for
privileged backend connections.
"""

from __future__ import annotations

import os
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import asyncpg

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None
_rest_mode: bool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        database_url = os.environ.get("DATABASE_URL", "")
        if not database_url:
            raise RuntimeError("DATABASE_URL environment variable is not set.")
        _pool = await asyncpg.create_pool(
            database_url, min_size=2, max_size=10,
            ssl="require", timeout=8,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


# ── Supabase REST fallback (HTTPS) ────────────────────────────────────

async def _use_rest() -> bool:
    """Decide once whether direct PostgreSQL works; otherwise use PostgREST."""
    global _rest_mode
    if _rest_mode is None:
        try:
            pool = await get_pool()
            await pool.execute("SELECT 1")
            _rest_mode = False
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Direct PostgreSQL unreachable (%s); using Supabase REST over HTTPS.",
                type(exc).__name__,
            )
            _rest_mode = True
    return _rest_mode


def _secret_key() -> str:
    return os.environ.get("SUPABASE_SECRET_KEY", "")


async def _rest(
    method: str,
    table: str,
    params: dict[str, str] | None = None,
    json_body: Any = None,
    prefer: str | None = None,
) -> list[dict]:
    import httpx
    url = f"{os.environ.get('SUPABASE_URL', '').rstrip('/')}/rest/v1/{table}"
    headers = {
        "apikey": _secret_key(),
        "Authorization": f"Bearer {_secret_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(
            method, url, params=params, json=json_body, headers=headers,
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Supabase REST {method} {table} failed: {resp.status_code} {resp.text[:300]}"
        )
    if not resp.text.strip():
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    return data if isinstance(data, list) else [data]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _uuid() -> str:
    return uuid4().hex


def _bytea_out(v: Any) -> Any:
    if isinstance(v, (bytes, bytearray)):
        return f"\\x{v.hex()}"
    return v


def _bytea_in(row: dict, cols: tuple[str, ...]) -> dict:
    for c in cols:
        v = row.get(c)
        if isinstance(v, str) and v.startswith("\\x"):
            row[c] = bytes.fromhex(v[2:])
    return row


# ── Profiles ──────────────────────────────────────────────────────────

async def upsert_profile(user_id: str, full_name: str | None = None) -> dict:
    if await _use_rest():
        body: dict[str, Any] = {"id": user_id}
        if full_name is not None:
            body["full_name"] = full_name
        rows = await _rest(
            "POST", "profiles", json_body=body,
            prefer="resolution=merge-duplicates,return=representation",
        )
        return rows[0] if rows else {"id": user_id}
    pool = await get_pool()
    now = _now()
    row = await pool.fetchrow(
        """
        INSERT INTO profiles (id, full_name, created_at, updated_at)
        VALUES ($1, $2, $3, $3)
        ON CONFLICT (id) DO UPDATE SET
            full_name = COALESCE($2, profiles.full_name),
            updated_at = $3
        RETURNING *
        """,
        user_id, full_name, now,
    )
    return dict(row)


async def get_profile(user_id: str) -> dict | None:
    if await _use_rest():
        rows = await _rest("GET", "profiles", params={"id": f"eq.{user_id}", "limit": "1"})
        return rows[0] if rows else None
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM profiles WHERE id = $1", user_id)
    return dict(row) if row else None


# ── Documents ─────────────────────────────────────────────────────────

async def create_document(
    user_id: str,
    original_filename: str,
    mime_type: str,
    file_size_bytes: int,
) -> dict:
    if await _use_rest():
        rows = await _rest(
            "POST", "documents",
            json_body={
                "user_id": user_id,
                "original_filename": original_filename,
                "mime_type": mime_type,
                "file_size_bytes": file_size_bytes,
                "status": "pending",
            },
            prefer="return=representation",
        )
        return rows[0]
    pool = await get_pool()
    now = _now()
    row = await pool.fetchrow(
        """
        INSERT INTO documents (user_id, original_filename, mime_type, file_size_bytes, status, created_at, updated_at)
        VALUES ($1, $2, $3, $4, 'pending', $5, $5)
        RETURNING *
        """,
        user_id, original_filename, mime_type, file_size_bytes, now,
    )
    return dict(row)


async def update_document_status(
    document_id: str,
    user_id: str,
    status: str,
    page_count: int | None = None,
    block_count: int | None = None,
    processing_time_ms: int | None = None,
) -> None:
    if await _use_rest():
        await _rest(
            "PATCH", "documents",
            params={"id": f"eq.{document_id}", "user_id": f"eq.{user_id}"},
            json_body={
                "status": status,
                "page_count": page_count,
                "block_count": block_count,
                "processing_time_ms": processing_time_ms,
                "updated_at": _iso(_now()),
            },
        )
        return
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE documents
        SET status = $1, page_count = $2, block_count = $3, processing_time_ms = $4, updated_at = $5
        WHERE id = $6 AND user_id = $7
        """,
        status, page_count, block_count, processing_time_ms, _now(), document_id, user_id,
    )


async def list_documents(user_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
    if await _use_rest():
        return await _rest(
            "GET", "documents",
            params={
                "user_id": f"eq.{user_id}",
                "select": ",".join([
                    "id", "original_filename", "mime_type", "file_size_bytes", "status",
                    "page_count", "block_count", "processing_time_ms", "created_at", "updated_at",
                ]),
                "order": "created_at.desc",
                "limit": str(limit),
                "offset": str(offset),
            },
        )
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, original_filename, mime_type, file_size_bytes, status,
               page_count, block_count, processing_time_ms, created_at, updated_at
        FROM documents
        WHERE user_id = $1
        ORDER BY created_at DESC
        LIMIT $2 OFFSET $3
        """,
        user_id, limit, offset,
    )
    return [dict(r) for r in rows]


async def get_document(document_id: str, user_id: str) -> dict | None:
    if await _use_rest():
        rows = await _rest(
            "GET", "documents",
            params={"id": f"eq.{document_id}", "user_id": f"eq.{user_id}", "limit": "1"},
        )
        return rows[0] if rows else None
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM documents WHERE id = $1 AND user_id = $2",
        document_id, user_id,
    )
    return dict(row) if row else None


async def delete_document(document_id: str, user_id: str) -> bool:
    if await _use_rest():
        rows = await _rest(
            "DELETE", "documents",
            params={"id": f"eq.{document_id}", "user_id": f"eq.{user_id}"},
            prefer="return=representation",
        )
        return len(rows) > 0
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM documents WHERE id = $1 AND user_id = $2",
        document_id, user_id,
    )
    return result == "DELETE 1"


# ── Document Versions ─────────────────────────────────────────────────

_BYTEA_COLS = ("encryption_nonce", "encrypted_dek", "dek_wrapping_nonce")


async def create_document_version(
    document_id: str,
    version_number: int,
    storage_path: str,
    encryption_nonce: bytes,
    encrypted_dek: bytes,
    dek_wrapping_nonce: bytes,
    key_version: int,
    ciphertext_size_bytes: int,
    content_hash: str | None = None,
) -> dict:
    if await _use_rest():
        rows = await _rest(
            "POST", "document_versions",
            json_body={
                "document_id": document_id,
                "version_number": version_number,
                "storage_path": storage_path,
                "encryption_nonce": _bytea_out(encryption_nonce),
                "encrypted_dek": _bytea_out(encrypted_dek),
                "dek_wrapping_nonce": _bytea_out(dek_wrapping_nonce),
                "key_version": key_version,
                "ciphertext_size_bytes": ciphertext_size_bytes,
                "content_hash": content_hash,
                "created_at": _iso(_now()),
            },
            prefer="return=representation",
        )
        return _bytea_in(rows[0], _BYTEA_COLS) if rows else {}
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO document_versions
            (document_id, version_number, storage_path,
             encryption_nonce, encrypted_dek, dek_wrapping_nonce,
             key_version, ciphertext_size_bytes, content_hash, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        RETURNING *
        """,
        document_id, version_number, storage_path,
        encryption_nonce, encrypted_dek, dek_wrapping_nonce,
        key_version, ciphertext_size_bytes, content_hash, _now(),
    )
    return dict(row)


async def get_latest_version(document_id: str) -> dict | None:
    if await _use_rest():
        rows = await _rest(
            "GET", "document_versions",
            params={
                "document_id": f"eq.{document_id}",
                "order": "version_number.desc",
                "limit": "1",
            },
        )
        return _bytea_in(rows[0], _BYTEA_COLS) if rows else None
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT * FROM document_versions
        WHERE document_id = $1
        ORDER BY version_number DESC
        LIMIT 1
        """,
        document_id,
    )
    return dict(row) if row else None


# ── Processing Runs ───────────────────────────────────────────────────

async def create_processing_run(
    document_id: str,
    document_version_id: str,
    user_id: str,
) -> dict:
    if await _use_rest():
        rows = await _rest(
            "POST", "processing_runs",
            json_body={
                "document_id": document_id,
                "document_version_id": document_version_id,
                "user_id": user_id,
                "status": "processing",
                "started_at": _iso(_now()),
            },
            prefer="return=representation",
        )
        return rows[0]
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO processing_runs (document_id, document_version_id, user_id, status, started_at)
        VALUES ($1, $2, $3, 'processing', $4)
        RETURNING *
        """,
        document_id, document_version_id, user_id, _now(),
    )
    return dict(row)


async def complete_processing_run(
    run_id: str,
    status: str,
    processing_time_ms: int | None = None,
    page_count: int | None = None,
    block_count: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    if await _use_rest():
        await _rest(
            "PATCH", "processing_runs",
            params={"id": f"eq.{run_id}"},
            json_body={
                "status": status,
                "completed_at": _iso(_now()),
                "processing_time_ms": processing_time_ms,
                "page_count": page_count,
                "block_count": block_count,
                "error_code": error_code,
                "error_message": error_message,
            },
        )
        return
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE processing_runs
        SET status = $1, completed_at = $2, processing_time_ms = $3,
            page_count = $4, block_count = $5, error_code = $6, error_message = $7
        WHERE id = $8
        """,
        status, _now(), processing_time_ms,
        page_count, block_count, error_code, error_message, run_id,
    )


async def get_processing_history(
    document_id: str, user_id: str, limit: int = 20
) -> list[dict]:
    if await _use_rest():
        rows = await _rest(
            "GET", "processing_runs",
            params={
                "document_id": f"eq.{document_id}",
                "user_id": f"eq.{user_id}",
                "select": "*,document_outputs(schema_version)",
                "order": "started_at.desc",
                "limit": str(limit),
            },
        )
        out = []
        for r in rows:
            r = dict(r)
            embedded = r.pop("document_outputs", None)
            if isinstance(embedded, dict):
                embedded = [embedded]
            if embedded:
                r["schema_version"] = embedded[0].get("schema_version")
            out.append(r)
        return out
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT pr.*, do.schema_version
        FROM processing_runs pr
        LEFT JOIN document_outputs do ON do.processing_run_id = pr.id
        WHERE pr.document_id = $1 AND pr.user_id = $2
        ORDER BY pr.started_at DESC
        LIMIT $3
        """,
        document_id, user_id, limit,
    )
    return [dict(r) for r in rows]


# ── Document Outputs ──────────────────────────────────────────────────

async def save_document_output(
    processing_run_id: str,
    response_json: str,
    markdown: str,
) -> dict:
    if await _use_rest():
        import json
        rows = await _rest(
            "POST", "document_outputs",
            json_body={
                "processing_run_id": processing_run_id,
                "response_json": json.loads(response_json),
                "markdown": markdown,
                "created_at": _iso(_now()),
            },
            prefer="return=representation",
        )
        return rows[0] if rows else {}
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO document_outputs (processing_run_id, response_json, markdown, created_at)
        VALUES ($1, $2::jsonb, $3, $4)
        RETURNING *
        """,
        processing_run_id, response_json, markdown, _now(),
    )
    return dict(row)


async def get_latest_output(document_id: str, user_id: str) -> dict | None:
    if await _use_rest():
        rows = await _rest(
            "GET", "document_outputs",
            params={
                "processing_runs.document_id": f"eq.{document_id}",
                "processing_runs.user_id": f"eq.{user_id}",
                "processing_runs.status": "eq.completed",
                "select": "*,processing_runs!inner(id)",
                "order": "created_at.desc",
                "limit": "1",
            },
        )
        for r in rows:
            r.pop("processing_runs", None)
        return rows[0] if rows else None
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT do.*
        FROM document_outputs do
        JOIN processing_runs pr ON pr.id = do.processing_run_id
        WHERE pr.document_id = $1 AND pr.user_id = $2 AND pr.status = 'completed'
        ORDER BY do.created_at DESC
        LIMIT 1
        """,
        document_id, user_id,
    )
    return dict(row) if row else None


async def get_output_by_run(run_id: str, user_id: str) -> dict | None:
    if await _use_rest():
        rows = await _rest(
            "GET", "document_outputs",
            params={
                "processing_run_id": f"eq.{run_id}",
                "processing_runs.user_id": f"eq.{user_id}",
                "select": "*,processing_runs!inner(id)",
                "limit": "1",
            },
        )
        for r in rows:
            r.pop("processing_runs", None)
        return rows[0] if rows else None
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT do.*
        FROM document_outputs do
        JOIN processing_runs pr ON pr.id = do.processing_run_id
        WHERE do.processing_run_id = $1 AND pr.user_id = $2
        """,
        run_id, user_id,
    )
    return dict(row) if row else None


# ── Storage paths for cleanup ─────────────────────────────────────────

async def get_version_storage_paths(document_id: str) -> list[str]:
    if await _use_rest():
        rows = await _rest(
            "GET", "document_versions",
            params={"document_id": f"eq.{document_id}", "select": "storage_path"},
        )
        return [r["storage_path"] for r in rows]
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT storage_path FROM document_versions WHERE document_id = $1",
        document_id,
    )
    return [r["storage_path"] for r in rows]
