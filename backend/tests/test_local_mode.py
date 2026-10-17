"""Real socket boundary + real local persistence; no accounts, cloud or official benchmark runs."""
import json
import socket

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from auth import local_dev
from db import local_documents as db
from models.document import DocumentResponse


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def local_app(monkeypatch, tmp_path):
    monkeypatch.setenv("AXTRACT_LOCAL_NO_AUTH", "true")
    monkeypatch.setenv("AXTRACT_LOCAL_DATA_DIR", str(tmp_path))
    from rag import warmup
    monkeypatch.setattr(warmup, "start", lambda: None)
    sock = local_dev.bind_loopback("127.0.0.1", 0)
    monkeypatch.setattr(main.app.state, "local_no_auth_socket", sock, raising=False)
    yield main.app
    sock.close()


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.5", "localhost", "example.org", ""])
def test_reject_nonliteral_or_public_binds(host):
    with pytest.raises(RuntimeError):
        local_dev.bind_loopback(host, 0)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_actual_socket_binding(host):
    try:
        sock = local_dev.bind_loopback(host, 0)
    except OSError:
        if host == "::1" and not socket.has_ipv6:
            pytest.skip("IPv6 unavailable")
        raise
    with sock:
        assert sock.getsockname()[0] == host


def test_enabled_flag_alone_cannot_start_server(monkeypatch, tmp_path):
    monkeypatch.setenv("AXTRACT_LOCAL_NO_AUTH", "true")
    monkeypatch.setenv("AXTRACT_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.delattr(main.app.state, "local_no_auth_socket", raising=False)
    with pytest.raises(RuntimeError, match="serve_local"):
        with TestClient(main.app):
            pass


def test_public_socket_not_attested(local_app, monkeypatch):
    with socket.socket() as public:
        public.bind(("0.0.0.0", 0))
        public.listen()
        monkeypatch.setattr(local_app.state, "local_no_auth_socket", public)
        with pytest.raises(RuntimeError, match="loopback"):
            local_dev.verify_binding(local_app)


@pytest.mark.anyio
@pytest.mark.parametrize("flag", [None, "false", "1"])
async def test_disabled_defaults_fail_closed(local_app, monkeypatch, flag):
    if flag is None:
        monkeypatch.delenv("AXTRACT_LOCAL_NO_AUTH")
    else:
        monkeypatch.setenv("AXTRACT_LOCAL_NO_AUTH", flag)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app), base_url="http://127.0.0.1") as client:
        for path in ["/api/documents", "/api/benchmark/runs", "/api/parse"]:
            assert (await client.get(path)).status_code == 503


@pytest.mark.anyio
@pytest.mark.parametrize("peer,host,headers", [
    ("192.168.1.10", "127.0.0.1", {}),
    ("127.0.0.1", "attacker.example", {}),
    ("127.0.0.1", "127.0.0.1", {"origin": "https://attacker.example"}),
    ("127.0.0.1", "127.0.0.1", {"origin": "null"}),
    ("127.0.0.1", "127.0.0.1", {"x-forwarded-for": "127.0.0.1"}),
    ("127.0.0.1", "127.0.0.1", {"sec-fetch-site": "cross-site"}),
])
async def test_request_boundary(local_app, peer, host, headers):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app, client=(peer, 1234)), base_url=f"http://{host}") as client:
        assert (await client.get("/api/documents", headers=headers)).status_code == 403


def test_persistent_document_lifecycle_without_cloud(local_app, monkeypatch):
    from routers import documents

    response = DocumentResponse(document_id="a" * 32, filename="sample.pdf", file_type="pdf", page_count=1,
                                processing_time_ms=1, status="success", blocks=[], markdown="local result", validation={"status": "verified"})
    monkeypatch.setattr(documents, "parse_upload", lambda *args: response)
    options = dict(base_url="http://127.0.0.1", client=("127.0.0.1", 1234))
    original = b"%PDF-1.4 test fixture (parser mocked)"
    with TestClient(local_app, **options) as client:
        assert client.get("/api/documents").json() == {"documents": []}
        saved = client.post("/api/documents", files={"file": ("sample.pdf", original, "application/pdf")})
        assert saved.status_code == 201, saved.text
        identity = saved.json()["document_id"]
        result = client.get(f"/api/documents/{identity}/result")
        assert result.status_code == 200 and result.json()["result"]["validation"]["status"] == "verified"
        assert client.get(f"/api/documents/{identity}/history").json()["runs"][0]["status"] == "completed"
        assert client.get(f"/api/documents/{identity}/download").content == original
        ciphertext = next((local_dev.data_dir() / "objects").rglob("*.enc")).read_bytes()
        assert original not in ciphertext
    # A new client/process connection can reopen the saved result without extraction.
    monkeypatch.setattr(documents, "parse_upload", lambda *args: pytest.fail("Must not re-extract"))
    with TestClient(local_app, **options) as client:
        assert len(client.get("/api/documents").json()["documents"]) == 1
        assert client.get(f"/api/documents/{identity}/result").json()["markdown"] == "local result"
        assert client.delete(f"/api/documents/{identity}").status_code == 204
        assert client.get(f"/api/documents/{identity}").status_code == 404
        assert list((local_dev.data_dir() / "objects").rglob("*.enc")) == []


@pytest.mark.anyio
async def test_real_identities_and_storage_paths_are_isolated(local_app):
    local = await db.create_document(local_dev.LOCAL_USER_ID, "x.pdf", "application/pdf", 3)
    assert await db.get_document(local["id"], "real-user") is None
    assert await db.list_documents("real-user") == []
    with pytest.raises(ValueError):
        await db.create_document("real-user", "x.pdf", "application/pdf", 3)
    for path in ["real-user/id/v1.enc", "axtract-local-development/../v1.enc", "axtract-local-development/x/a:stream"]:
        with pytest.raises(ValueError):
            local_dev.object_path(path)
    assert local_dev.data_dir().name == "local-no-auth"


@pytest.mark.anyio
async def test_saved_verify_and_recovery_updates(local_app):
    owner = local_dev.LOCAL_USER_ID
    doc = await db.create_document(owner, "x.pdf", "application/pdf", 3)
    version = await db.create_document_version(doc["id"], 1, "unused", b"nonce", b"key", b"wrap", 1, 3)
    run = await db.create_processing_run(doc["id"], version["id"], owner)
    await db.save_document_output(run["id"], json.dumps({"validation": {"status": "review"}}), "md")
    await db.complete_processing_run(run["id"], "completed")
    assert await db.update_latest_output_json(doc["id"], owner, json.dumps({"validation": {"status": "verified"}}))
    assert (await db.get_latest_output(doc["id"], owner))["response_json"]["validation"]["status"] == "verified"
    assert (await db.get_output_by_run(run["id"], owner))["markdown"] == "md"
    assert (await db.get_latest_version(doc["id"]))["encryption_nonce"] == b"nonce"
    with pytest.raises(ValueError):
        await db.update_latest_output_json(doc["id"], "real-user", "{}")


def test_benchmark_dashboard_access_without_account(local_app, monkeypatch):
    from routers import benchmark
    class Runner:
        def history(self, limit=50):
            return []
    class Manager:
        runner = Runner()
    local_app.dependency_overrides[benchmark.get_manager] = lambda: Manager()
    try:
        with TestClient(local_app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234)) as client:
            assert client.get("/api/benchmark/runs").status_code == 200
    finally:
        local_app.dependency_overrides.clear()
