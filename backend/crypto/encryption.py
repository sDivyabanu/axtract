"""AES-256-GCM envelope encryption for document storage.

Envelope encryption scheme:
  1. Generate a random 256-bit Document Encryption Key (DEK).
  2. Encrypt the document plaintext with AES-256-GCM using the DEK and a random 96-bit nonce.
  3. Wrap (encrypt) the DEK with the server master key using AES-256-GCM and a separate random nonce.
  4. Store: ciphertext, document nonce, wrapped DEK, wrapping nonce.
  5. The GCM authentication tag is appended to the ciphertext by the cryptography library.

The master key is loaded from AXTRACT_MASTER_KEY_BASE64 (base64-encoded 32-byte key).
Never log or persist the master key or plaintext DEKs.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12  # 96-bit nonce for AES-GCM
_DEK_BYTES = 32    # 256-bit DEK

_master_key: bytes | None = None


def _get_master_key() -> bytes:
    global _master_key
    if _master_key is None:
        raw = os.environ.get("AXTRACT_MASTER_KEY_BASE64", "")
        if not raw:
            raise RuntimeError(
                "AXTRACT_MASTER_KEY_BASE64 environment variable is not set. "
                "Generate one with: python -c \"import os,base64; print(base64.b64encode(os.urandom(32)).decode())\""
            )
        _master_key = base64.b64decode(raw)
        if len(_master_key) != 32:
            raise RuntimeError("Master key must be exactly 32 bytes (256 bits).")
    return _master_key


def get_key_version() -> int:
    return int(os.environ.get("AXTRACT_KEY_VERSION", "1"))


class EncryptionResult:
    __slots__ = ("ciphertext", "nonce", "encrypted_dek", "dek_wrapping_nonce", "content_hash")

    def __init__(
        self,
        ciphertext: bytes,
        nonce: bytes,
        encrypted_dek: bytes,
        dek_wrapping_nonce: bytes,
        content_hash: str,
    ):
        self.ciphertext = ciphertext
        self.nonce = nonce
        self.encrypted_dek = encrypted_dek
        self.dek_wrapping_nonce = dek_wrapping_nonce
        self.content_hash = content_hash


def encrypt_document(plaintext: bytes) -> EncryptionResult:
    """Encrypt document bytes using envelope encryption with AES-256-GCM."""
    content_hash = hashlib.sha256(plaintext).hexdigest()

    dek = os.urandom(_DEK_BYTES)
    doc_nonce = os.urandom(_NONCE_BYTES)
    doc_cipher = AESGCM(dek)
    ciphertext = doc_cipher.encrypt(doc_nonce, plaintext, None)

    master_key = _get_master_key()
    wrapping_nonce = os.urandom(_NONCE_BYTES)
    wrapping_cipher = AESGCM(master_key)
    encrypted_dek = wrapping_cipher.encrypt(wrapping_nonce, dek, None)

    return EncryptionResult(
        ciphertext=ciphertext,
        nonce=doc_nonce,
        encrypted_dek=encrypted_dek,
        dek_wrapping_nonce=wrapping_nonce,
        content_hash=content_hash,
    )


def decrypt_document(
    ciphertext: bytes,
    nonce: bytes,
    encrypted_dek: bytes,
    dek_wrapping_nonce: bytes,
) -> bytes:
    """Decrypt document bytes. Raises on authentication failure."""
    master_key = _get_master_key()
    wrapping_cipher = AESGCM(master_key)
    dek = wrapping_cipher.decrypt(dek_wrapping_nonce, encrypted_dek, None)

    doc_cipher = AESGCM(dek)
    return doc_cipher.decrypt(nonce, ciphertext, None)
