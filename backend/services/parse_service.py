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

import logging
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import UploadFile

from extractors.registry import get_extractor
from models.document import DocumentResponse
from models.errors import AppError
from services.chart_service import detect_charts_in_page, enhance_chart_block
from services.equation_service import enhance_equation_block
from services.layout_service import assign_reading_order
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

    finally:
        remove_temp_file(temp_path)

    # 4.5. Detect charts (reclassify figures)
    chart_detected_blocks = detect_charts_in_page(result.blocks)
    result.blocks = chart_detected_blocks
    
    # 4.6. Enhance tables (merged cells, financial parsing)
    enhanced_blocks = [enhance_table_block(block) for block in result.blocks]
    result.blocks = enhanced_blocks
    
    # 4.7. Enhance equations (LaTeX extraction)
    equation_enhanced_blocks = [enhance_equation_block(block) for block in result.blocks]
    result.blocks = equation_enhanced_blocks
    
    # 4.8. Merge cross-page tables
    merged_blocks = merge_cross_page_tables(result.blocks)
    result.blocks = merged_blocks

    # 5. Layout analysis and reading order
    ordered_blocks = assign_reading_order(result.blocks)

    # 6. Markdown generation
    markdown = blocks_to_markdown(ordered_blocks)

    return DocumentResponse(
        document_id=uuid4().hex,
        filename=filename,
        file_type=file_type,
        page_count=result.page_count,
        processing_time_ms=round((perf_counter() - started) * 1000),
        status="partial" if result.errors else "success",
        blocks=ordered_blocks,
        markdown=markdown,
        errors=result.errors,
    )
