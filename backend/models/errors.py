from typing import Literal

from pydantic import BaseModel


class ErrorInfo(BaseModel):
    code: str
    message: str


class DocumentError(ErrorInfo):
    """A non-fatal problem recorded while processing a document (e.g. one bad page)."""

    page: int | None = None


class ErrorResponse(BaseModel):
    status: Literal["error"] = "error"
    error: ErrorInfo


class AppError(Exception):
    """Raised anywhere in the pipeline to return a structured error to the client."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
