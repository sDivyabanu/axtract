"""Opt-in local development boundary. Never infer safety from Host/forwarded headers."""
from __future__ import annotations

import os
import re
import socket
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

LOCAL_USER_ID = "axtract-local-development"
LOCAL_USER = {"sub": LOCAL_USER_ID, "email": "local@axtract.invalid", "local_development": True}


def enabled() -> bool:
    return os.environ.get("AXTRACT_LOCAL_NO_AUTH", "false").strip().lower() == "true"


def loopback(host: str) -> bool:
    return host in ("127.0.0.1", "::1")


def bind_loopback(host: str, port: int) -> socket.socket:
    if not loopback(host):
        raise RuntimeError("Local no-auth requires an explicit 127.0.0.1 or ::1 listening address.")
    sock = socket.socket(socket.AF_INET6 if host == "::1" else socket.AF_INET)
    try:
        if host == "::1":
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.bind((host, port))
        sock.listen(128)
        return sock
    except BaseException:
        sock.close()
        raise


def verify_binding(app) -> None:
    """The dedicated launcher supplies the actual bound socket, not a claimed env host."""
    sock = getattr(app.state, "local_no_auth_socket", None)
    if not isinstance(sock, socket.socket) or not loopback(sock.getsockname()[0]):
        raise RuntimeError("Local no-auth must be started with backend/serve_local.py on loopback.")
    if not sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN):
        raise RuntimeError("Local no-auth socket is not listening.")


def validate_request(request: Request) -> None:
    if not enabled():
        raise HTTPException(503, "Local development mode is disabled.")
    try:
        verify_binding(request.app)
    except (RuntimeError, OSError) as exc:
        raise HTTPException(403, "Local no-auth listener has not been verified.") from exc
    # proxy_headers=False in the launcher keeps the peer address authoritative.
    if not request.client or not loopback(request.client.host):
        raise HTTPException(403, "Local no-auth accepts loopback clients only.")
    if any(h in request.headers for h in ("forwarded", "x-forwarded-for", "x-forwarded-host")):
        raise HTTPException(403, "Local no-auth does not support reverse proxies.")
    if request.url.hostname not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, "Local no-auth requires a loopback Host.")
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
        except ValueError as exc:
            raise HTTPException(403, "Invalid request origin.") from exc
        if parsed.scheme not in ("http", "https") or parsed.hostname not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(403, "Local no-auth rejects non-local origins.")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Local no-auth rejects cross-site requests.")


def data_dir() -> Path:
    if not enabled():
        raise RuntimeError("Local document storage is disabled.")
    base = Path(os.environ.get("AXTRACT_LOCAL_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
    # Namespace cannot be removed even when the base directory is customized.
    path = base.resolve() / "local-no-auth"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def master_key() -> bytes:
    path = data_dir() / "master.key"
    try:
        with path.open("xb") as stream:
            os.chmod(path, 0o600)
            stream.write(os.urandom(32))
    except FileExistsError:
        pass
    key = path.read_bytes()
    if len(key) != 32:
        raise RuntimeError("Invalid local development encryption key; restore it from your local backup.")
    return key


def object_path(path: str) -> Path:
    parts = path.split("/")
    if len(parts) != 3 or parts[0] != LOCAL_USER_ID or any(p in ("", ".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in parts):
        raise ValueError("Invalid local development storage path.")
    root = (data_dir() / "objects").resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise ValueError("Storage path escapes local namespace.")
    return target


async def get_current_user(request: Request) -> dict:
    validate_request(request)
    return dict(LOCAL_USER)


def get_user_id(request: Request) -> str:
    validate_request(request)
    return LOCAL_USER_ID
