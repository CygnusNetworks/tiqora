"""WebAuthn passkeys as an alternative 2nd factor to TOTP.

Challenges are stored in Redis keyed by the session token (pending or full/
enroll) and are single-use. Clients never supply the challenge. Passkeys are
only enabled when ``settings.webauthn_rp_id`` and ``settings.webauthn_origin``
are both set.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis
import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraUserPasskey
from tiqora.domain.totp import TOTPService

logger = structlog.get_logger(__name__)

_CHALLENGE_PREFIX_REG = "tiqora:webauthn:reg:"
_CHALLENGE_PREFIX_AUTH = "tiqora:webauthn:auth:"
_DEFAULT_CHALLENGE_TTL = 300
_DEFAULT_PASSKEY_NAME = "Passkey"


def webauthn_enabled(settings: Settings) -> bool:
    """True only when both RP id and origin are configured."""
    return bool(settings.webauthn_rp_id.strip() and settings.webauthn_origin.strip())


async def two_factor_enabled(
    totp: TOTPService,
    passkeys: WebAuthnService,
    user_id: int,
) -> bool:
    """Unified 2FA: TOTP enrolled **or** at least one passkey registered."""
    return await totp.is_enabled(user_id) or await passkeys.has_passkey(user_id)


class WebAuthnService:
    def __init__(
        self,
        session: AsyncSession,
        redis_client: redis.Redis,
        settings: Settings,
    ) -> None:
        self._session = session
        self._redis = redis_client
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return webauthn_enabled(self._settings)

    def _challenge_ttl(self) -> int:
        return self._settings.totp_pending_ttl_seconds or _DEFAULT_CHALLENGE_TTL

    async def _store_challenge(self, prefix: str, session_token: str, challenge: bytes) -> None:
        key = f"{prefix}{session_token}"
        await self._redis.set(
            key,
            bytes_to_base64url(challenge),
            ex=self._challenge_ttl(),
        )

    async def _pop_challenge(self, prefix: str, session_token: str) -> bytes | None:
        """Fetch and delete the challenge (single-use)."""
        key = f"{prefix}{session_token}"
        raw = await self._redis.get(key)
        if raw is None:
            return None
        await self._redis.delete(key)
        if isinstance(raw, bytes):
            raw = raw.decode("ascii")
        return base64url_to_bytes(raw)

    async def list(self, user_id: int) -> list[TiqoraUserPasskey]:
        result = await self._session.execute(
            select(TiqoraUserPasskey)
            .where(TiqoraUserPasskey.user_id == user_id)
            .order_by(TiqoraUserPasskey.created.desc())
        )
        return list(result.scalars().all())

    async def count(self, user_id: int) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(TiqoraUserPasskey)
            .where(TiqoraUserPasskey.user_id == user_id)
        )
        return int(result.scalar_one() or 0)

    async def has_passkey(self, user_id: int) -> bool:
        return (await self.count(user_id)) > 0

    async def get_by_id(self, user_id: int, passkey_id: int) -> TiqoraUserPasskey | None:
        result = await self._session.execute(
            select(TiqoraUserPasskey).where(
                TiqoraUserPasskey.id == passkey_id,
                TiqoraUserPasskey.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_credential_id(self, credential_id_b64: str) -> TiqoraUserPasskey | None:
        result = await self._session.execute(
            select(TiqoraUserPasskey).where(TiqoraUserPasskey.credential_id == credential_id_b64)
        )
        return result.scalar_one_or_none()

    async def delete(self, user_id: int, passkey_id: int) -> bool:
        row = await self.get_by_id(user_id, passkey_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True

    async def delete_all_for_user(self, user_id: int) -> int:
        """Admin force-reset: remove every passkey for the user. Returns count."""
        count = await self.count(user_id)
        if count:
            await self._session.execute(
                delete(TiqoraUserPasskey).where(TiqoraUserPasskey.user_id == user_id)
            )
            await self._session.commit()
        return count

    async def begin_registration(
        self,
        *,
        user_id: int,
        login: str,
        session_token: str,
    ) -> dict[str, Any]:
        """Generate registration options; store challenge under ``session_token``."""
        existing = await self.list(user_id)
        exclude = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(row.credential_id))
            for row in existing
        ]
        challenge = secrets.token_bytes(32)
        options = generate_registration_options(
            rp_id=self._settings.webauthn_rp_id,
            rp_name=self._settings.webauthn_rp_name or "Tiqora",
            user_name=login,
            user_id=user_id.to_bytes(8, "big"),
            user_display_name=login,
            challenge=challenge,
            exclude_credentials=exclude or None,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
        )
        await self._store_challenge(_CHALLENGE_PREFIX_REG, session_token, challenge)
        parsed: dict[str, Any] = json.loads(options_to_json(options))
        return parsed

    async def finish_registration(
        self,
        *,
        user_id: int,
        session_token: str,
        credential: dict[str, Any] | str,
        name: str | None = None,
    ) -> TiqoraUserPasskey | None:
        """Verify attestation against the Redis challenge and store the credential."""
        expected = await self._pop_challenge(_CHALLENGE_PREFIX_REG, session_token)
        if expected is None:
            return None
        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=expected,
                expected_rp_id=self._settings.webauthn_rp_id,
                expected_origin=self._settings.webauthn_origin,
            )
        except WebAuthnException as exc:
            logger.info("webauthn_registration_failed", error=str(exc), user_id=user_id)
            return None

        cred_id_b64 = bytes_to_base64url(verified.credential_id)
        if await self.get_by_credential_id(cred_id_b64) is not None:
            logger.warning("webauthn_duplicate_credential", credential_id=cred_id_b64)
            return None

        transports_raw = None
        if isinstance(credential, dict):
            response = credential.get("response") or {}
            transports = response.get("transports")
            if transports:
                transports_raw = json.dumps(transports)

        label = (name or "").strip() or _DEFAULT_PASSKEY_NAME
        if len(label) > 120:
            label = label[:120]

        row = TiqoraUserPasskey(
            user_id=user_id,
            credential_id=cred_id_b64,
            public_key=verified.credential_public_key,
            sign_count=int(verified.sign_count),
            transports=transports_raw,
            aaguid=verified.aaguid or None,
            name=label,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def begin_authentication(
        self,
        *,
        user_id: int,
        session_token: str,
    ) -> dict[str, Any] | None:
        """Generate authentication options for the user's registered credentials."""
        rows = await self.list(user_id)
        if not rows:
            return None
        allow = [
            PublicKeyCredentialDescriptor(
                id=base64url_to_bytes(row.credential_id),
                transports=_parse_transports(row.transports),
            )
            for row in rows
        ]
        challenge = secrets.token_bytes(32)
        options = generate_authentication_options(
            rp_id=self._settings.webauthn_rp_id,
            challenge=challenge,
            allow_credentials=allow,
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        await self._store_challenge(_CHALLENGE_PREFIX_AUTH, session_token, challenge)
        parsed: dict[str, Any] = json.loads(options_to_json(options))
        return parsed

    async def finish_authentication(
        self,
        *,
        user_id: int,
        session_token: str,
        credential: dict[str, Any] | str,
    ) -> TiqoraUserPasskey | None:
        """Verify assertion, bump sign_count + last_used_at. Returns the row on success."""
        expected = await self._pop_challenge(_CHALLENGE_PREFIX_AUTH, session_token)
        if expected is None:
            return None

        cred_id_b64 = _credential_id_from_payload(credential)
        if cred_id_b64 is None:
            return None
        row = await self.get_by_credential_id(cred_id_b64)
        if row is None or row.user_id != user_id:
            return None

        try:
            verified = verify_authentication_response(
                credential=credential,
                expected_challenge=expected,
                expected_rp_id=self._settings.webauthn_rp_id,
                expected_origin=self._settings.webauthn_origin,
                credential_public_key=row.public_key,
                credential_current_sign_count=int(row.sign_count),
            )
        except WebAuthnException as exc:
            logger.info(
                "webauthn_authentication_failed",
                error=str(exc),
                user_id=user_id,
                passkey_id=row.id,
            )
            return None

        row.sign_count = int(verified.new_sign_count)
        row.last_used_at = datetime.now(UTC).replace(tzinfo=None)
        await self._session.commit()
        await self._session.refresh(row)
        return row


def _parse_transports(raw: str | None) -> list[AuthenticatorTransport] | None:
    if not raw:
        return None
    try:
        values = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(values, list):
        return None
    out: list[AuthenticatorTransport] = []
    for item in values:
        if not isinstance(item, str):
            continue
        try:
            out.append(AuthenticatorTransport(item))
        except ValueError:
            continue
    return out or None


def _credential_id_from_payload(credential: dict[str, Any] | str) -> str | None:
    if isinstance(credential, str):
        try:
            credential = json.loads(credential)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if not isinstance(credential, dict):
        return None
    raw_id = credential.get("rawId") or credential.get("id")
    if not isinstance(raw_id, str) or not raw_id:
        return None
    # Browser sends base64url in both id and rawId for JSON serialization.
    return raw_id
