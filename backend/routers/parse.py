from fastapi import APIRouter, File, UploadFile
from starlette.concurrency import run_in_threadpool

from models.document import DocumentResponse
from services.ingestion_service import parse_universal_upload

router = APIRouter(prefix="/api", tags=["parse"])


@router.post("/parse", response_model=DocumentResponse)
async def parse_document(file: UploadFile | None = File(default=None)) -> DocumentResponse:
    """Parse any document type with automatic format detection.
    
    Supports: PDF, DOCX, PPTX, XLSX, JPG, PNG, EML, MSG
    Automatically detects file type from content (magic bytes, MIME), not just extension.
    Email files are parsed with all attachments extracted and processed recursively.
    """
    return await run_in_threadpool(parse_universal_upload, file)
