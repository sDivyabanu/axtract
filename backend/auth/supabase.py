"""Supabase JWT verification for FastAPI.

Verifies access tokens using the project's JWKS endpoint for asymmetric
signature validation. Falls back to HS256 with the JWT secret for projects
still using symmetric signing.

Never trust a user_id from the frontend — always extract it from the
verified token.
"""

from __future__ import annotations

import os
import logging
from typing import Any

import httpx
import jwt as pyjwt
from jwt import PyJWKClient

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

_bearer = HTTPBearer()
_jwks_client: PyJWKClient | None = None
_jwt_secret: str | None = None


def _get_supabase_url() -> str:
    url = os.environ.get("SUPABASE_URL", "")
    if not url:
        raise RuntimeError("SUPABASE_URL environment variable is not set.")
    return url.rstrip("/")


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        jwks_url = os.environ.get(
            "SUPABASE_JWKS_URL",
            f"{_get_supabase_url()}/auth/v1/.well-known/jwks.json",
        )
        _jwks_client = _HttpxJWKClient(jwks_url, cache_keys=True)
    return _jwks_client


class _HttpxJWKClient(PyJWKClient):
    """PyJWKClient that fetches the JWKS via httpx instead of urllib.

    urllib fails with CERTIFICATE_VERIFY_FAILED on Python installs without
    a system CA bundle (common on macOS python.org installs); httpx ships
    with certifi so the fetch always works.
    """

    def fetch_data(self):  # type: ignore[override]
        with httpx.Client(timeout=15) as client:
            resp = client.get(self.uri)
            resp.raise_for_status()
            return resp.json()


def _get_jwt_secret() -> str | None:
    global _jwt_secret
    if _jwt_secret is None:
        _jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "")
    return _jwt_secret or None


def _verify_token(token: str) -> dict[str, Any]:
    """Verify a Supabase access token and return the decoded payload."""
    supabase_url = _get_supabase_url()
    expected_issuer = f"{supabase_url}/auth/v1"

    # Try asymmetric verification via JWKS first
    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        payload = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            issuer=expected_issuer,
            options={"verify_aud": False},
        )
        return payload
    except (pyjwt.exceptions.PyJWKClientError, pyjwt.exceptions.DecodeError):
        pass

    # Fall back to symmetric HS256 if JWKS fails and a secret is configured
    secret = _get_jwt_secret()
    if secret:
        try:
            payload = pyjwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                issuer=expected_issuer,
                options={"verify_aud": False},
            )
            return payload
        except pyjwt.exceptions.DecodeError:
            pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token.",
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict[str, Any]:
    """FastAPI dependency: verify the Bearer token and return the payload.

    The 'sub' field contains the Supabase auth.users UUID.
    """
    payload = _verify_token(credentials.credentials)

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing user identity.",
        )

    return payload


def get_user_id(user: dict[str, Any] = Depends(get_current_user)) -> str:
    """Convenience dependency: extract user UUID string from verified token."""
    return user["sub"]
