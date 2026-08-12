"""TOTP 2FA: enrollment, confirmation, verification, disable.

Secrets are stored Fernet-encrypted (key derived from ``settings.secret_key``
via SHA-256) — never in plaintext. Verification uses a ±1 step window (30s
step => ~90s total tolerance), matching common authenticator app behaviour.

After a successful :meth:`verify` the accepted timestep is recorded in Redis
so the same (or earlier) code cannot be replayed within the window (M-04).

Replacing a *live* factor is a privileged act, not a convenience: :meth:`enroll`
demands a valid current code before it will hand out a new secret, and the
pending secret lives in Redis until :meth:`confirm` succeeds. Together those
mean a hijacked session can neither switch the second factor to one it controls
nor turn 2FA off by starting an enrollment it never finishes — the previous
implementation cleared ``enabled`` the moment ``enroll`` was called.
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

# Pending (unconfirmed) enrollment secrets. Held in Redis rather than on the
# tiqora_user_totp row so an abandoned re-enrollment cannot damage the factor
# that is currently live. Long enough to scan a QR code and type one code.
_TOTP_PENDING_KEY_PREFIX = "tiqora:totp:pending:"
_TOTP_PENDING_TTL_SECONDS = 600


class TOTPStepUpRequired(Exception):
    """Raised when replacing an active TOTP factor without proving possession.

    The caller must re-run :meth:`TOTPService.enroll` with a valid code from the
    authenticator that is currently enrolled.
    """


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

    def _pending_key(self, user_id: int) -> str:
        return f"{_TOTP_PENDING_KEY_PREFIX}{user_id}"

    async def _store_pending(self, user_id: int, secret: str, row: TiqoraUserTotp | None) -> None:
        """Park an unconfirmed secret until :meth:`confirm` accepts it.

        Redis is the right home: the value is short-lived and must not touch the
        live row. Without Redis (unit tests) fall back to the row — safe there
        because an *active* factor is only ever replaced after the step-up check
        in :meth:`enroll` has already passed.
        """
        if self._redis is not None:
            await self._redis.set(
                self._pending_key(user_id),
                self._encrypt(secret),
                ex=_TOTP_PENDING_TTL_SECONDS,
            )
            return
        if row is None:
            self._session.add(
                TiqoraUserTotp(user_id=user_id, secret=self._encrypt(secret), enabled=False)
            )
        else:
            row.secret = self._encrypt(secret)
            row.enabled = False
        await self._session.commit()

    async def _get_pending_secret(self, user_id: int) -> str | None:
        """Plaintext secret of an unconfirmed enrollment, or ``None``."""
        if self._redis is not None:
            raw = await self._redis.get(self._pending_key(user_id))
            if raw is not None:
                token = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                return self._decrypt(token)
        row = await self._get_row(user_id)
        if row is None or row.enabled:
            return None
        return self._decrypt(row.secret)

    async def _clear_pending(self, user_id: int) -> None:
        if self._redis is not None:
            await self._redis.delete(self._pending_key(user_id))

    async def _step_up_ok(
        self, row: TiqoraUserTotp, user_id: int, current_code: str | None
    ) -> bool:
        """Whether *current_code* proves possession of the live factor."""
        if not current_code:
            return False
        return await self._verify_code(row, current_code, consume_replay=True, user_id=user_id)

    async def enroll(
        self, user_id: int, login: str, *, current_code: str | None = None
    ) -> tuple[str, str]:
        """Create (or replace) a pending enrollment. Returns (secret, otpauth_uri).

        When a factor is already enabled, *current_code* must be a valid code
        from it — otherwise :class:`TOTPStepUpRequired` is raised and nothing
        changes. This is what stops a stolen session from swapping or silently
        disabling the second factor.
        """
        row = await self._get_row(user_id)
        if (
            row is not None
            and row.enabled
            and not await self._step_up_ok(row, user_id, current_code)
        ):
            raise TOTPStepUpRequired(
                "A valid code from the current authenticator is required to re-enroll"
            )
        secret = pyotp.random_base32()
        await self._store_pending(user_id, secret, row)
        uri = pyotp.totp.TOTP(secret).provisioning_uri(
            name=login, issuer_name=self._settings.totp_issuer
        )
        return secret, uri

    async def get_pending_provisioning_uri(self, user_id: int, login: str) -> str | None:
        """Return the ``otpauth://`` URI for a not-yet-confirmed enrollment, if any.

        Used by the QR endpoint: ``None`` if the user never called
        :meth:`enroll`, or the pending enrollment already expired / was
        confirmed (re-enroll to get a fresh QR).
        """
        secret = await self._get_pending_secret(user_id)
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
        """Promote a pending enrollment to the live factor.

        No replay-consume here: the pending secret has never authenticated a
        login, and the pending entry is dropped on success, so each secret can
        be confirmed exactly once anyway.
        """
        secret = await self._get_pending_secret(user_id)
        if secret is None or self._match_timestep(secret, code) is None:
            return False
        row = await self._get_row(user_id)
        if row is None:
            row = TiqoraUserTotp(user_id=user_id, secret=self._encrypt(secret), enabled=True)
            self._session.add(row)
        else:
            row.secret = self._encrypt(secret)
            row.enabled = True
        await self._session.commit()
        await self._clear_pending(user_id)
        return True

    async def verify(self, user_id: int, code: str) -> bool:
        """Verify a login TOTP code with replay protection for used timesteps."""
        row = await self._get_row(user_id)
        if row is None or not row.enabled:
            return False
        return await self._verify_code(row, code, consume_replay=True, user_id=user_id)

    async def disable(self, user_id: int, code: str) -> bool:
        """Self-service removal of the factor — consumes the timestep.

        Replay-consume matters here (security review L-4): without it a code
        observed on a login could still be spent to turn 2FA off inside the
        same ~90s window.
        """
        row = await self._get_row(user_id)
        if row is None or not row.enabled:
            return False
        if not await self._verify_code(row, code, consume_replay=True, user_id=user_id):
            return False
        await self._session.delete(row)
        await self._session.commit()
        await self._clear_pending(user_id)
        return True

    async def force_disable(self, user_id: int) -> bool:
        """Admin force-reset: delete the TOTP row without requiring a code.

        Idempotent — returns ``True`` when a row was removed, ``False`` when
        none existed. Distinct from :meth:`disable` (self-service, code-gated).
        """
        row = await self._get_row(user_id)
        await self._clear_pending(user_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True
