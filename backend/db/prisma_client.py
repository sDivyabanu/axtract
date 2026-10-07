"""Prisma client wrapper using raw PostgreSQL queries via asyncpg.

Prisma's Python client (prisma-client-py) is community-maintained and
may lag behind schema changes. For the FastAPI backend we use asyncpg
directly with parameterized queries for reliable async database access.

All queries enforce user_id ownership — never rely on RLS alone for
privileged backend connections.
"""

from __future__ import annotations

import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import asyncpg

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        database_url = os.environ.get("DATABASE_URL", "")
        if not database_url:
            raise RuntimeError("DATABASE_URL environment variable is not set.")
        _pool = await asyncpg.create_pool(database_url, min_size=2, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return uuid4().hex


# ── Profiles ──────────────────────────────────────────────────────────

async def upsert_profile(user_id: str, full_name: str | None = None) -> dict:
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
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM documents WHERE id = $1 AND user_id = $2",
        document_id, user_id,
    )
    return dict(row) if row else None


async def delete_document(document_id: str, user_id: str) -> bool:
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM documents WHERE id = $1 AND user_id = $2",
        document_id, user_id,
    )
    return result == "DELETE 1"


# ── Document Versions ─────────────────────────────────────────────────

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
    pool = await get_pool()
    import json
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
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT storage_path FROM document_versions WHERE document_id = $1",
        document_id,
    )
    return [r["storage_path"] for r in rows]
