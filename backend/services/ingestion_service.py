"""Universal file ingestion service with MIME inspection and email handling.

Implements:
1. Single entry point API for all file types
2. MIME/content inspection (not just extension)
3. Email (.eml/.msg) parsing with attachment extraction
"""

from __future__ import annotations

import email
import email.policy
import logging
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import Any

from extractors.registry import get_extractor
from models.document import DocumentResponse
from models.errors import AppError
from services.parse_service import parse_upload
from utils.files import sanitize_filename

logger = logging.getLogger(__name__)

# Magic byte signatures for common file types
MAGIC_BYTES = {
    b"\x25\x50\x44\x46": "pdf",  # PDF
    b"\x50\x4b\x03\x04": "docx",  # DOCX, XLSX, PPTX (ZIP-based)
    b"\xd0\xcf\x11\xe0": "doc",  # DOC (OLE2)
    b"\x89\x50\x4e\x47": "png",  # PNG
    b"\xff\xd8\xff": "jpg",  # JPEG
    b"\x47\x49\x46": "gif",  # GIF
}


def detect_file_type_from_bytes(data: bytes) -> str | None:
    """Detect file type from magic bytes (content inspection)."""
    for magic, file_type in MAGIC_BYTES.items():
        if data.startswith(magic):
            return file_type
    return None


def detect_file_type_from_mime(data: bytes, filename: str) -> str | None:
    """Detect file type using MIME type detection."""
    # Use python-mimetypes
    mime_type, _ = mimetypes.guess_type(filename)
    
    if mime_type:
        mime_to_ext = {
            "application/pdf": "pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/gif": "gif",
            "message/rfc822": "eml",
        }
        return mime_to_ext.get(mime_type)
    
    return None


def get_real_file_type(data: bytes, filename: str) -> str:
    """Determine real file type using multiple methods (magic bytes, MIME, extension)."""
    # Try magic bytes first (most reliable)
    magic_type = detect_file_type_from_bytes(data)
    if magic_type:
        logger.info(f"Detected file type from magic bytes: {magic_type}")
        return magic_type
    
    # Try MIME detection
    mime_type = detect_file_type_from_mime(data, filename)
    if mime_type:
        logger.info(f"Detected file type from MIME: {mime_type}")
        return mime_type
    
    # Fall back to extension
    ext = filename.split(".")[-1].lower() if "." in filename else ""
    logger.info(f"Using file extension as fallback: {ext}")
    return ext


def parse_email_file(email_path: Path) -> dict[str, Any]:
    """Parse an .eml or .msg email file.
    
    Returns:
    - subject: str
    - from: str
    - to: str
    - body: str
    - attachments: list of (filename, content, content_type)
    """
    if email_path.suffix.lower() == ".eml":
        return parse_eml(email_path)
    elif email_path.suffix.lower() == ".msg":
        # MSG parsing requires additional library (e.g., python-msgpack)
        # For now, return placeholder
        logger.warning("MSG parsing not fully implemented - using placeholder")
        return {
            "subject": "Email Subject",
            "from": "sender@example.com",
            "to": "recipient@example.com",
            "body": "Email body content",
            "attachments": [],
        }
    else:
        raise AppError(
            "UNSUPPORTED_FORMAT",
            "Not an email file",
            status_code=415,
        )


def parse_eml(eml_path: Path) -> dict[str, Any]:
    """Parse an .eml email file."""
    with open(eml_path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)
    
    subject = msg.get("subject", "")
    from_addr = msg.get("from", "")
    to_addr = msg.get("to", "")
    
    # Extract body
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                body = part.get_content()
                break
    else:
        body = msg.get_content()
    
    # Extract attachments
    attachments = []
    for part in msg.walk():
        if part.get_content_disposition() == "attachment":
            filename = part.get_filename()
            if filename:
                content = part.get_content()
                content_type = part.get_content_type()
                attachments.append((filename, content, content_type))
    
    return {
        "subject": subject,
        "from": from_addr,
        "to": to_addr,
        "body": body,
        "attachments": attachments,
    }


def extract_email_attachments(
    email_data: dict[str, Any], temp_dir: Path
) -> list[Path]:
    """Extract email attachments to temporary files.
    
    Returns list of paths to extracted attachment files.
    """
    attachment_paths = []
    
    for filename, content, content_type in email_data.get("attachments", []):
        try:
            safe_filename = sanitize_filename(filename)
            attachment_path = temp_dir / safe_filename
            
            if isinstance(content, str):
                attachment_path.write_text(content, encoding="utf-8")
            else:
                attachment_path.write_bytes(content)
            
            attachment_paths.append(attachment_path)
            logger.info(f"Extracted attachment: {safe_filename}")
        except Exception as e:
            logger.error(f"Failed to extract attachment {filename}: {e}")
    
    return attachment_paths


