"""Smart Benchmark Runner API. Every endpoint requires a signed-in, allow-listed user.

Handlers are plain `def` so FastAPI runs them in its thread pool: file reads and git calls here never block
the event loop that serves ordinary AXTRACT requests.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from auth.supabase import get_current_user
from benchmark import changes, compare, config, importer, procutil
from benchmark.cases import CaseBrowser
from benchmark.manager import RunManager
from benchmark.runner import BenchmarkRunner, RunError
from benchmark.store import RUN_ID_RE
from models.errors import AppError

router = APIRouter(prefix="/api/benchmark", tags=["benchmark"])

RunId = Annotated[str, Query(pattern=RUN_ID_RE.pattern)]


@lru_cache(maxsize=1)
def get_manager() -> RunManager:
    return RunManager(BenchmarkRunner())


def require_benchmark_user(user: dict = Depends(get_current_user)) -> dict:
    """Deny by default: AXTRACT_BENCHMARK_USERS lists allowed user ids/emails ('*' = any signed-in user)."""
    allowed = config.allowed_users()
    identity = {str(user.get("sub", "")).lower(), str(user.get("email", "")).lower()} - {""}
    if "*" not in allowed and not (allowed & identity):
        raise HTTPException(status_code=403, detail="You are not allowed to use the benchmark runner.")
    return user


def call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except RunError as exc:
        raise AppError(exc.code, exc.message, exc.status)


class StartBody(BaseModel):
    model_config = {"extra": "forbid"}
    mode: str = config.DEFAULT_MODE
    categories: list[str] = Field(default_factory=lambda: ["all"], max_length=32)
    per_category: int | None = Field(config.DEFAULT_PER_CATEGORY, ge=1, le=config.MAX_PER_CATEGORY)
    seed: int = Field(config.DEFAULT_SEED, ge=0, le=2**31 - 1)
    workers: int = Field(config.DEFAULT_WORKERS, ge=1, le=config.MAX_WORKERS)
    timeout_s: int = Field(config.DEFAULT_TIMEOUT_S, ge=config.MIN_TIMEOUT_S, le=config.MAX_TIMEOUT_S)
    html_report: bool | None = None
    confirm_full: bool = False


@router.get("/environment")
def environment(_: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    """What is ready, as booleans only: no paths, no environment variables."""
    s = mgr.runner.settings
    return {
        "dataset_available": (s.data_dir / "pdfs").is_dir(),
        "scorer_available": s.scorer_python.is_file(),
        "extractor_available": s.extract_python.is_file(),
        "official_benchmark_active": mgr.official_check(),
        "active_run_id": mgr.active_run_id(),
        "free_memory_mb": procutil.free_memory_mb(),
        "limits": {"max_workers": config.MAX_WORKERS, "max_per_category": config.MAX_PER_CATEGORY,
                   "min_timeout_s": config.MIN_TIMEOUT_S, "max_timeout_s": config.MAX_TIMEOUT_S},
    }


@router.get("/categories")
def list_categories(_: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    try:
        cats = mgr.runner.categories()
    except (FileNotFoundError, ValueError):
        raise AppError("DATASET_UNAVAILABLE", "The benchmark dataset is not available on this server.", 503)
    return {
        "modes": list(config.MODES),
        "defaults": {"mode": config.DEFAULT_MODE, "per_category": config.DEFAULT_PER_CATEGORY,
                     "seed": config.DEFAULT_SEED, "workers": config.DEFAULT_WORKERS,
                     "timeout_s": config.DEFAULT_TIMEOUT_S, "max_per_category": config.MAX_PER_CATEGORY,
                     "max_workers": config.MAX_WORKERS},
        "categories": [{"name": c.name, "pdfs": c.pdf_count, "tests": c.test_count} for c in cats.values()],
    }


@router.post("/runs", status_code=202)
def create_run(body: StartBody, _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    run_id = call(mgr.start, mode=body.mode, categories=body.categories, per_category=body.per_category,
                  seed=body.seed, workers=body.workers, timeout_s=body.timeout_s, html_report=body.html_report,
                  confirm_full=body.confirm_full)
    return {"run_id": run_id}


@router.get("/runs")
def list_runs(limit: int = Query(50, ge=1, le=200), _: dict = Depends(require_benchmark_user),
              mgr: RunManager = Depends(get_manager)):
    return {"runs": mgr.runner.history(limit)}


@router.get("/changes")
def recommended_categories(base: str | None = Query(None, max_length=100), _: dict = Depends(require_benchmark_user),
                           mgr: RunManager = Depends(get_manager)):
    """Which categories do the changes since `base` (default: the last benchmarked revision) put at risk?"""
    runner = mgr.runner
    try:
        available = list(runner.categories())
    except (FileNotFoundError, ValueError):
        raise AppError("DATASET_UNAVAILABLE", "The benchmark dataset is not available on this server.", 503)
    last_sha = next((h["git"]["sha"] for h in runner.history(50)
                     if h["status"] == "completed" and (h.get("git") or {}).get("sha")), None)
    try:
        return changes.recommend(runner.settings.repo_root, available, base, last_sha)
    except changes.ChangeError as exc:
        raise AppError(exc.code, exc.message, exc.status)


@router.get("/compare")
def compare_runs(a: RunId, b: RunId, allow_partial: bool = False, _: dict = Depends(require_benchmark_user),
                 mgr: RunManager = Depends(get_manager)):
    return call(compare.compare_runs, mgr.runner, a, b, allow_partial)


@router.get("/trends")
def trends(sample_id: str | None = Query(None, pattern="^[0-9a-f]{16}$"), limit: int = Query(50, ge=1, le=200),
           _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    return compare.trends(mgr.runner, sample_id, limit)


@router.get("/imports")
def importable(_: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    return {"sources": call(importer.list_importable, mgr.runner)}


class ImportBody(BaseModel):
    model_config = {"extra": "forbid"}
    source: str = Field(min_length=1, max_length=300)


@router.post("/imports", status_code=201)
def import_results(body: ImportBody, _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    """Import a finished official run's artifacts (read-only; nothing is re-run)."""
    return call(importer.import_official, mgr.runner, body.source)


