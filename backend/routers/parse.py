from fastapi import APIRouter, File, UploadFile
from starlette.concurrency import run_in_threadpool

from models.document import DocumentResponse
from services.parse_service import parse_upload

router = APIRouter(prefix="/api", tags=["parse"])


@router.post("/parse", response_model=DocumentResponse)
async def parse_document(file: UploadFile | None = File(default=None)) -> DocumentResponse:
    return await run_in_threadpool(parse_upload, file)
