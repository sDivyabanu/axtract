"""Document parsing service: the central orchestration layer.

Pipeline:
  1. Validate upload (file present, not empty, supported format)
  2. Validate file content (magic bytes)
  3. Route to appropriate extractor via registry
  4. Apply layout analysis and reading-order reconstruction
  5. Generate Markdown from ordered blocks
  6. Return DocumentResponse with JSON blocks + Markdown
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import UploadFile

from extractors.registry import get_extractor
from models.document import DocumentResponse
from models.errors import AppError
from services.region_router import route_regions
from services.layout_service import assign_reading_order
from services import preview_service
from services.markdown_service import blocks_to_markdown
from services.table_service import enhance_table_block, merge_cross_page_tables
from utils import deadline
from utils.files import (
    MAX_UPLOAD_BYTES,
    get_extension,
    remove_temp_file,
    sanitize_filename,
    save_upload_to_temp,
    validate_magic_bytes,
)

logger = logging.getLogger(__name__)


def parse_upload(upload: UploadFile | None) -> DocumentResponse:
    """Validate an upload, run the matching extractor, apply layout analysis,
    generate Markdown, and return the common document schema.

    Synchronous on purpose: extractors are CPU/IO bound, so the route runs
    this in a threadpool.
    """
    deadline.start()

    # 1. Validate upload
    if upload is None or not upload.filename:
        raise AppError("MISSING_FILE", "No file was provided.", status_code=400)

    filename = sanitize_filename(upload.filename)
    file_type = get_extension(filename)
    extractor = get_extractor(file_type)
    if extractor is None:
        raise AppError(
            "UNSUPPORTED_FORMAT",
            "This file type is not supported yet.",
            status_code=415,
        )

    started = perf_counter()
    temp_path: Path | None = None
    document_id = uuid4().hex

    try:
        # 2. Save and validate file
        temp_path = save_upload_to_temp(upload, file_type)
        file_size = temp_path.stat().st_size

        if file_size == 0:
            raise AppError("EMPTY_FILE", "The uploaded file is empty.", status_code=400)

        if file_size > MAX_UPLOAD_BYTES:
            raise AppError(
                "FILE_TOO_LARGE",
                f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
                status_code=413,
            )

        # 3. Magic byte validation
        with open(temp_path, "rb") as f:
            header = f.read(16)
        if not validate_magic_bytes(header, file_type):
            raise AppError(
                "INVALID_FILE",
                "File content does not match the expected format.",
                status_code=422,
            )

        # 4. Extract
        result = extractor.extract(temp_path)

        # 4a. Charts, equations and figures: route every region to its reader
        route_regions(result, temp_path, file_type)

        # 4b. Preview artifacts (never fatal). Office files are converted to PDF here,
        # while the upload still exists.
        preview = preview_service.prepare(document_id, temp_path, file_type)

    except BaseException:
        remove_temp_file(temp_path)
        raise

    try:
        return _finish(result, temp_path, preview, document_id, filename, file_type, started)
    finally:
        remove_temp_file(temp_path)  # kept until now so AXTRACT Verify can read the original


def _finish(result, temp_path, preview, document_id, filename, file_type, started) -> DocumentResponse:
    # 4.6. Enhance tables (merged cells, financial parsing)
    result.blocks = [enhance_table_block(block) for block in result.blocks]

    # 4.8. Merge cross-page tables
    merged_blocks = merge_cross_page_tables(result.blocks)
    result.blocks = merged_blocks

    # 5. Layout analysis and reading order
    ordered_blocks = assign_reading_order(result.blocks)

    # 5b. Where each block sits in the preview pages (Office formats)
    try:
        preview_service.annotate_blocks(preview, ordered_blocks, file_type)
    except AppError:
        raise
    except Exception:  # noqa: BLE001 - locating blocks in the preview must never fail a parse
        logger.exception("preview annotation failed")
        preview.error = preview.error or "Block positions could not be located in the preview."

    # 6. Markdown generation
    markdown = blocks_to_markdown(ordered_blocks)

    response = DocumentResponse(
        document_id=document_id,
        filename=filename,
        file_type=file_type,
        page_count=result.page_count,
        processing_time_ms=round((perf_counter() - started) * 1000),
        status="partial" if result.errors else "success",
        blocks=ordered_blocks,
        markdown=markdown,
        errors=result.errors,
        preview_available=preview.available,
        preview_pages=preview.pages,
        preview_error=preview.error,
    )
    _attach_validation(response, temp_path)
    return response


_MAX_REPORT_BYTES = 2_000_000


def _attach_validation(response: DocumentResponse, path: Path) -> None:
    """Run AXTRACT Verify and attach its report. Advisory and fail-safe: nothing here can fail a parse."""
    if os.environ.get("AXTRACT_VERIFY", "1").strip().lower() in ("0", "false", "off", "no"):
        return
    try:
        from verify.engine import VerifyOptions, verify_extraction
        from verify.models import ValidationFailure
        from verify.rollup import build_report

        remaining = deadline.remaining()
        if remaining < 4:
            report = build_report([], failure=ValidationFailure(
                stage="budget", error_type="TimeBudget", message="too little of the request's time budget was left to validate"))
        else:
            report = verify_extraction(path, response, VerifyOptions(time_budget_s=min(15.0, remaining - 3)))
        data = report.model_dump(mode="json")
        if len(json.dumps(data)) > _MAX_REPORT_BYTES:  # keep very large documents' responses bounded
            for unit in data["units"]:
                unit["checks"] = []
            data["truncated"] = "per-unit check details omitted: the report exceeded its size limit"
        response.validation = data
    except Exception:  # noqa: BLE001 - validation must never cost a successful extraction
        logger.exception("AXTRACT Verify failed; the extraction is returned without a validation report")
        response.validation = None
