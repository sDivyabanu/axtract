"""Document management endpoints: upload, list, retrieve, download, delete.

All endpoints require authentication via Supabase JWT.
Ownership is enforced in every query — never trust client-supplied user_id.
"""

from __future__ import annotations

import json
import logging
from time import perf_counter

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from auth.supabase import get_user_id
from crypto.encryption import decrypt_document, encrypt_document, get_key_version
from db import prisma_client as db
from db.storage import delete_from_storage, download_encrypted, storage_path, upload_encrypted
from models.errors import AppError
from services.parse_service import parse_upload
from utils.files import sanitize_filename

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    user_id: str = Depends(get_user_id),
):
    """Upload, encrypt, store, parse, and save results for a document."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    filename = sanitize_filename(file.filename)
    file_bytes = await file.read()
    file_size = len(file_bytes)

    if file_size == 0:
        raise HTTPException(status_code=400, detail="File is empty.")

    mime_type = file.content_type or "application/octet-stream"

    # Ensure profile exists
    await db.upsert_profile(user_id)

    # Create document record
    doc = await db.create_document(
        user_id=user_id,
        original_filename=filename,
        mime_type=mime_type,
        file_size_bytes=file_size,
    )
    doc_id = str(doc["id"])

    # Encrypt and upload
    enc = await run_in_threadpool(encrypt_document, file_bytes)
    path = storage_path(user_id, doc_id, "v1")

    try:
        await upload_encrypted(path, enc.ciphertext)
    except Exception as exc:
        await db.delete_document(doc_id, user_id)
        logger.error("Storage upload failed for doc %s: %s", doc_id, exc)
        raise HTTPException(status_code=502, detail="Failed to store encrypted document.")

    # Save version
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
    version_id = str(version["id"])

    # Create processing run
    run = await db.create_processing_run(doc_id, version_id, user_id)
    run_id = str(run["id"])

    # Parse document using the same full pipeline as /api/parse
    # (region routing, preview artifacts, table enhancement, cross-page merge)
    started = perf_counter()
    try:
        await file.seek(0)  # rewind: the upload was already read for hashing
        response = await run_in_threadpool(parse_upload, file)

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
            doc_id, user_id, "processed",
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

    except AppError:
        processing_time_ms = round((perf_counter() - started) * 1000)
        await db.complete_processing_run(
            run_id, status="failed",
            processing_time_ms=processing_time_ms,
            error_code="PARSE_ERROR",
            error_message="Document parsing failed.",
        )
        await db.update_document_status(doc_id, user_id, "failed")
        raise
    except Exception as exc:
        processing_time_ms = round((perf_counter() - started) * 1000)
        await db.complete_processing_run(
            run_id, status="failed",
            processing_time_ms=processing_time_ms,
            error_code="PROCESSING_FAILED",
            error_message=str(exc),
        )
        await db.update_document_status(doc_id, user_id, "failed")
        logger.exception("Processing failed for doc %s", doc_id)
        raise HTTPException(status_code=500, detail="Document processing failed.")


@router.get("")
async def list_documents(
    limit: int = 50,
    offset: int = 0,
    user_id: str = Depends(get_user_id),
):
    """List the current user's documents."""
    docs = await db.list_documents(user_id, limit=min(limit, 100), offset=offset)
    return {"documents": docs}


@router.get("/{document_id}")
async def get_document(
    document_id: str,
    user_id: str = Depends(get_user_id),
):
    """Get document metadata."""
    doc = await db.get_document(document_id, user_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    return doc


@router.get("/{document_id}/result")
async def get_document_result(
    document_id: str,
    user_id: str = Depends(get_user_id),
):
    """Retrieve the latest parsing result without re-processing."""
    doc = await db.get_document(document_id, user_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    output = await db.get_latest_output(document_id, user_id)
    if not output:
        raise HTTPException(status_code=404, detail="No processing result available.")

    return {
        "document_id": document_id,
        "result": output["response_json"],
        "markdown": output["markdown"],
        "schema_version": output["schema_version"],
        "created_at": str(output["created_at"]),
    }


@router.get("/{document_id}/history")
async def get_processing_history(
    document_id: str,
    user_id: str = Depends(get_user_id),
):
    """Get processing run history for a document."""
    doc = await db.get_document(document_id, user_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    history = await db.get_processing_history(document_id, user_id)
    return {"runs": history}


@router.get("/{document_id}/download")
async def download_document(
    document_id: str,
    user_id: str = Depends(get_user_id),
):
    """Download the original decrypted document."""
    doc = await db.get_document(document_id, user_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    version = await db.get_latest_version(document_id)
    if not version:
        raise HTTPException(status_code=404, detail="No stored version found.")

    try:
        ciphertext = await download_encrypted(version["storage_path"])
    except Exception:
        raise HTTPException(status_code=502, detail="Failed to retrieve file from storage.")

    try:
        plaintext = await run_in_threadpool(
            decrypt_document,
            ciphertext,
            bytes(version["encryption_nonce"]),
            bytes(version["encrypted_dek"]),
            bytes(version["dek_wrapping_nonce"]),
        )
    except Exception:
        raise HTTPException(status_code=500, detail="Decryption failed — file may be corrupted.")

    return Response(
        content=plaintext,
        media_type=doc["mime_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{doc["original_filename"]}"',
        },
    )


@router.delete("/{document_id}", status_code=204)
async def delete_document_endpoint(
    document_id: str,
    user_id: str = Depends(get_user_id),
):
    """Delete a document and all associated data and storage objects."""
    doc = await db.get_document(document_id, user_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    # Delete storage objects
    paths = await db.get_version_storage_paths(document_id)
    for path in paths:
        try:
            await delete_from_storage(path)
        except Exception:
            logger.warning("Failed to delete storage object: %s", path)

    # Cascade delete handles versions, runs, outputs
    await db.delete_document(document_id, user_id)
