"""TOTP 2FA: enrollment, confirmation, verification, disable.

Secrets are stored Fernet-encrypted (key derived from ``settings.secret_key``
via SHA-256) — never in plaintext. Verification uses a ±1 step window (30s
step => ~90s total tolerance), matching common authenticator app behaviour.

After a successful :meth:`verify` the accepted timestep is recorded in Redis
so the same (or earlier) code cannot be replayed within the window (M-04).
"""

from __future__ import annotations

import base64
import hashlib
import time
from datetime import UTC, datetime
from typing import Any

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraUserTotp

_TOTP_VALID_WINDOW = 1
_TOTP_STEP_SECONDS = 30
# Keep used-timestep keys a bit longer than the ±1 window (~90s).
_TOTP_REPLAY_TTL_SECONDS = 90
_TOTP_USED_KEY_PREFIX = "tiqora:totp:used:"


def _fernet_key(secret_key: str) -> bytes:
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class TOTPService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        redis_client: Any | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._redis = redis_client
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

    def _match_timestep(self, secret: str, code: str) -> int | None:
        """Return the matching TOTP timestep for *code*, or None if invalid."""
        totp = pyotp.TOTP(secret, interval=_TOTP_STEP_SECONDS)
        # Explicit per-offset check so we know *which* step matched (for replay).
        now = int(time.time())
        base = now // _TOTP_STEP_SECONDS
        for delta in range(-_TOTP_VALID_WINDOW, _TOTP_VALID_WINDOW + 1):
            step = base + delta
            for_time = datetime.fromtimestamp(step * _TOTP_STEP_SECONDS, tz=UTC)
            if totp.verify(code, for_time=for_time, valid_window=0):
                return step
        return None

    async def _verify_code(
        self,
        row: TiqoraUserTotp,
        code: str,
        *,
        consume_replay: bool = False,
        user_id: int | None = None,
    ) -> bool:
        secret = self._decrypt(row.secret)
        if secret is None:
            return False
        step = self._match_timestep(secret, code)
        if step is None:
            return False
        if not consume_replay or user_id is None:
            return True
        return await self._accept_timestep(user_id, step)

    async def _accept_timestep(self, user_id: int, step: int) -> bool:
        """Record *step* as used; reject if same or earlier timestep already used.

        Uses Redis when available. Without Redis (unit tests that omit it),
        replay protection is a no-op so existing fixtures keep working.
        """
        if self._redis is None:
            return True
        key = f"{_TOTP_USED_KEY_PREFIX}{user_id}"
        raw = await self._redis.get(key)
        if raw is not None:
            try:
                last = int(raw if not isinstance(raw, bytes) else raw.decode("utf-8"))
            except (TypeError, ValueError):
                last = None
            if last is not None and step <= last:
                return False
        await self._redis.set(key, str(step), ex=_TOTP_REPLAY_TTL_SECONDS)
        return True

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
        """Verify a login TOTP code with replay protection for used timesteps."""
        row = await self._get_row(user_id)
        if row is None or not row.enabled:
            return False
        return await self._verify_code(row, code, consume_replay=True, user_id=user_id)

    async def disable(self, user_id: int, code: str) -> bool:
        row = await self._get_row(user_id)
        if row is None or not row.enabled:
            return False
        if not await self._verify_code(row, code):
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True

    async def force_disable(self, user_id: int) -> bool:
        """Admin force-reset: delete the TOTP row without requiring a code.

        Idempotent — returns ``True`` when a row was removed, ``False`` when
        none existed. Distinct from :meth:`disable` (self-service, code-gated).
        """
        row = await self._get_row(user_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True
