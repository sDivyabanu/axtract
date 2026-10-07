"""Persistence layer on top of main's parser: runs against an in-memory fake DB/storage.

Real pieces: the FastAPI app, `parse_upload`, AES-256-GCM encryption, the preview cache.
Fakes: Postgres and Supabase Storage (no network, no real data touched).
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SAMPLE_PDF = Path(__file__).resolve().parents[2] / "sample_files" / "01_cross_page_table.pdf"


class FakeDb:
    def __init__(self):
        self.documents, self.versions, self.runs, self.outputs = {}, {}, {}, {}

    async def upsert_profile(self, user_id, full_name=None):
        return {"id": user_id}

    async def create_document(self, user_id, original_filename, mime_type, file_size_bytes):
        row = {"id": uuid.uuid4(), "user_id": user_id, "original_filename": original_filename,
               "mime_type": mime_type, "file_size_bytes": file_size_bytes, "status": "pending"}
        self.documents[str(row["id"])] = row
        return row

    async def delete_document(self, document_id, user_id):
        row = self.documents.get(document_id)
        if row and row["user_id"] == user_id:
            del self.documents[document_id]
            return True
        return False

    async def create_document_version(self, document_id, **kw):
        row = {"id": uuid.uuid4(), "document_id": document_id, "encryption_nonce": kw["encryption_nonce"],
               "encrypted_dek": kw["encrypted_dek"], "dek_wrapping_nonce": kw["dek_wrapping_nonce"],
               "storage_path": kw["storage_path"], "key_version": kw["key_version"],
               "content_hash": kw["content_hash"], "version_number": kw["version_number"]}
        self.versions[document_id] = row
        return row

    async def create_processing_run(self, document_id, version_id, user_id):
        row = {"id": uuid.uuid4(), "document_id": document_id, "user_id": user_id, "status": "processing"}
        self.runs[str(row["id"])] = row
        return row

    async def complete_processing_run(self, run_id, status, **kw):
        self.runs[run_id].update(status=status, **kw)

    async def update_document_status(self, document_id, user_id, status, **kw):
        self.documents[document_id]["status"] = status

    async def save_document_output(self, run_id, response_json, markdown):
        # asyncpg hands jsonb back as text, so store text like the real driver returns it.
        self.outputs[run_id] = {"response_json": response_json, "markdown": markdown,
                                "schema_version": "1.0", "created_at": "now"}
        return self.outputs[run_id]

    async def get_document(self, document_id, user_id):
        row = self.documents.get(document_id)
        return row if row and row["user_id"] == user_id else None

    async def get_latest_output(self, document_id, user_id):
        for run_id, run in self.runs.items():
            if run["document_id"] == document_id and run["user_id"] == user_id and run_id in self.outputs:
                return self.outputs[run_id]
        return None

    async def get_latest_version(self, document_id):
        return self.versions.get(document_id)

    async def list_documents(self, user_id, limit=50, offset=0):
        return [d for d in self.documents.values() if d["user_id"] == user_id]

    async def get_version_storage_paths(self, document_id):
        v = self.versions.get(document_id)
        return [v["storage_path"]] if v else []


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AXTRACT_MASTER_KEY_BASE64", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("AXTRACT_KEY_VERSION", "v1")  # the form used in the real .env
    import crypto.encryption as enc

    enc._master_key = None

    import main
    from auth.supabase import get_user_id
    from routers import documents

    fake, storage, who = FakeDb(), {}, {"user": "user-a"}
    for name in dir(FakeDb):
        if not name.startswith("_"):
            monkeypatch.setattr(documents.db, name, getattr(fake, name))

    async def up(path, data):
        storage[path] = data

    async def down(path):
        return storage[path]

    async def rm(path):
        storage.pop(path, None)

    monkeypatch.setattr(documents, "upload_encrypted", up)
    monkeypatch.setattr(documents, "download_encrypted", down)
    monkeypatch.setattr(documents, "delete_from_storage", rm)
    main.app.dependency_overrides[get_user_id] = lambda: who["user"]
    with TestClient(main.app) as client:
        yield client, fake, storage, who
    main.app.dependency_overrides.clear()
    enc._master_key = None


def _upload(client, path=SAMPLE_PDF, name=None):
    with open(path, "rb") as fh:
        return client.post("/api/documents", files={"file": (name or path.name, fh, "application/pdf")})


def test_upload_uses_main_parser_and_matches_plain_parse(env):
    client, fake, storage, _ = env
    saved = _upload(client)
    assert saved.status_code == 201, saved.text
    with open(SAMPLE_PDF, "rb") as fh:
        plain = client.post("/api/parse", files={"file": (SAMPLE_PDF.name, fh, "application/pdf")}).json()
    result = saved.json()["result"]
    assert len(result["blocks"]) == len(plain["blocks"])
    assert result["page_count"] == plain["page_count"]
    assert result["preview_available"] == plain["preview_available"]
    assert [b["type"] for b in result["blocks"]] == [b["type"] for b in plain["blocks"]]


def test_original_is_encrypted_at_rest_and_download_roundtrips(env):
    client, fake, storage, _ = env
    body = _upload(client).json()
    plaintext = SAMPLE_PDF.read_bytes()
    (ciphertext,) = storage.values()
    assert ciphertext != plaintext and plaintext[:8] not in ciphertext
    version = fake.versions[body["document_id"]]
    assert version["key_version"] == 1 and len(version["encrypted_dek"]) > 32
    dl = client.get(f"/api/documents/{body['document_id']}/download")
    assert dl.status_code == 200 and dl.content == plaintext


def test_saved_result_is_an_object_and_needs_no_reparse(env, monkeypatch):
    client, *_ = env
    doc_id = _upload(client).json()["document_id"]
    from routers import documents

    def boom(*a, **k):
        raise AssertionError("parser must not run when opening a saved result")

    monkeypatch.setattr(documents, "parse_upload", boom)
    res = client.get(f"/api/documents/{doc_id}/result")
    assert res.status_code == 200
    assert isinstance(res.json()["result"], dict) and res.json()["result"]["blocks"]


def test_history_provenance_restored_from_encrypted_original(env):
    client, *_ = env
    body = _upload(client).json()
    preview_id = body["result"]["document_id"]
    from services import preview_service

    assert body["result"]["preview_available"] is True
    shutil.rmtree(preview_service.PREVIEW_ROOT / preview_id)  # simulate expiry / restart
    assert client.get(f"/api/preview/{preview_id}/pages/1").status_code == 404

    res = client.get(f"/api/documents/{body['document_id']}/result").json()["result"]
    assert res["preview_available"] is True and res["preview_pages"] == body["result"]["preview_pages"]
    page = client.get(f"/api/preview/{preview_id}/pages/1")
    assert page.status_code == 200 and page.content[:4] == b"\x89PNG"
    # block boxes come from the saved result, not recomputed
    assert [b["bbox"] for b in res["blocks"]] == [b["bbox"] for b in body["result"]["blocks"]]


def test_other_users_cannot_read_download_or_delete(env):
    client, fake, storage, who = env
    doc_id = _upload(client).json()["document_id"]
    who["user"] = "user-b"
    for method, suffix in (("get", ""), ("get", "/result"), ("get", "/download"), ("delete", "")):
        assert getattr(client, method)(f"/api/documents/{doc_id}{suffix}").status_code == 404
    assert client.get("/api/documents").json()["documents"] == []
    assert doc_id in fake.documents and storage


def test_unsupported_format_stores_nothing(env):
    client, fake, storage, _ = env
    res = client.post("/api/documents", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert res.status_code == 415
    assert not fake.documents and not storage


def test_parse_failure_marks_run_failed_and_keeps_history_consistent(env):
    client, fake, storage, _ = env
    res = client.post("/api/documents", files={"file": ("bad.pdf", b"not really a pdf at all", "application/pdf")})
    assert res.status_code == 422
    assert [r["status"] for r in fake.runs.values()] == ["failed"]
    assert [d["status"] for d in fake.documents.values()] == ["failed"]


def test_delete_removes_record_and_ciphertext(env):
    client, fake, storage, _ = env
    doc_id = _upload(client).json()["document_id"]
    assert client.delete(f"/api/documents/{doc_id}").status_code == 204
    assert doc_id not in fake.documents and not storage


def test_plain_parse_still_works_without_any_auth_or_database(env):
    client, *_ = env
    with open(SAMPLE_PDF, "rb") as fh:
        res = client.post("/api/parse", files={"file": (SAMPLE_PDF.name, fh, "application/pdf")})
    assert res.status_code == 200 and res.json()["blocks"]
    assert json.dumps(res.json())
