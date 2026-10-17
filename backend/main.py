import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from models.errors import AppError, ErrorInfo, ErrorResponse
from auth import local_dev
from routers import benchmark, documents, health, parse, preview, rag

# Optional local backend settings. Real environment wins.
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

logger = logging.getLogger("parseanything")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not local_dev.enabled():
        raise RuntimeError("AXTRACT is local-only. Set AXTRACT_LOCAL_NO_AUTH=true and use serve_local.py.")
    local_dev.verify_binding(app)
    local_dev.master_key()
    from rag import warmup

    warmup.start()  # background: LLM weights + prompt, embedder, reranker, retrieval indexes (never blocks or fails startup)
    from services import prewarm

    prewarm.start()  # background: pipeline imports + OCR/layout sessions, so the first upload is not the slow one
    yield



app = FastAPI(title="ParseAnything API", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def catch_unhandled(request: Request, call_next):
    """Turn any unhandled error into the structured error body.

    Registered BEFORE CORSMiddleware so CORS is the outer layer: error responses keep
    their Access-Control-* headers and the browser can read the JSON instead of
    reporting an opaque network error.
    """
    try:
        if not local_dev.enabled():
            return _error_response(503, "LOCAL_MODE_DISABLED", "Local development mode is disabled.")
        local_dev.validate_request(request)
        return await call_next(request)
    except StarletteHTTPException as exc:
        return _error_response(exc.status_code, f"HTTP_{exc.status_code}", str(exc.detail))
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
app.include_router(documents.router)
app.include_router(rag.router)
app.include_router(benchmark.router)



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
