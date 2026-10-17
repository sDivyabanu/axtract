from __future__ import annotations

import threading
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from auth.local_dev import get_current_user
from benchmark.manager import RunManager
from benchmark.tests.conftest import FakeExtract
from models.errors import AppError
from routers import benchmark as api

from auth.local_dev import LOCAL_USER

USER = LOCAL_USER


@pytest.fixture
def app_env(make_runner, monkeypatch):
    monkeypatch.setenv("AXTRACT_LOCAL_NO_AUTH", "true")
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
    app.dependency_overrides[get_current_user] = lambda: {"sub": "not-the-local-identity"}
    for method, path in [("GET", "/api/benchmark/categories"), ("POST", "/api/benchmark/runs"), ("GET", "/api/benchmark/runs")]:
        assert (await call(app, method, path, json={} if method == "POST" else None)).status_code == 403
    app.dependency_overrides[get_current_user] = lambda: dict(USER)
    monkeypatch.setenv("AXTRACT_LOCAL_NO_AUTH", "false")
    assert (await call(app, "GET", "/api/benchmark/categories")).status_code == 403
    monkeypatch.setenv("AXTRACT_LOCAL_NO_AUTH", "true")
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


# ---- Phase 2 endpoints ---------------------------------------------------------------------------------
async def finish_run(app, mgr, **body):
    r = await call(app, "POST", "/api/benchmark/runs", json=body)
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    wait_done(mgr, run_id)
    return run_id


@pytest.mark.anyio
async def test_cases_endpoints_page_and_detail(app_env):
    app, mgr, _ = app_env
    run_id = await finish_run(app, mgr, per_category=3, categories=["beta"])
    page = (await call(app, "GET", f"/api/benchmark/runs/{run_id}/cases", params={"outcome": "all", "limit": 4})).json()
    assert page["total"] == 6 and len(page["items"]) == 4
    failed = (await call(app, "GET", f"/api/benchmark/runs/{run_id}/cases")).json()
    cid = failed["items"][0]["id"]
    d = (await call(app, "GET", f"/api/benchmark/runs/{run_id}/case", params={"case_id": cid})).json()
    assert d["id"] == cid and d["failure_reason"] == "content_mismatch" and d["actual_output"]["available"] is True
    assert (await call(app, "GET", f"/api/benchmark/runs/{run_id}/case", params={"case_id": "nope"})).status_code == 404
    for bad in ({"outcome": "weird"}, {"limit": 101}, {"offset": -1}):
        assert (await call(app, "GET", f"/api/benchmark/runs/{run_id}/cases", params=bad)).status_code == 422
    assert (await call(app, "GET", "/api/benchmark/runs/bad-id/cases")).status_code == 400


@pytest.mark.anyio
async def test_compare_trends_and_partial_flag_endpoints(app_env):
    app, mgr, _ = app_env
    a = await finish_run(app, mgr, per_category=2, categories=["beta"], seed=1)
    b = await finish_run(app, mgr, per_category=2, categories=["beta"], seed=1)
    c = await finish_run(app, mgr, per_category=2, categories=["beta"], seed=2)
    ok = (await call(app, "GET", "/api/benchmark/compare", params={"a": a, "b": b})).json()
    assert ok["comparable"] is True and ok["cases"]["compared"] == 4 and ok["overall_delta_pct"] == 0.0
    blocked = (await call(app, "GET", "/api/benchmark/compare", params={"a": a, "b": c})).json()
    assert blocked["comparable"] is False and blocked["cases"] is None
    partial = (await call(app, "GET", "/api/benchmark/compare", params={"a": a, "b": c, "allow_partial": "true"})).json()
    assert partial["partial"] is True and partial["cases"] is not None
    assert (await call(app, "GET", "/api/benchmark/compare", params={"a": a, "b": a})).status_code == 400
    sample = ok["a"]["sample_id"]
    t = (await call(app, "GET", "/api/benchmark/trends", params={"sample_id": sample})).json()
    assert [p["run_id"] for p in t["points"]] == [a, b]
    assert (await call(app, "GET", "/api/benchmark/trends", params={"sample_id": "../etc"})).status_code == 422


@pytest.mark.anyio
async def test_changes_endpoint_validates_and_reports_git_errors(app_env):
    app, _, _ = app_env
    for bad in ("--output=x", "a;b", "x" * 101):
        assert (await call(app, "GET", "/api/benchmark/changes", params={"base": bad})).status_code in (400, 422)
    r = await call(app, "GET", "/api/benchmark/changes", params={"base": "main"})
    assert r.status_code in (400, 404, 503)  # the test repo is not a git checkout: a clean error, never a 500
    assert str(app_env[1].runner.settings.repo_root) not in r.text


@pytest.mark.anyio
async def test_import_endpoints_and_traversal(app_env, settings):
    from benchmark.tests.test_phase2 import make_artifact

    app, mgr, _ = app_env
    make_artifact(settings.resolved_import_root())
    listing = (await call(app, "GET", "/api/benchmark/imports")).json()["sources"]
    assert [s["source"] for s in listing] == ["current_x/official_x_001"]
    r = await call(app, "POST", "/api/benchmark/imports", json={"source": "current_x/official_x_001"})
    assert r.status_code == 201 and r.json()["already_imported"] is False
    st = (await call(app, "GET", f"/api/benchmark/runs/{r.json()['run_id']}")).json()
    assert st["status"] == "imported" and st["is_official"] is False and st["resumable"] is False
    assert (await call(app, "POST", f"/api/benchmark/runs/{r.json()['run_id']}/resume")).status_code == 409
    again = await call(app, "POST", "/api/benchmark/imports", json={"source": "current_x/official_x_001"})
    assert again.json()["already_imported"] is True
    for bad in ("../x", "/etc/passwd", "C:\\Windows", "a/b/c/d"):
        assert (await call(app, "POST", "/api/benchmark/imports", json={"source": bad})).status_code in (400, 404)
    assert (await call(app, "POST", "/api/benchmark/imports", json={"source": "x", "extra": 1})).status_code == 422
    assert (await call(app, "GET", f"/api/benchmark/runs/{r.json()['run_id']}/report")).status_code == 404  # no huge report served


@pytest.mark.anyio
async def test_full_mode_is_never_started_without_confirmation(app_env):
    app, mgr, _ = app_env
    assert (await call(app, "POST", "/api/benchmark/runs", json={"mode": "full", "per_category": None})).status_code == 400
    assert mgr.runner.history() == []
    run_id = await finish_run(app, mgr, mode="full", per_category=None, confirm_full=True)
    st = (await call(app, "GET", f"/api/benchmark/runs/{run_id}")).json()
    assert st["status"] == "completed" and st["totals"]["documents"] == 17 and st["config"]["html_report"] is False


def test_every_benchmark_endpoint_runs_in_the_thread_pool_not_the_event_loop():
    """Plain `def` handlers: disk and git work here can never stall ordinary AXTRACT requests."""
    import inspect

    handlers = [r.endpoint for r in api.router.routes]
    assert len(handlers) >= 15
    assert not [h.__name__ for h in handlers if inspect.iscoroutinefunction(h)]
