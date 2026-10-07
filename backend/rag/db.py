"""SQLite store. Every row that holds data-room content carries a workspace_id."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from rag import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, filename TEXT NOT NULL, sha256 TEXT NOT NULL,
  doc_type TEXT DEFAULT 'other', file_type TEXT, page_count INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'queued', stage TEXT DEFAULT '', progress REAL DEFAULT 0, error TEXT,
  parse_status TEXT, flags_json TEXT DEFAULT '[]', flag_count INTEGER DEFAULT 0,
  block_count INTEGER DEFAULT 0, chunk_count INTEGER DEFAULT 0, quarantined_count INTEGER DEFAULT 0,
  preview_available INTEGER DEFAULT 0, preview_pages INTEGER DEFAULT 0, preview_error TEXT,
  parse_ms INTEGER DEFAULT 0, created_at REAL NOT NULL, indexed_at REAL, source_path TEXT);
CREATE INDEX IF NOT EXISTS ix_doc_ws ON documents(workspace_id);
CREATE TABLE IF NOT EXISTS blocks (
  doc_id TEXT NOT NULL, block_id TEXT NOT NULL, ord INTEGER NOT NULL, json TEXT NOT NULL,
  PRIMARY KEY (doc_id, block_id));
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, workspace_id TEXT NOT NULL, kind TEXT NOT NULL,
  text TEXT NOT NULL, embed_text TEXT NOT NULL, heading_json TEXT, block_ids_json TEXT, pages_json TEXT,
  printed_json TEXT, bboxes_json TEXT, min_confidence REAL, flags_json TEXT, meta_json TEXT,
  trust TEXT NOT NULL DEFAULT 'normal', table_ref TEXT, content_hash TEXT NOT NULL, embedding BLOB,
  ord INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_chunk_ws ON chunks(workspace_id);
CREATE INDEX IF NOT EXISTS ix_chunk_doc ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS tables_store (
  table_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, workspace_id TEXT NOT NULL, block_id TEXT,
  title TEXT, page INTEGER, unit TEXT, scale REAL, currency TEXT, statement TEXT, grid_json TEXT NOT NULL,
  printed_json TEXT, conf REAL, estimated INTEGER DEFAULT 0, kind TEXT DEFAULT 'table');
CREATE INDEX IF NOT EXISTS ix_tab_ws ON tables_store(workspace_id);
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL, doc_id TEXT NOT NULL, concept TEXT NOT NULL,
  value REAL NOT NULL, unit TEXT, scale REAL, currency TEXT, period TEXT, block_id TEXT, page INTEGER,
  bbox_json TEXT, confidence REAL, label TEXT, raw TEXT);
CREATE INDEX IF NOT EXISTS ix_fact_ws ON facts(workspace_id, concept, period);
CREATE TABLE IF NOT EXISTS quarantine (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, doc_id TEXT NOT NULL, chunk_id TEXT, block_id TEXT,
  reason TEXT NOT NULL, kind TEXT, page INTEGER, bbox_json TEXT, snippet TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_q_ws ON quarantine(workspace_id);
CREATE TABLE IF NOT EXISTS answers (
  answer_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, created_at REAL NOT NULL, mode TEXT, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, workspace_id TEXT, event TEXT NOT NULL,
  ref TEXT, detail_json TEXT);
CREATE TABLE IF NOT EXISTS pack_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL, pack TEXT NOT NULL, ts REAL NOT NULL, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL, doc_id TEXT NOT NULL, table_id TEXT,
  row_idx INTEGER, col_idx INTEGER, old_value TEXT, new_value TEXT, ts REAL NOT NULL);
"""

_init_lock = threading.Lock()
_initialised: set[str] = set()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a database was first created."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(tables_store)")}
    for col, ddl in (("printed_json", "TEXT"), ("conf", "REAL"), ("estimated", "INTEGER DEFAULT 0"), ("kind", "TEXT DEFAULT 'table'")):
        if col not in have:
            conn.execute(f"ALTER TABLE tables_store ADD COLUMN {col} {ddl}")
    have_f = {r["name"] for r in conn.execute("PRAGMA table_info(facts)")}
    if "raw" not in have_f:
        conn.execute("ALTER TABLE facts ADD COLUMN raw TEXT")
    conn.commit()


def _connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    key = str(config.DB_PATH)
    if key not in _initialised:
        with _init_lock:
            conn.executescript(_SCHEMA)
            _migrate(conn)
            _initialised.add(key)
    return conn


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def new_id() -> str:
    return uuid.uuid4().hex


def jdump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def jload(s: str | None, default: Any = None) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except ValueError:
        return default


def audit(event: str, workspace_id: str | None = None, ref: str | None = None, detail: dict | None = None) -> None:
    """Audit entries hold ids, hashes and timings only: never document text."""
    with connect() as c:
        c.execute(
            "INSERT INTO audit (ts, workspace_id, event, ref, detail_json) VALUES (?,?,?,?,?)",
            (time.time(), workspace_id, event, ref, jdump(detail or {})),
        )