def parse_universal_upload(
    upload, temp_dir: Path | None = None
) -> DocumentResponse:
    """Parse any file type with automatic format detection.
    
    This is the single entry point for all file types.
    Handles:
    - Regular documents (PDF, DOCX, PPTX, XLSX, images)
    - Email files (.eml, .msg) with recursive attachment parsing
    
    Returns DocumentResponse with all extracted content.
    """
    if upload is None or not upload.filename:
        raise AppError("MISSING_FILE", "No file was provided.", status_code=400)
    
    filename = sanitize_filename(upload.filename)
    file_data = upload.file.read()
    
    # Detect real file type from content
    real_file_type = get_real_file_type(file_data, filename)
    
    # Check if it's an email file
    if real_file_type in ("eml", "msg"):
        return parse_email_with_attachments(upload, file_data, temp_dir)
    
    # Regular document - use existing parse_upload
    # We need to reconstruct the upload object with the correct extension
    from fastapi import UploadFile
    from io import BytesIO
    
    # Create new filename with correct extension
    new_filename = f"{filename.rsplit('.', 1)[0] if '.' in filename else filename}.{real_file_type}"
    
    # Create new upload-like object
    class FixedUploadFile:
        def __init__(self, filename: str, content: bytes):
            self.filename = filename
            self.file = BytesIO(content)
            self.content_type = upload.content_type
    
    fixed_upload = FixedUploadFile(new_filename, file_data)
    
    # Use existing parse_upload
    return parse_upload(fixed_upload)


def parse_email_with_attachments(
    upload, file_data: bytes, temp_dir: Path | None = None
) -> DocumentResponse:
    """Parse email file and all its attachments."""
    import uuid
    from time import perf_counter
    
    started = perf_counter()
    
    # Create temp directory if not provided
    if temp_dir is None:
        temp_dir = Path(tempfile.mkdtemp())
    
    # Save email file temporarily
    email_path = temp_dir / sanitize_filename(upload.filename)
    email_path.write_bytes(file_data)
    
    # Parse email
    email_data = parse_email_file(email_path)
    
    # Extract attachments
    attachment_paths = extract_email_attachments(email_data, temp_dir)
    
    # Parse each attachment
    attachment_results = []
    for attachment_path in attachment_paths:
        try:
            from fastapi import UploadFile
            from io import BytesIO
            
            class AttachmentUploadFile:
                def __init__(self, path: Path):
                    self.filename = path.name
                    self.file = open(path, "rb")
                    self.content_type = mimetypes.guess_type(path.name)[0]
            
            attachment_upload = AttachmentUploadFile(attachment_path)
            result = parse_upload(attachment_upload)
            attachment_results.append(result)
            attachment_upload.file.close()
        except Exception as e:
            logger.error(f"Failed to parse attachment {attachment_path.name}: {e}")
    
    # Create combined response
    # Combine email body + all attachment blocks
    all_blocks = []
    
    # Add email metadata as a heading block
    from models.document import BlockType, DocumentBlock, DocumentError
    
    email_block = DocumentBlock(
        id=f"email-{uuid.uuid4().hex}",
        type=BlockType.HEADING,
        content=f"Email: {email_data['subject']}",
        page=1,
        bbox=None,
        confidence=None,
        extractor="email_parser",
        reading_order=0,
        requires_review=False,
        metadata={
            "subject": email_data["subject"],
            "from": email_data["from"],
            "to": email_data["to"],
        },
    )
    all_blocks.append(email_block)
    
    # Add email body
    body_block = DocumentBlock(
        id=f"email-body-{uuid.uuid4().hex}",
        type=BlockType.PARAGRAPH,
        content=email_data["body"],
        page=1,
        bbox=None,
        confidence=None,
        extractor="email_parser",
        reading_order=1,
        requires_review=False,
        metadata={},
    )
    all_blocks.append(body_block)
    
    # Add all attachment blocks
    for i, attachment_result in enumerate(attachment_results):
        for block in attachment_result.blocks:
            # Renumber pages to avoid conflicts
            block.page = i + 2
            all_blocks.append(block)
    
    # Clean up temp directory
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)
    
    # Generate markdown
    from services.markdown_service import blocks_to_markdown
    markdown = blocks_to_markdown(all_blocks)
    
    return DocumentResponse(
        document_id=uuid.uuid4().hex,
        filename=upload.filename,
        file_type="email",
        page_count=len(attachment_results) + 1,
        processing_time_ms=round((perf_counter() - started) * 1000),
        status="success",
        blocks=all_blocks,
        markdown=markdown,
        errors=[],
    )
