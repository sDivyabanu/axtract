"""Smart Benchmark Runner API. Every endpoint requires a signed-in, allow-listed user."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from auth.supabase import get_current_user
from benchmark import config
from benchmark.manager import RunManager
from benchmark.runner import BenchmarkRunner, RunError
from benchmark.store import RUN_ID_RE
from models.errors import AppError

router = APIRouter(prefix="/api/benchmark", tags=["benchmark"])

RunId = Annotated[str, Query(pattern=RUN_ID_RE.pattern)]


@lru_cache(maxsize=1)
def get_manager() -> RunManager:
    return RunManager(BenchmarkRunner())


async def require_benchmark_user(user: dict = Depends(get_current_user)) -> dict:
    """Deny by default: AXTRACT_BENCHMARK_USERS lists allowed user ids/emails ('*' = any signed-in user)."""
    allowed = config.allowed_users()
    identity = {str(user.get("sub", "")).lower(), str(user.get("email", "")).lower()} - {""}
    if "*" not in allowed and not (allowed & identity):
        raise HTTPException(status_code=403, detail="You are not allowed to use the benchmark runner.")
    return user


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except RunError as exc:
        raise AppError(exc.code, exc.message, exc.status)


class StartBody(BaseModel):
    model_config = {"extra": "forbid"}
    mode: str = config.DEFAULT_MODE
    categories: list[str] = Field(default_factory=lambda: ["all"], max_length=32)
    per_category: int = Field(config.DEFAULT_PER_CATEGORY, ge=1, le=config.MAX_PER_CATEGORY)
    seed: int = Field(config.DEFAULT_SEED, ge=0, le=2**31 - 1)
    workers: int = Field(config.DEFAULT_WORKERS, ge=1, le=config.MAX_WORKERS)


@router.get("/categories")
async def list_categories(_: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    try:
        cats = mgr.runner.categories()
    except (FileNotFoundError, ValueError):
        raise AppError("DATASET_UNAVAILABLE", "The benchmark dataset is not available on this server.", 503)
    return {
        "modes": list(config.MODES),
        "defaults": {"mode": config.DEFAULT_MODE, "per_category": config.DEFAULT_PER_CATEGORY,
                     "seed": config.DEFAULT_SEED, "workers": config.DEFAULT_WORKERS,
                     "max_per_category": config.MAX_PER_CATEGORY, "max_workers": config.MAX_WORKERS},
        "categories": [{"name": c.name, "pdfs": c.pdf_count, "tests": c.test_count} for c in cats.values()],
    }


@router.post("/runs", status_code=202)
async def create_run(body: StartBody, _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    run_id = _call(mgr.start, mode=body.mode, categories=body.categories, per_category=body.per_category,
                   seed=body.seed, workers=body.workers)
    return {"run_id": run_id}


@router.get("/runs")
async def list_runs(limit: int = Query(50, ge=1, le=200), _: dict = Depends(require_benchmark_user),
                    mgr: RunManager = Depends(get_manager)):
    return {"runs": mgr.runner.history(limit)}


@router.get("/compare")
async def compare_runs(a: RunId, b: RunId, _: dict = Depends(require_benchmark_user),
                       mgr: RunManager = Depends(get_manager)):
    return _call(mgr.runner.compare, a, b)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    return _call(mgr.runner.status, run_id)


@router.get("/runs/{run_id}/results")
async def get_results(run_id: str, _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    return _call(mgr.runner.results, run_id)


@router.get("/runs/{run_id}/manifest")
async def get_manifest(run_id: str, _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    return _call(mgr.runner.manifest, run_id)


@router.get("/runs/{run_id}/log")
async def get_log(run_id: str, _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    _call(mgr.runner.status, run_id)  # 404 for unknown runs
    return {"log": mgr.runner.log_tail(run_id)}


@router.get("/runs/{run_id}/report")
async def get_report(run_id: str, _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    path = _call(mgr.runner.report_path, run_id)
    return FileResponse(path, media_type="text/html", headers={"Content-Security-Policy": "sandbox allow-scripts"})


@router.post("/runs/{run_id}/resume", status_code=202)
async def resume_run(run_id: str, _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    _call(mgr.resume, run_id)
    return {"run_id": run_id}


@router.post("/runs/{run_id}/cancel", status_code=202)
async def cancel_run(run_id: str, _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    _call(mgr.cancel, run_id)
    return {"run_id": run_id}
