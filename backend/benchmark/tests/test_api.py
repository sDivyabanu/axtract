from __future__ import annotations

import threading
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from auth.supabase import get_current_user
from benchmark.manager import RunManager
from benchmark.tests.conftest import FakeExtract
from models.errors import AppError
from routers import benchmark as api

USER = {"sub": "user-1", "email": "me@example.com"}


@pytest.fixture
def app_env(make_runner, monkeypatch):
    monkeypatch.setenv("AXTRACT_BENCHMARK_USERS", "me@example.com")
    official = {"active": False}
    mgr = RunManager(make_runner(extract=FakeExtract(fail_substr=None)), official_check=lambda: official["active"])
    app = FastAPI()
    app.include_router(api.router)

    @app.exception_handler(AppError)
    async def _app_error(_, exc):
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})

    @app.exception_handler(RequestValidationError)
    async def _invalid(_, exc):
        return JSONResponse(status_code=422, content={"error": {"code": "INVALID_REQUEST"}})

    app.dependency_overrides[api.get_manager] = lambda: mgr
    app.dependency_overrides[get_current_user] = lambda: dict(USER)
    return app, mgr, official


async def call(app, method, path, **kw):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(method, path, **kw)


def wait_done(mgr, run_id):
    assert mgr._active and mgr._active[0] == run_id
    mgr._active[1].join(30)
    assert not mgr._active[1].is_alive()


@pytest.mark.anyio
async def test_unauthorized_users_are_rejected(app_env, monkeypatch):
    app, _, _ = app_env
    monkeypatch.setenv("AXTRACT_BENCHMARK_USERS", "someone-else@example.com")
    for method, path in [("GET", "/api/benchmark/categories"), ("POST", "/api/benchmark/runs"), ("GET", "/api/benchmark/runs"),
                         ("GET", "/api/benchmark/environment"), ("GET", "/api/benchmark/runs/active")]:
        assert (await call(app, method, path, json={} if method == "POST" else None)).status_code == 403
    monkeypatch.delenv("AXTRACT_BENCHMARK_USERS")
    assert (await call(app, "GET", "/api/benchmark/categories")).status_code == 403  # default deny
    monkeypatch.setenv("AXTRACT_BENCHMARK_USERS", "user-1")  # by id also works
    assert (await call(app, "GET", "/api/benchmark/categories")).status_code == 200


@pytest.mark.anyio
async def test_categories_and_environment_expose_no_paths(app_env, settings):
    app, _, _ = app_env
    body = (await call(app, "GET", "/api/benchmark/categories")).json()
    assert [c["name"] for c in body["categories"]] == ["alpha", "beta", "tables"]
    assert body["defaults"]["per_category"] == 5 and body["modes"] == ["quick", "full"]
    env = await call(app, "GET", "/api/benchmark/environment")
    assert env.json()["dataset_available"] is True and env.json()["official_benchmark_active"] is False
    for text in (str(body), env.text):
        assert str(settings.data_dir) not in text and str(settings.workspace) not in text


@pytest.mark.anyio
@pytest.mark.parametrize("payload", [
    {"mode": "nightly"}, {"per_category": 0}, {"per_category": 1000}, {"workers": 50}, {"timeout_s": 1}, {"timeout_s": 10**6},
    {"categories": ["nope"]}, {"categories": ["../../etc"]}, {"seed": -1}, {"surprise": 1}, {"per_category": "x"},
    {"mode": "full"}, {"mode": "full", "confirm_full": True, "categories": ["alpha"]},
])
async def test_create_run_rejects_bad_input(app_env, payload):
    app, mgr, _ = app_env
    r = await call(app, "POST", "/api/benchmark/runs", json=payload)
    assert r.status_code in (400, 422)
    assert mgr.runner.history() == []  # nothing was created


