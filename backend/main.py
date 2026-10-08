import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from models.errors import AppError, ErrorInfo, ErrorResponse
from routers import auth, documents, health, parse, preview, rag

# Optional backend settings (Supabase, database, encryption key). Real environment wins.
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

logger = logging.getLogger("parseanything")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Decide the DB path up front (PostgreSQL vs Supabase REST) so the first
    # request doesn't pay for the probe; an unreachable or unconfigured
    # database never prevents parsing from working.
    if os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_URL"):
        from db.prisma_client import _use_rest
        try:
            rest_mode = await _use_rest()
            logger.info(
                "Database ready (%s).",
                "Supabase REST over HTTPS" if rest_mode else "PostgreSQL",
            )
        except Exception:
            logger.warning("Database unavailable; DB-backed features degraded.")
    from rag import warmup

    warmup.start()  # background: LLM weights + prompt, embedder, reranker, retrieval indexes (never blocks or fails startup)
    yield
    if os.environ.get("DATABASE_URL"):
        from db.prisma_client import close_pool

        await close_pool()


app = FastAPI(title="ParseAnything API", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def catch_unhandled(request: Request, call_next):
    """Turn any unhandled error into the structured error body.

    Registered BEFORE CORSMiddleware so CORS is the outer layer: error responses keep
    their Access-Control-* headers and the browser can read the JSON instead of
    reporting an opaque network error.
    """
    try:
        return await call_next(request)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unhandled error", exc_info=exc)
        return _error_response(500, "INTERNAL_ERROR", "An unexpected error occurred.")


# Local Next.js dev server. Add more origins here if the frontend moves.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(parse.router)
app.include_router(preview.router)
app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(rag.router)



def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorInfo(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump())


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return _error_response(exc.status_code, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return _error_response(422, "INVALID_REQUEST", "The request is malformed.")


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _error_response(exc.status_code, f"HTTP_{exc.status_code}", str(exc.detail))
