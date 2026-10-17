"""Authentication and authorisation of the benchmark API, through the REAL auth dependency of the real app.

These are automated tests with locally signed tokens and a local JWKS stub. They prove the access rules end to end,
but they are NOT a login against the real Supabase project.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import jwt
import pytest

import main
from auth import supabase as supabase_auth
from benchmark.manager import RunManager
from benchmark.tests.conftest import FakeExtract, anyio_backend, dataset, make_runner, settings  # noqa: F401 (fixtures)
from routers import benchmark as api

SECRET = "unit-test-signing-secret-32-bytes-long!!"
CANARIES = {
    "SUPABASE_JWT_SECRET": SECRET,
    "AXTRACT_MASTER_KEY_BASE64": "CANARY-master-key-AAAA1111",
    "DATABASE_URL": "postgresql://canary-user:CANARY-db-password@localhost/db",
    "SUPABASE_SECRET_KEY": "CANARY-supabase-secret-BBBB2222",
}
GET_PATHS = [
    "/api/benchmark/environment", "/api/benchmark/categories", "/api/benchmark/runs", "/api/benchmark/runs/active",
    "/api/benchmark/changes", "/api/benchmark/trends", "/api/benchmark/imports",
    "/api/benchmark/runs/qk-20260101T000000Z-aaaaaaaa", "/api/benchmark/runs/qk-20260101T000000Z-aaaaaaaa/results",
    "/api/benchmark/runs/qk-20260101T000000Z-aaaaaaaa/log", "/api/benchmark/runs/qk-20260101T000000Z-aaaaaaaa/report",
    "/api/benchmark/runs/qk-20260101T000000Z-aaaaaaaa/cases", "/api/benchmark/runs/qk-20260101T000000Z-aaaaaaaa/manifest",
]


class _Jwks(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        # One unrelated key, like a real Supabase JWKS: lookup of our kid-less token fails and verification
        # falls back to the configured HS256 secret.
        body = b'{"keys": [{"kty": "oct", "kid": "other", "alg": "HS256", "k": "b3RoZXItc2VjcmV0"}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


@pytest.fixture
def real_auth(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _Jwks)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://supabase.test"
    monkeypatch.setenv("SUPABASE_URL", base)
    monkeypatch.setenv("SUPABASE_JWKS_URL", f"http://127.0.0.1:{server.server_port}/jwks")
    for k, v in CANARIES.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(supabase_auth, "_jwks_client", None)
    monkeypatch.setattr(supabase_auth, "_jwt_secret", None)
    yield base
    server.shutdown()


def token(base, sub="user-1", email="me@example.com", exp_in=3600, issuer=None, secret=SECRET):
    return jwt.encode({"sub": sub, "email": email, "iss": issuer or f"{base}/auth/v1", "exp": int(time.time()) + exp_in}, secret, algorithm="HS256")


async def get(path, tok=None, method="GET", **kw):
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t") as c:
        return await c.request(method, path, headers=headers, **kw)


@pytest.fixture
def fake_manager(make_runner):  # noqa: F811
    mgr = RunManager(make_runner(extract=FakeExtract(fail_substr=None)), official_check=lambda: False)
    main.app.dependency_overrides[api.get_manager] = lambda: mgr
    yield mgr
    main.app.dependency_overrides.pop(api.get_manager, None)


@pytest.mark.anyio
@pytest.mark.parametrize("path", GET_PATHS)
async def test_no_credentials_are_rejected_on_every_benchmark_endpoint(real_auth, path):
    r = await get(path)
    assert r.status_code in (401, 403) and "categories" not in r.text


@pytest.mark.anyio
@pytest.mark.parametrize("method,path", [("POST", "/api/benchmark/runs"), ("POST", "/api/benchmark/imports"),
                                         ("POST", "/api/benchmark/runs/qk-20260101T000000Z-aaaaaaaa/resume"),
                                         ("POST", "/api/benchmark/runs/qk-20260101T000000Z-aaaaaaaa/cancel")])
async def test_no_credentials_cannot_start_resume_cancel_or_import(real_auth, method, path):
    r = await get(path, method=method, json={})
    assert r.status_code in (401, 403)


@pytest.mark.anyio
async def test_bad_tokens_are_rejected(real_auth):
    for bad in ("garbage", token(real_auth, exp_in=-60), token(real_auth, issuer="http://evil.test/auth/v1"),
                token(real_auth, secret="a-different-secret-that-is-long-enough-1234")):
        assert (await get("/api/benchmark/environment", bad)).status_code == 401


@pytest.mark.anyio
async def test_a_valid_login_is_not_enough_without_being_allow_listed(real_auth, monkeypatch, fake_manager):
    ok = token(real_auth)
    monkeypatch.delenv("AXTRACT_BENCHMARK_USERS", raising=False)  # missing configuration fails closed
    r = await get("/api/benchmark/environment", ok)
    assert r.status_code == 403 and "not allowed" in r.text
    monkeypatch.setenv("AXTRACT_BENCHMARK_USERS", "")
    assert (await get("/api/benchmark/environment", ok)).status_code == 403
    monkeypatch.setenv("AXTRACT_BENCHMARK_USERS", "someone-else@example.com, another-id")
    assert (await get("/api/benchmark/environment", ok)).status_code == 403
    assert (await get("/api/benchmark/runs", ok)).status_code == 403
    assert (await get("/api/benchmark/runs", ok, method="POST", json={})).status_code == 403


@pytest.mark.anyio
async def test_allow_list_by_email_by_id_and_wildcard(real_auth, monkeypatch, fake_manager):
    ok = token(real_auth)
    monkeypatch.setenv("AXTRACT_BENCHMARK_USERS", "ME@example.com")  # case-insensitive
    assert (await get("/api/benchmark/environment", ok)).status_code == 200
    monkeypatch.setenv("AXTRACT_BENCHMARK_USERS", "user-1")
    assert (await get("/api/benchmark/environment", ok)).status_code == 200
    assert (await get("/api/benchmark/environment", token(real_auth, sub="user-2", email="x@y.z"))).status_code == 403
    monkeypatch.setenv("AXTRACT_BENCHMARK_USERS", "*")
    assert (await get("/api/benchmark/environment", token(real_auth, sub="user-2", email="x@y.z"))).status_code == 200
    assert (await get("/api/benchmark/environment")).status_code in (401, 403)  # '*' still needs a valid login


@pytest.mark.anyio
async def test_no_secret_or_environment_value_appears_in_any_response_or_run_file(real_auth, monkeypatch, fake_manager):
    monkeypatch.setenv("AXTRACT_BENCHMARK_USERS", "me@example.com")
    ok = token(real_auth)
    r = await get("/api/benchmark/runs", ok, method="POST", json={"per_category": 2, "categories": ["alpha"]})
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    fake_manager._active[1].join(30)
    bodies = []
    for path in GET_PATHS[:7] + [f"/api/benchmark/runs/{run_id}", f"/api/benchmark/runs/{run_id}/results", f"/api/benchmark/runs/{run_id}/log",
                                 f"/api/benchmark/runs/{run_id}/manifest", f"/api/benchmark/runs/{run_id}/cases?outcome=all",
                                 f"/api/benchmark/runs/{run_id}/report", "/api/benchmark/runs/not-a-run", "/api/benchmark/compare?a=x&b=y"]:
        bodies.append((await get(path, ok)).text)
    bodies.append((await get("/api/benchmark/imports", ok, method="POST", json={"source": "../../etc/passwd"})).text)
    case = json.loads(bodies[-6])["items"][0]["id"] if json.loads(bodies[-6]).get("items") else None
    if case:
        bodies.append((await get(f"/api/benchmark/runs/{run_id}/case?case_id={case}", ok)).text)
    blob = "\n".join(bodies)
    files = "\n".join(p.read_text(errors="ignore") for p in fake_manager.runner.settings.workspace.rglob("*")
                      if p.is_file() and p.suffix in (".json", ".jsonl", ".log", ".txt"))
    for name, value in CANARIES.items():
        assert value not in blob, f"{name} leaked into an API response"
        assert value not in files, f"{name} leaked into a run file or log"
    assert str(fake_manager.runner.settings.data_dir) not in blob and str(fake_manager.runner.settings.workspace) not in blob


@pytest.mark.anyio
async def test_quick_mode_can_never_start_a_full_corpus_run(real_auth, monkeypatch, fake_manager):
    monkeypatch.setenv("AXTRACT_BENCHMARK_USERS", "me@example.com")
    ok = token(real_auth)
    for body in ({"mode": "quick", "per_category": None}, {"mode": "quick", "per_category": 10_000}, {"mode": "quick", "per_category": 51},
                 {"mode": "quick", "confirm_full": True, "per_category": None}, {"mode": "full"},
                 {"mode": "full", "confirm_full": False}, {"mode": "full", "confirm_full": True, "categories": ["alpha"]}):
        r = await get("/api/benchmark/runs", ok, method="POST", json=body)
        assert r.status_code in (400, 422), body
    assert fake_manager.runner.history() == []  # not one run folder was created by any refused request


@pytest.mark.anyio
async def test_full_mode_needs_explicit_confirmation_and_uses_bounded_workers(real_auth, monkeypatch, fake_manager):
    from benchmark import config, procutil

    monkeypatch.setenv("AXTRACT_BENCHMARK_USERS", "me@example.com")
    ok = token(real_auth)
    assert (await get("/api/benchmark/runs", ok, method="POST", json={"mode": "full", "workers": config.MAX_WORKERS + 1, "confirm_full": True})).status_code in (400, 422)
    assert (await get("/api/benchmark/runs", ok, method="POST", json={"mode": "full", "workers": 0, "confirm_full": True})).status_code in (400, 422)
    r = await get("/api/benchmark/runs", ok, method="POST", json={"mode": "full", "per_category": None, "workers": config.MAX_WORKERS, "confirm_full": True})
    assert r.status_code == 202
    fake_manager._active[1].join(30)
    st = fake_manager.runner.status(r.json()["run_id"])
    assert st["mode"] == "full" and st["totals"]["documents"] == 17 and st["config"]["html_report"] is False
    for requested in (1, 2, 3, 4, 64):
        assert 1 <= procutil.worker_budget(requested, config.MAX_WORKERS) <= min(config.MAX_WORKERS, max(1, (__import__("os").cpu_count() or 2) // 2))


@pytest.mark.anyio
async def test_ordinary_axtract_routes_do_not_depend_on_the_benchmark_allow_list(real_auth, monkeypatch):
    monkeypatch.delenv("AXTRACT_BENCHMARK_USERS", raising=False)
    assert (await get("/health")).json() == {"status": "ok"}
    assert (await get("/api/documents")).status_code in (401, 403)  # still protected by its own (unchanged) auth
    spec = main.app.openapi()["paths"]
    assert "/api/documents" in spec and "/api/parse" in spec or "/parse" in "".join(spec)  # existing routes still registered