@pytest.mark.anyio
async def test_full_lifecycle_through_api(app_env):
    app, mgr, _ = app_env
    r = await call(app, "POST", "/api/benchmark/runs", json={"per_category": 2, "categories": ["alpha", "beta"]})
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    wait_done(mgr, run_id)
    st = (await call(app, "GET", f"/api/benchmark/runs/{run_id}")).json()
    assert st["status"] == "completed" and st["is_official"] is False and st["counts"]["done"] == 4
    res = (await call(app, "GET", f"/api/benchmark/runs/{run_id}/results")).json()
    assert res["overall_score_pct"] == 62.5 and res["is_official"] is False and res["validity"] == "valid"
    rep = await call(app, "GET", f"/api/benchmark/runs/{run_id}/report")
    assert rep.status_code == 200 and "sandbox" in rep.headers["content-security-policy"]
    assert (await call(app, "GET", f"/api/benchmark/runs/{run_id}/manifest")).json()["documents"]
    assert "Starting run" in (await call(app, "GET", f"/api/benchmark/runs/{run_id}/log")).json()["log"]
    assert [x["run_id"] for x in (await call(app, "GET", "/api/benchmark/runs")).json()["runs"]] == [run_id]
    assert (await call(app, "POST", f"/api/benchmark/runs/{run_id}/resume")).status_code == 409  # completed
    assert (await call(app, "GET", "/api/benchmark/runs/active")).json() == {"run": None}


@pytest.mark.anyio
async def test_path_traversal_and_unknown_runs(app_env):
    app, _, _ = app_env
    assert (await call(app, "GET", "/api/benchmark/runs/qk-20260101T000000Z-aaaaaaaa")).status_code == 404
    assert (await call(app, "GET", "/api/benchmark/runs/..%2F..%2Fsecret/report")).status_code in (400, 404)
    assert (await call(app, "GET", "/api/benchmark/runs/not-a-run/results")).status_code == 400
    assert (await call(app, "GET", "/api/benchmark/compare", params={"a": "../x", "b": "y"})).status_code in (404, 422)


@pytest.mark.anyio
async def test_start_is_blocked_while_official_benchmark_runs(app_env):
    app, mgr, official = app_env
    official["active"] = True
    r = await call(app, "POST", "/api/benchmark/runs", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "OFFICIAL_BENCHMARK_ACTIVE"
    assert mgr.runner.history() == []  # refused before anything was created or extracted
    assert (await call(app, "GET", "/api/benchmark/environment")).json()["official_benchmark_active"] is True


@pytest.mark.anyio
async def test_active_run_is_visible_for_refresh_recovery_and_second_start_is_rejected(app_env):
    app, mgr, _ = app_env
    gate = threading.Event()
    ex = mgr.runner.extract_fn = FakeExtract(fail_substr=None, gate=gate)
    first = await call(app, "POST", "/api/benchmark/runs", json={"per_category": 1, "categories": ["beta"]})
    assert first.status_code == 202
    run_id = first.json()["run_id"]
    for _ in range(500):
        if ex.calls:
            break
        time.sleep(0.01)
    active = (await call(app, "GET", "/api/benchmark/runs/active")).json()["run"]
    assert active["run_id"] == run_id and active["status"] == "running"
    second = await call(app, "POST", "/api/benchmark/runs", json={"per_category": 1})
    assert second.status_code == 409 and second.json()["error"]["code"] == "RUN_CONFLICT"
    cancel = await call(app, "POST", f"/api/benchmark/runs/{run_id}/cancel")
    assert cancel.status_code == 202
    gate.set()
    wait_done(mgr, run_id)
    st = (await call(app, "GET", f"/api/benchmark/runs/{run_id}")).json()
    assert st["status"] == "cancelled" and st["resumable"] is True


@pytest.mark.anyio
async def test_resume_and_cancel_through_api(app_env):
    from benchmark import store

    app, mgr, _ = app_env
    run_id = mgr.runner.create_run(per_category=3, categories=["alpha"], workers=1)
    mgr.runner._set_status(store.run_dir(mgr.runner.settings.workspace, run_id), "failed", error="earlier crash")
    r = await call(app, "POST", f"/api/benchmark/runs/{run_id}/resume")
    assert r.status_code == 202
    wait_done(mgr, run_id)
    assert mgr.runner.status(run_id)["status"] == "completed"
    assert (await call(app, "POST", f"/api/benchmark/runs/{run_id}/cancel")).status_code == 409  # not running
