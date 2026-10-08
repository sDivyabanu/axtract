import asyncio

from fastapi import APIRouter, File, UploadFile

from models.document import DocumentResponse
from models.errors import AppError
from services.parse_service import parse_upload
from utils.jobs import run_parse_job
from utils.deadline import HARD_LIMIT_SECONDS

router = APIRouter(prefix="/api", tags=["parse"])


@router.post("/parse", response_model=DocumentResponse)
async def parse_document(file: UploadFile | None = File(default=None)) -> DocumentResponse:
    try:
        return await asyncio.wait_for(
            run_parse_job(parse_upload, file), timeout=HARD_LIMIT_SECONDS
        )
    except asyncio.TimeoutError as exc:
        raise AppError(
            "TIMEOUT",
            f"Processing exceeded the {HARD_LIMIT_SECONDS} second limit.",
            status_code=504,
        ) from exc
