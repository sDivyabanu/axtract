"""File handling utilities: safe filenames, temporary storage, magic-byte validation."""

import logging
import re
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from models.errors import AppError

logger = logging.getLogger(__name__)

import os

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"
# Persistent app data (RAG database, stored data-room files and their previews). Gitignored.
DATA_DIR = Path(os.environ.get("DEALLENS_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_MAX_FILENAME_LENGTH = 255

# Maximum upload size: 100 MB
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

# Magic byte signatures for supported formats.
# fmt: off
_MAGIC_SIGNATURES: dict[str, list[tuple[bytes, int]]] = {
    "pdf":  [(b"%PDF", 0)],
    "docx": [(b"PK\x03\x04", 0)],   # ZIP-based (shared with pptx/xlsx)
    "pptx": [(b"PK\x03\x04", 0)],
    "xlsx": [(b"PK\x03\x04", 0)],
    "png":  [(b"\x89PNG\r\n\x1a\n", 0)],
    "jpg":  [(b"\xff\xd8\xff", 0)],
    "jpeg": [(b"\xff\xd8\xff", 0)],
}
# fmt: on

# Allowed extensions and their canonical types.
SUPPORTED_EXTENSIONS: set[str] = {
    "pdf", "docx", "pptx", "xlsx", "jpg", "jpeg", "png",
}


def sanitize_filename(name: str) -> str:
    """Return a safe display name: basename only, no control characters."""
    basename = Path(name.replace("\\", "/")).name
    cleaned = _CONTROL_CHARS.sub("", basename).strip()
    return cleaned[:_MAX_FILENAME_LENGTH] or "document"


def get_extension(filename: str) -> str:
    """Lowercase extension without the dot, e.g. 'pdf'. Empty string if none."""
    return Path(filename).suffix.lower().lstrip(".")


def validate_magic_bytes(data: bytes, extension: str) -> bool:
    """Check if file content matches expected magic bytes for the extension.

    Returns True if the signature matches or no signature is known.
    """
    sigs = _MAGIC_SIGNATURES.get(extension)
    if not sigs:
        return True  # No known signature — skip check
    for magic, offset in sigs:
        if len(data) > offset and data[offset : offset + len(magic)] == magic:
            return True
    return False


def save_upload_to_temp(upload: UploadFile, extension: str) -> Path:
    """Stream the upload to uploads/ under a generated name.

    Stops (and removes the partial file) as soon as MAX_UPLOAD_BYTES is exceeded, so an
    oversized upload cannot fill the disk before the size check runs.
    """
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{uuid4().hex}.{extension}"
    written = 0
    try:
        with path.open("wb") as out:
            while chunk := upload.file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise AppError(
                        "FILE_TOO_LARGE",
                        f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
                        status_code=413,
                    )
                out.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def remove_temp_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete temporary file %s", path, exc_info=True)
