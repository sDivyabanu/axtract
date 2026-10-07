import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from models.errors import AppError, ErrorInfo, ErrorResponse
from routers import health, parse, preview

logger = logging.getLogger("parseanything")

app = FastAPI(title="ParseAnything API", version="0.1.0")

# Local Next.js dev server. Add more origins here if the frontend moves.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(parse.router)
app.include_router(preview.router)


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
