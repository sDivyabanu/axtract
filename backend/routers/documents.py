"""Authenticated document persistence: upload, list, retrieve, download, delete.

These endpoints are additive. Parsing itself is main's `parse_upload` pipeline, unchanged;
this router only encrypts and stores the original, records the run, and saves the result.
Ownership is enforced in every query; a client-supplied user id is never trusted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from io import BytesIO
from pathlib import Path

from pydantic import BaseModel, Field

from fastapi.responses import StreamingResponse
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as BytesUpload

from auth.supabase import get_user_id
from crypto.encryption import decrypt_document, encrypt_document, get_key_version
from db import prisma_client as db
from db.storage import delete_from_storage, download_encrypted, storage_path, upload_encrypted
from extractors.registry import get_extractor
from models.document import DocumentResponse
from models.errors import AppError
from services import preview_service
from services import progress
from services.parse_service import parse_upload
from services.progress_stream import SSE_HEADERS, event_stream
from utils import deadline
from utils.deadline import HARD_LIMIT_SECONDS
from utils.files import get_extension, sanitize_filename

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])

_PREVIEW_ID = re.compile(r"^[0-9a-f]{32}$")


def _as_dict(value) -> dict:
    """asyncpg returns jsonb as text unless a codec is registered."""
    return json.loads(value) if isinstance(value, (str, bytes)) else value


async def _decrypt_original(version: dict) -> bytes:
    try:
        ciphertext = await download_encrypted(version["storage_path"])
    except Exception:
        raise HTTPException(status_code=502, detail="Failed to retrieve file from storage.")
    try:
        return await run_in_threadpool(
            decrypt_document,
            ciphertext,
            bytes(version["encryption_nonce"]),
            bytes(version["encrypted_dek"]),
            bytes(version["dek_wrapping_nonce"]),
        )
    except Exception:
        raise HTTPException(status_code=500, detail="Decryption failed; the file may be corrupted.")


def _restore_preview_sync(preview_id: str, plaintext: bytes, file_type: str):
    deadline.start()
    with tempfile.NamedTemporaryFile(suffix=f".{file_type}", delete=False) as tmp:
        tmp.write(plaintext)
        tmp_path = Path(tmp.name)
    try:
        return preview_service.prepare(preview_id, tmp_path, file_type)
    finally:
        tmp_path.unlink(missing_ok=True)


async def _ensure_preview(result: dict, version: dict | None) -> dict:
    """Recreate the source-viewer page cache for a saved result from the decrypted original.

    Block boxes are already in the saved result; only the rendered pages are rebuilt.
    If that is impossible the result says so instead of claiming a preview it cannot serve.
    """
    preview_id = result.get("document_id", "")
    if not result.get("preview_available") or not _PREVIEW_ID.match(preview_id):
        return result
    if (preview_service.PREVIEW_ROOT / preview_id).exists() or version is None:
        return result
    try:
        plaintext = await _decrypt_original(version)
        info = await run_in_threadpool(
            _restore_preview_sync, preview_id, plaintext, result.get("file_type", "")
        )
    except HTTPException:
        info = None
    if info is None or not info.available:
        result = {
            **result,
            "preview_available": False,
            "preview_pages": 0,
            "preview_error": (info.error if info else None) or "The original could not be retrieved for preview.",
        }
    return result


@router.post("", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    user_id: str = Depends(get_user_id),
):
    """Encrypt and store the original, parse it with the standard pipeline, save the result."""
    return await _store_and_parse(file, user_id)


@router.post("/stream")
async def upload_document_stream(
    file: UploadFile = File(...),
    user_id: str = Depends(get_user_id),
):
    """Same as POST /api/documents, reported live as server-sent events (authenticated like the original)."""
    return StreamingResponse(event_stream(lambda: _store_and_parse(file, user_id)),
                             media_type="text/event-stream", headers=SSE_HEADERS)


async def _store_and_parse(file: UploadFile, user_id: str):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    filename = sanitize_filename(file.filename)
    file_type = get_extension(filename)
    file_bytes = await file.read()

    if not file_bytes:
        raise AppError("EMPTY_FILE", "The uploaded file is empty.", status_code=400)
    if get_extractor(file_type) is None:
        raise AppError("UNSUPPORTED_FORMAT", "This file type is not supported yet.", status_code=415)

    await db.upsert_profile(user_id)
    doc = await db.create_document(
        user_id=user_id,
        original_filename=filename,
        mime_type=file.content_type or "application/octet-stream",
        file_size_bytes=len(file_bytes),
    )
    doc_id = str(doc["id"])

    try:
        enc = await run_in_threadpool(encrypt_document, file_bytes)
        path = storage_path(user_id, doc_id, "v1")
        await upload_encrypted(path, enc.ciphertext)
        version = await db.create_document_version(
            document_id=doc_id,
            version_number=1,
            storage_path=path,
            encryption_nonce=enc.nonce,
            encrypted_dek=enc.encrypted_dek,
            dek_wrapping_nonce=enc.dek_wrapping_nonce,
            key_version=get_key_version(),
            ciphertext_size_bytes=len(enc.ciphertext),
            content_hash=enc.content_hash,
        )
    except Exception as exc:  # noqa: BLE001 - nothing may be left half-stored
        await db.delete_document(doc_id, user_id)
        logger.error("Storing document %s failed: %s", doc_id, type(exc).__name__)
        raise HTTPException(status_code=502, detail="Failed to store encrypted document.")

    version_id = str(version["id"])
    run = await db.create_processing_run(doc_id, version_id, user_id)
    run_id = str(run["id"])

    try:
        response = await asyncio.wait_for(
            run_in_threadpool(parse_upload, BytesUpload(file=BytesIO(file_bytes), filename=filename)),
            timeout=HARD_LIMIT_SECONDS,
        )
    except (asyncio.CancelledError, progress.Cancelled):  # the client left a streamed upload
        await asyncio.shield(_mark_failed(doc_id, run_id, user_id, "CANCELLED", "Processing was cancelled."))
        raise
    except asyncio.TimeoutError:
        tracker = progress.current()
        if tracker is not None:
            tracker.cancelled.set()
        await _mark_failed(doc_id, run_id, user_id, "TIMEOUT", "Processing time limit exceeded.")
        raise AppError("TIMEOUT", f"Processing exceeded the {HARD_LIMIT_SECONDS} second limit.", status_code=504)
    except AppError as exc:
        await _mark_failed(doc_id, run_id, user_id, exc.code, exc.message)
        raise
    except Exception as exc:  # noqa: BLE001
        await _mark_failed(doc_id, run_id, user_id, "PROCESSING_FAILED", type(exc).__name__)
        logger.exception("Processing failed for doc %s", doc_id)
        raise HTTPException(status_code=500, detail="Document processing failed.")

    response_json = response.model_dump_json()
    await db.save_document_output(run_id, response_json, response.markdown)
    await db.complete_processing_run(
        run_id,
        status="completed",
        processing_time_ms=response.processing_time_ms,
        page_count=response.page_count,
        block_count=len(response.blocks),
    )
    await db.update_document_status(
        doc_id,
        user_id,
        "processed",
        page_count=response.page_count,
        block_count=len(response.blocks),
        processing_time_ms=response.processing_time_ms,
    )
    return {
        "document_id": doc_id,
        "version_id": version_id,
        "processing_run_id": run_id,
        "result": json.loads(response_json),
    }


async def _mark_failed(doc_id: str, run_id: str, user_id: str, code: str, message: str) -> None:
    await db.complete_processing_run(run_id, status="failed", error_code=code, error_message=message)
    await db.update_document_status(doc_id, user_id, "failed")


@router.get("")
async def list_documents(
    limit: int = 50,
    offset: int = 0,
    user_id: str = Depends(get_user_id),
):
    docs = await db.list_documents(user_id, limit=min(limit, 100), offset=offset)
    return {"documents": docs}


@router.get("/{document_id}")
async def get_document(document_id: str, user_id: str = Depends(get_user_id)):
    doc = await db.get_document(document_id, user_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    return doc


@router.get("/{document_id}/result")
async def get_document_result(document_id: str, user_id: str = Depends(get_user_id)):
    """Saved parse result, returned without re-parsing."""
    doc = await db.get_document(document_id, user_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    output = await db.get_latest_output(document_id, user_id)
    if not output:
        raise HTTPException(status_code=404, detail="No processing result available.")

    result = _as_dict(output["response_json"])
    result = await _ensure_preview(result, await db.get_latest_version(document_id))
    return {
        "document_id": document_id,
        "result": result,
        "markdown": output["markdown"],
        "schema_version": output["schema_version"],
        "created_at": str(output["created_at"]),
    }


@router.get("/{document_id}/history")
async def get_processing_history(document_id: str, user_id: str = Depends(get_user_id)):
    doc = await db.get_document(document_id, user_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    return {"runs": await db.get_processing_history(document_id, user_id)}


@router.get("/{document_id}/download")
async def download_document(document_id: str, user_id: str = Depends(get_user_id)):
    """The original file, decrypted for its owner."""
    doc = await db.get_document(document_id, user_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    version = await db.get_latest_version(document_id)
    if not version:
        raise HTTPException(status_code=404, detail="No stored version found.")

    plaintext = await _decrypt_original(version)
    return Response(
        content=plaintext,
        media_type=doc["mime_type"],
        headers={"Content-Disposition": f'attachment; filename="{doc["original_filename"]}"'},
    )


@router.delete("/{document_id}", status_code=204)
async def delete_document_endpoint(document_id: str, user_id: str = Depends(get_user_id)):
    doc = await db.get_document(document_id, user_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    for path in await db.get_version_storage_paths(document_id):
        try:
            await delete_from_storage(path)
        except Exception:
            logger.warning("Failed to delete a storage object for document %s", document_id)

    await db.delete_document(document_id, user_id)


# ---------------------------------------------------------------------------------------------
# AXTRACT Verify: targeted escalation and explicit recovery (never part of the normal parse path)
# ---------------------------------------------------------------------------------------------


class PromoteBody(BaseModel):
    recovery_ids: list[str] = Field(min_length=1, max_length=50)
    reason: str = Field(min_length=3, max_length=500)


async def _load_saved(document_id: str, user_id: str):
    doc = await db.get_document(document_id, user_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    output = await db.get_latest_output(document_id, user_id)
    version = await db.get_latest_version(document_id)
    if not output or not version:
        raise HTTPException(status_code=404, detail="No processing result available.")
    resp = DocumentResponse.model_validate(_as_dict(output["response_json"]))
    if not resp.validation:
        raise HTTPException(status_code=409, detail="This result has no validation report to escalate.")
    return resp, version


def _escalate_sync(plaintext: bytes, resp: DocumentResponse):
    from verify.recovery import escalate

    deadline.start()
    with tempfile.NamedTemporaryFile(suffix=f".{resp.file_type}", delete=False) as tmp:
        tmp.write(plaintext)
        path = Path(tmp.name)
    try:
        return escalate(path, resp, resp.validation, budget_s=40.0)
    finally:
        path.unlink(missing_ok=True)


@router.post("/{document_id}/verify/escalate")
async def escalate_validation(document_id: str, user_id: str = Depends(get_user_id)):
    """Re-read the units Verify flagged with secondary providers. Stores candidates; changes no output."""
    resp, version = await _load_saved(document_id, user_id)
    plaintext = await _decrypt_original(version)
    try:
        esc = await asyncio.wait_for(run_in_threadpool(_escalate_sync, plaintext, resp), timeout=HARD_LIMIT_SECONDS)
    except asyncio.TimeoutError:
        raise AppError("TIMEOUT", f"Escalation exceeded the {HARD_LIMIT_SECONDS} second limit.", status_code=504)
    resp.validation = esc.report.model_dump(mode="json")
    await db.update_latest_output_json(document_id, user_id, resp.model_dump_json())
    return {"recoveries": [r.model_dump(mode="json") for r in esc.recoveries], "notes": esc.notes, "validation": resp.validation}


@router.post("/{document_id}/verify/promote")
async def promote_recovery(document_id: str, body: PromoteBody, user_id: str = Depends(get_user_id)):
    """Explicitly accept escalation candidates. Saved as a NEW result; the earlier result is kept for audit."""
    from verify.recovery import PromotionRefused, promote

    resp, version = await _load_saved(document_id, user_id)
    try:
        promoted = promote(resp, resp.validation, body.recovery_ids, decided_by="explicit_request", reason=body.reason)
    except PromotionRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    run = await db.create_processing_run(document_id, str(version["id"]), user_id)
    run_id = str(run["id"])
    out = promoted.response
    await db.save_document_output(run_id, out.model_dump_json(), out.markdown)
    await db.complete_processing_run(run_id, status="completed", processing_time_ms=0, page_count=out.page_count, block_count=len(out.blocks))
    return {"document_id": document_id, "processing_run_id": run_id, "result": json.loads(out.model_dump_json())}
