"""Symmetric secret encryption at rest (Fernet, key derived from app secret).

Used for SMTP passwords and similar credentials stored in ``tiqora_*`` tables.
Matches the TOTP secret scheme in :mod:`tiqora.domain.totp`: SHA-256 digest of
``settings.secret_key``, urlsafe-base64-encoded as a Fernet key. Never log the
plaintext or the derived key.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def _fernet(secret_key: str) -> Fernet:
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(secret_key: str, plaintext: str) -> str:
    """Return a Fernet token (ASCII) for *plaintext*."""
    return _fernet(secret_key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(secret_key: str, token: str) -> str | None:
    """Decrypt a Fernet token; return ``None`` on invalid/corrupt input."""
    try:
        return _fernet(secret_key).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return None


__all__ = ["decrypt_secret", "encrypt_secret"]
