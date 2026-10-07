"""Supabase Storage client for encrypted document upload/download.

Uses the service-role key for server-side operations on the private
axtract-documents bucket. Never expose this key to the frontend.
"""

from __future__ import annotations

import os
import logging

import httpx

logger = logging.getLogger(__name__)

_BUCKET = None
_supabase_url: str | None = None
_service_key: str | None = None


def _get_config() -> tuple[str, str, str]:
    global _supabase_url, _service_key, _BUCKET
    if _supabase_url is None:
        _supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        _service_key = os.environ.get("SUPABASE_SECRET_KEY", "")
        _BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "axtract-documents")
        if not _supabase_url or not _service_key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SECRET_KEY must be set for storage."
            )
    return _supabase_url, _service_key, _BUCKET  # type: ignore[return-value]


def _headers() -> dict[str, str]:
    url, key, _ = _get_config()
    return {
        "Authorization": f"Bearer {key}",
        "apikey": key,
    }


def storage_path(user_id: str, document_id: str, version_id: str) -> str:
    return f"{user_id}/{document_id}/{version_id}.enc"


async def upload_encrypted(path: str, data: bytes) -> None:
    """Upload ciphertext to the private storage bucket."""
    from db.prisma_client import _with_retries, get_http_client

    url, _, bucket = _get_config()
    endpoint = f"{url}/storage/v1/object/{bucket}/{path}"
    client = await get_http_client()
    headers = {
        **_headers(),
        "Content-Type": "application/octet-stream",
    }

    # Path is deterministic per (user, document, version), so a retried
    # upload overwrites the same object — safe.
    await _with_retries(
        lambda: _post_and_check(client, endpoint, headers, data),
        idempotent=True,
    )


async def _post_and_check(client, endpoint, headers, data) -> None:
    resp = await client.post(endpoint, headers=headers, content=data)
    if resp.status_code not in (200, 201):
        logger.error("Storage upload failed: %s %s", resp.status_code, resp.text)
        raise RuntimeError(f"Storage upload failed: {resp.status_code}")


async def download_encrypted(path: str) -> bytes:
    """Download ciphertext from the private storage bucket."""
    from db.prisma_client import _with_retries, get_http_client

    url, _, bucket = _get_config()
    endpoint = f"{url}/storage/v1/object/{bucket}/{path}"
    client = await get_http_client()

    async def call() -> httpx.Response:
        return await client.get(endpoint, headers=_headers())

    resp = await _with_retries(call, idempotent=True)
    if resp.status_code != 200:
        logger.error("Storage download failed: %s %s", resp.status_code, resp.text)
        raise RuntimeError(f"Storage download failed: {resp.status_code}")
    return resp.content


async def delete_from_storage(path: str) -> None:
    """Delete a file from the private storage bucket."""
    from db.prisma_client import _with_retries, get_http_client

    url, _, bucket = _get_config()
    endpoint = f"{url}/storage/v1/object/{bucket}"
    client = await get_http_client()
    headers = {**_headers(), "Content-Type": "application/json"}

    async def call() -> httpx.Response:
        return await client.request(
            "DELETE", endpoint, headers=headers, json={"prefixes": [path]},
        )

    resp = await _with_retries(call, idempotent=True)
    if resp.status_code not in (200, 204):
        logger.warning("Storage delete failed: %s %s", resp.status_code, resp.text)
