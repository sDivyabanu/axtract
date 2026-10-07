import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from models.errors import AppError, ErrorInfo, ErrorResponse
from routers import auth, documents, health, parse

logger = logging.getLogger("parseanything")


def _validate_env() -> list[str]:
    """Check which optional env vars are set for feature availability."""
    available = []
    if os.environ.get("DATABASE_URL"):
        available.append("database")
    if os.environ.get("SUPABASE_URL"):
        available.append("supabase")
    if os.environ.get("AXTRACT_MASTER_KEY_BASE64"):
        available.append("encryption")
    return available


@asynccontextmanager
async def lifespan(app: FastAPI):
    features = _validate_env()
    if features:
        logger.info("AXTRACT features enabled: %s", ", ".join(features))
    if "database" in features:
        from db.prisma_client import get_pool
        await get_pool()
        logger.info("Database connection pool initialized.")
    yield
    if "database" in features:
        from db.prisma_client import close_pool
        await close_pool()


app = FastAPI(title="AXTRACT API", version="0.2.0", lifespan=lifespan)

# Local Next.js dev server. Add more origins here if the frontend moves.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(parse.router)
app.include_router(auth.router)
app.include_router(documents.router)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorInfo(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump())


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return _error_response(exc.status_code, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return _error_response(422, "INVALID_REQUEST", "The request is malformed.")


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error", exc_info=exc)
    return _error_response(500, "INTERNAL_ERROR", "An unexpected error occurred.")
