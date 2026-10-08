import asyncio

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import StreamingResponse

from models.document import DocumentResponse
from models.errors import AppError
from services import progress
from services.parse_service import parse_upload
from services.progress_stream import SSE_HEADERS, event_stream
from utils.jobs import run_parse_job
from utils.deadline import HARD_LIMIT_SECONDS

router = APIRouter(prefix="/api", tags=["parse"])


async def _parse(file: UploadFile | None) -> DocumentResponse:
    try:
        return await asyncio.wait_for(
            run_parse_job(parse_upload, file), timeout=HARD_LIMIT_SECONDS
        )
    except asyncio.TimeoutError as exc:
        tracker = progress.current()
        if tracker is not None:  # a streamed job: tell the abandoned worker thread to stop
            tracker.cancelled.set()
        raise AppError(
            "TIMEOUT",
            f"Processing exceeded the {HARD_LIMIT_SECONDS} second limit.",
            status_code=504,
        ) from exc


@router.post("/parse", response_model=DocumentResponse)
async def parse_document(file: UploadFile | None = File(default=None)) -> DocumentResponse:
    return await _parse(file)


@router.post("/parse/stream")
async def parse_document_stream(file: UploadFile | None = File(default=None)) -> StreamingResponse:
    """Same parse as /api/parse, reported live as server-sent events (see services/progress_stream.py)."""
    return StreamingResponse(event_stream(lambda: _parse(file)), media_type="text/event-stream", headers=SSE_HEADERS)
