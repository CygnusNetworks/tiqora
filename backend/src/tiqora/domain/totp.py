"""TOTP 2FA: enrollment, confirmation, verification, disable.

Secrets are stored Fernet-encrypted (key derived from ``settings.secret_key``
via SHA-256) — never in plaintext. Verification uses a ±1 step window (30s
step => ~90s total tolerance), matching common authenticator app behaviour.
"""

from __future__ import annotations

import base64
import hashlib

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraUserTotp

_TOTP_VALID_WINDOW = 1


def _fernet_key(secret_key: str) -> bytes:
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class TOTPService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._fernet = Fernet(_fernet_key(settings.secret_key))

    def _encrypt(self, secret: str) -> str:
        return self._fernet.encrypt(secret.encode("utf-8")).decode("ascii")

    def _decrypt(self, token: str) -> str | None:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken:
            return None

    async def _get_row(self, user_id: int) -> TiqoraUserTotp | None:
        result = await self._session.execute(
            select(TiqoraUserTotp).where(TiqoraUserTotp.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def is_enabled(self, user_id: int) -> bool:
        row = await self._get_row(user_id)
        return bool(row is not None and row.enabled)

    async def enroll(self, user_id: int, login: str) -> tuple[str, str]:
        """Create (or reset) a pending enrollment. Returns (secret, otpauth_uri)."""
        secret = pyotp.random_base32()
        row = await self._get_row(user_id)
        if row is None:
            row = TiqoraUserTotp(user_id=user_id, secret=self._encrypt(secret), enabled=False)
            self._session.add(row)
        else:
            row.secret = self._encrypt(secret)
            row.enabled = False
        await self._session.commit()
        uri = pyotp.totp.TOTP(secret).provisioning_uri(
            name=login, issuer_name=self._settings.totp_issuer
        )
        return secret, uri

    async def get_pending_provisioning_uri(self, user_id: int, login: str) -> str | None:
        """Return the ``otpauth://`` URI for a not-yet-confirmed enrollment, if any.

        Used by the QR endpoint: ``None`` if the user never called
        :meth:`enroll`, or already confirmed it (``row.enabled`` is True —
        the secret is no longer "pending", re-enroll to get a fresh QR).
        """
        row = await self._get_row(user_id)
        if row is None or row.enabled:
            return None
        secret = self._decrypt(row.secret)
        if secret is None:
            return None
        return pyotp.totp.TOTP(secret).provisioning_uri(
            name=login, issuer_name=self._settings.totp_issuer
        )

    async def _verify_code(self, row: TiqoraUserTotp, code: str) -> bool:
        secret = self._decrypt(row.secret)
        if secret is None:
            return False
        totp = pyotp.TOTP(secret)
        return bool(totp.verify(code, valid_window=_TOTP_VALID_WINDOW))

    async def confirm(self, user_id: int, code: str) -> bool:
        row = await self._get_row(user_id)
        if row is None:
            return False
        if not await self._verify_code(row, code):
            return False
        row.enabled = True
        await self._session.commit()
        return True

    async def verify(self, user_id: int, code: str) -> bool:
        row = await self._get_row(user_id)
        if row is None or not row.enabled:
            return False
        return await self._verify_code(row, code)

    async def disable(self, user_id: int, code: str) -> bool:
        row = await self._get_row(user_id)
        if row is None or not row.enabled:
            return False
        if not await self._verify_code(row, code):
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True
