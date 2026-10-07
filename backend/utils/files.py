import logging
import re
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_MAX_FILENAME_LENGTH = 255


def sanitize_filename(name: str) -> str:
    """Return a safe display name: basename only, no control characters. Never used as a filesystem path."""
    basename = Path(name.replace("\\", "/")).name
    cleaned = _CONTROL_CHARS.sub("", basename).strip()
    return cleaned[:_MAX_FILENAME_LENGTH] or "document"


def get_extension(filename: str) -> str:
    """Lowercase extension without the dot, e.g. 'pdf'. Empty string if none."""
    return Path(filename).suffix.lower().lstrip(".")


def save_upload_to_temp(upload: UploadFile, extension: str) -> Path:
    """Stream the upload to uploads/ under a generated name. The client filename is never used on disk."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{uuid4().hex}.{extension}"
    with path.open("wb") as out:
        shutil.copyfileobj(upload.file, out)
    return path


def remove_temp_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete temporary file %s", path, exc_info=True)