@router.get("/runs/active")
def active_run(_: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    """The run currently executing (or None), so a refreshed dashboard can re-attach to it."""
    run_id = mgr.active_run_id()
    return {"run": call(mgr.runner.status, run_id) if run_id else None}


@router.get("/runs/{run_id}")
def get_run(run_id: str, _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    return call(mgr.runner.status, run_id)


@router.get("/runs/{run_id}/results")
def get_results(run_id: str, _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    return call(mgr.runner.results, run_id)


@router.get("/runs/{run_id}/cases")
def list_cases(run_id: str, outcome: str = Query("failed", pattern="^(failed|passed|all)$"),
               category: str | None = Query(None, max_length=64), type: str | None = Query(None, max_length=32),
               q: str | None = Query(None, max_length=200), offset: int = Query(0, ge=0, le=1_000_000),
               limit: int = Query(50, ge=1, le=100), _: dict = Depends(require_benchmark_user),
               mgr: RunManager = Depends(get_manager)):
    return call(CaseBrowser(mgr.runner).list, run_id, outcome=outcome, category=category, ctype=type, q=q,
                offset=offset, limit=limit)


@router.get("/runs/{run_id}/case")
def case_detail(run_id: str, case_id: str = Query(..., min_length=1, max_length=300),
                _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    return call(CaseBrowser(mgr.runner).detail, run_id, case_id)


@router.get("/runs/{run_id}/manifest")
def get_manifest(run_id: str, _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    return call(mgr.runner.manifest, run_id)


@router.get("/runs/{run_id}/log")
def get_log(run_id: str, _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    call(mgr.runner.status, run_id)  # 404 for unknown runs
    return {"log": mgr.runner.log_tail(run_id)}


@router.get("/runs/{run_id}/report")
def get_report(run_id: str, _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    path = call(mgr.runner.report_path, run_id)
    return FileResponse(path, media_type="text/html", headers={"Content-Security-Policy": "sandbox allow-scripts"})


@router.post("/runs/{run_id}/resume", status_code=202)
def resume_run(run_id: str, _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    call(mgr.resume, run_id)
    return {"run_id": run_id}


@router.post("/runs/{run_id}/cancel", status_code=202)
def cancel_run(run_id: str, _: dict = Depends(require_benchmark_user), mgr: RunManager = Depends(get_manager)):
    call(mgr.cancel, run_id)
    return {"run_id": run_id}
