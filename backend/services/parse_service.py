from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import UploadFile

from extractors.registry import get_extractor
from models.document import DocumentResponse
from models.errors import AppError
from utils.files import get_extension, remove_temp_file, sanitize_filename, save_upload_to_temp


def parse_upload(upload: UploadFile | None) -> DocumentResponse:
    """Validate an upload, run the matching extractor, and return the common document schema.

    Synchronous on purpose: extractors are CPU/IO bound, so the route runs this in a threadpool.
    """
    if upload is None or not upload.filename:
        raise AppError("MISSING_FILE", "No file was provided.", status_code=400)

    filename = sanitize_filename(upload.filename)
    file_type = get_extension(filename)
    extractor = get_extractor(file_type)
    if extractor is None:
        raise AppError("UNSUPPORTED_FORMAT", "This file type is not supported yet.", status_code=415)

    started = perf_counter()
    temp_path: Path | None = None
    try:
        temp_path = save_upload_to_temp(upload, file_type)
        if temp_path.stat().st_size == 0:
            raise AppError("EMPTY_FILE", "The uploaded file is empty.", status_code=400)

        result = extractor.extract(temp_path)
    finally:
        remove_temp_file(temp_path)

    return DocumentResponse(
        document_id=uuid4().hex,
        filename=filename,
        file_type=file_type,
        page_count=result.page_count,
        processing_time_ms=round((perf_counter() - started) * 1000),
        status="partial" if result.errors else "success",
        blocks=result.blocks,
        errors=result.errors,
    )
