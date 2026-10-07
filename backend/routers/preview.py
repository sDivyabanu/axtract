"""Preview endpoints: rendered page images for the source viewer."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from models.errors import AppError
from services import preview_service
from services.preview_service import PreviewError
from utils.files import (
    MAX_UPLOAD_BYTES,
    get_extension,
    remove_temp_file,
    sanitize_filename,
    save_upload_to_temp,
    validate_magic_bytes,
)

router = APIRouter(prefix="/api", tags=["preview"])

_PREVIEWABLE = {"pdf", "docx", "pptx", "xlsx", "jpg", "jpeg", "png"}


def _png(data: bytes) -> Response:
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/preview/{document_id}/pages/{page}")
async def get_page_preview(
    document_id: str,
    page: int,
    dpi: int = Query(preview_service.DEFAULT_DPI, ge=72, le=300),
) -> Response:
    """PNG of one page (1-based) of a document that was just parsed."""
    try:
        return _png(await run_in_threadpool(preview_service.page_png, document_id, page, dpi))
    except PreviewError as exc:
        raise AppError("PREVIEW_UNAVAILABLE", str(exc), status_code=404) from exc


@router.post("/preview")
async def preview_upload(
    file: UploadFile = File(...),
    page: int = Query(1, ge=1, description="1-based page (slide / sheet page) to render"),
    dpi: int = Query(preview_service.DEFAULT_DPI, ge=72, le=300),
) -> Response:
    """Stateless preview: upload a file and get one page back as PNG."""
    if not file.filename:
        raise AppError("MISSING_FILE", "No file was provided.", status_code=400)
    file_type = get_extension(sanitize_filename(file.filename))
    if file_type not in _PREVIEWABLE:
        raise AppError("UNSUPPORTED_FORMAT", "Preview not available for this file type.", status_code=415)

    def work() -> bytes:
        path: Path | None = None
        try:
            path = save_upload_to_temp(file, file_type)
            if path.stat().st_size == 0:
                raise AppError("EMPTY_FILE", "The uploaded file is empty.", status_code=400)
            with open(path, "rb") as fh:
                if not validate_magic_bytes(fh.read(16), file_type):
                    raise AppError("INVALID_FILE", "File content does not match the expected format.", status_code=422)
            return preview_service.preview_from_upload(path, file_type, page, dpi)
        except PreviewError as exc:
            raise AppError("PREVIEW_UNAVAILABLE", str(exc), status_code=422) from exc
        finally:
            remove_temp_file(path)

    return _png(await run_in_threadpool(work))
