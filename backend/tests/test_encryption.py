"""Tests for AES-256-GCM envelope encryption."""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def set_master_key(monkeypatch):
    """Set a test master key for all encryption tests."""
    test_key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("AXTRACT_MASTER_KEY_BASE64", test_key)
    monkeypatch.setenv("AXTRACT_KEY_VERSION", "1")
    import crypto.encryption as enc
    enc._master_key = None


class TestEncryption:
    def test_roundtrip(self):
        from crypto.encryption import decrypt_document, encrypt_document

        plaintext = b"Hello, AXTRACT! This is a test document."
        result = encrypt_document(plaintext)

        decrypted = decrypt_document(
            result.ciphertext,
            result.nonce,
            result.encrypted_dek,
            result.dek_wrapping_nonce,
        )
        assert decrypted == plaintext

    def test_different_ciphertext_each_time(self):
        from crypto.encryption import encrypt_document

        plaintext = b"Same document encrypted twice."
        r1 = encrypt_document(plaintext)
        r2 = encrypt_document(plaintext)

        assert r1.ciphertext != r2.ciphertext
        assert r1.nonce != r2.nonce
        assert r1.encrypted_dek != r2.encrypted_dek

    def test_content_hash(self):
        from crypto.encryption import encrypt_document
        import hashlib

        plaintext = b"Hash test document."
        result = encrypt_document(plaintext)

        expected = hashlib.sha256(plaintext).hexdigest()
        assert result.content_hash == expected

    def test_tampered_ciphertext_rejected(self):
        from crypto.encryption import decrypt_document, encrypt_document

        plaintext = b"Tamper test document."
        result = encrypt_document(plaintext)

        tampered = bytearray(result.ciphertext)
        tampered[0] ^= 0xFF
        tampered = bytes(tampered)

        with pytest.raises(Exception):
            decrypt_document(
                tampered,
                result.nonce,
                result.encrypted_dek,
                result.dek_wrapping_nonce,
            )

    def test_tampered_auth_tag_rejected(self):
        from crypto.encryption import decrypt_document, encrypt_document

        plaintext = b"Auth tag tamper test."
        result = encrypt_document(plaintext)

        tampered = bytearray(result.ciphertext)
        tampered[-1] ^= 0xFF
        tampered = bytes(tampered)

        with pytest.raises(Exception):
            decrypt_document(
                tampered,
                result.nonce,
                result.encrypted_dek,
                result.dek_wrapping_nonce,
            )

    def test_wrong_key_decryption_fails(self, monkeypatch):
        from crypto.encryption import encrypt_document

        plaintext = b"Wrong key test."
        result = encrypt_document(plaintext)

        different_key = base64.b64encode(os.urandom(32)).decode()
        monkeypatch.setenv("AXTRACT_MASTER_KEY_BASE64", different_key)

        import crypto.encryption as enc
        enc._master_key = None

        with pytest.raises(Exception):
            enc.decrypt_document(
                result.ciphertext,
                result.nonce,
                result.encrypted_dek,
                result.dek_wrapping_nonce,
            )

    def test_large_document(self):
        from crypto.encryption import decrypt_document, encrypt_document

        plaintext = os.urandom(5 * 1024 * 1024)  # 5 MB
        result = encrypt_document(plaintext)
        decrypted = decrypt_document(
            result.ciphertext,
            result.nonce,
            result.encrypted_dek,
            result.dek_wrapping_nonce,
        )
        assert decrypted == plaintext

    def test_empty_document(self):
        from crypto.encryption import decrypt_document, encrypt_document

        plaintext = b""
        result = encrypt_document(plaintext)
        decrypted = decrypt_document(
            result.ciphertext,
            result.nonce,
            result.encrypted_dek,
            result.dek_wrapping_nonce,
        )
        assert decrypted == plaintext

    def test_nonce_uniqueness(self):
        from crypto.encryption import encrypt_document

        nonces = set()
        for _ in range(100):
            result = encrypt_document(b"nonce test")
            nonces.add(result.nonce)

        assert len(nonces) == 100

    def test_missing_master_key_raises(self, monkeypatch):
        monkeypatch.delenv("AXTRACT_MASTER_KEY_BASE64", raising=False)
        import crypto.encryption as enc
        enc._master_key = None

        with pytest.raises(RuntimeError, match="AXTRACT_MASTER_KEY_BASE64"):
            enc.encrypt_document(b"test")
