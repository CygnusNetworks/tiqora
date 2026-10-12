"""SCIM provisioning tokens: creation, listing, revocation and tenant authentication.

Tokens are random bearer secrets shown once; only their sha256 hex is stored. The
authentication result is an :class:`AuthOutcome` (not a bare tenant) so the caller can write a
precise audit row (provider/token id, reason) while answering every failure identically.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraScimEvent, TiqoraScimToken
from tiqora.identity.scim.constants import (
    LAST_USED_WRITE_INTERVAL,
    MAX_ACTIVE_TOKENS,
    TOKEN_MAX_DAYS,
    TOKEN_PREFIX,
)
from tiqora.identity.store import ProviderRecord, get_provider
from tiqora.identity.types import SCIM_KINDS, UserKind, audience_includes, utc_now

logger = structlog.get_logger(__name__)

_MAX_RAW_LENGTH = 128
_MAX_NAME_LENGTH = 120


@dataclass(frozen=True, slots=True)
class ScimTokenRecord:
    id: int
    provider_id: int
    user_kind: UserKind
    name: str
    token_hint: str
    created_by: int
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None

    @property
    def active(self) -> bool:
        return self.revoked_at is None and self.expires_at > utc_now()


@dataclass(frozen=True, slots=True)
class ScimTenant:
    provider: ProviderRecord
    user_kind: UserKind
    token_id: int


@dataclass(frozen=True, slots=True)
class AuthOutcome:
    """Result of :func:`authenticate_token`; ``tenant`` is set only on success.

    ``token_id`` / ``provider_id`` are set whenever the hash matched a stored token (also
    when the token is revoked, expired or its provider no longer qualifies).
    """

    tenant: ScimTenant | None
    token_id: int | None
    provider_id: int | None
    reason: str


class ScimTokenError(ValueError):
    """Admin-side validation failure; ``errors`` maps a field name to a German message."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def _record(row: TiqoraScimToken) -> ScimTokenRecord:
    kind: UserKind = "customer" if row.user_kind == "customer" else "agent"
    return ScimTokenRecord(
        id=int(row.id),
        provider_id=int(row.provider_id),
        user_kind=kind,
        name=row.name,
        token_hint=row.token_hint,
        created_by=int(row.created_by),
        created_at=row.created_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        last_used_at=row.last_used_at,
    )


def _admin_event(
    *, provider_id: int, token_id: int, operation: str, actor_id: int, user_kind: str
) -> TiqoraScimEvent:
    # Task 5 replaces this direct row with ``audit.record_event_in``.
    return TiqoraScimEvent(
        provider_id=provider_id,
        token_id=token_id,
        created=utc_now(),
        method="ADMIN",
        resource="Token",
        operation=operation,
        user_kind=user_kind,
        status=200,
        actor_user_id=actor_id,
    )


async def _active_count(session: AsyncSession, provider_id: int) -> int:
    stmt = select(func.count(TiqoraScimToken.id)).where(
        TiqoraScimToken.provider_id == provider_id,
        TiqoraScimToken.revoked_at.is_(None),
        TiqoraScimToken.expires_at > utc_now(),
    )
    return int((await session.execute(stmt)).scalar_one())


async def create_token(
    session: AsyncSession,
    *,
    provider: ProviderRecord,
    user_kind: UserKind,
    name: str,
    expires_in_days: int,
    actor_id: int,
) -> tuple[ScimTokenRecord, str]:
    """Create a token and return ``(record, raw)``; the raw value is never retrievable again."""
    errors: dict[str, str] = {}
    if provider.source != "db" or provider.kind not in SCIM_KINDS:
        errors["provider"] = (
            "Bereitstellung ist nur für OIDC- und SAML-Anmeldedienste aus der Verwaltung möglich."
        )
    if user_kind not in ("agent", "customer") or not audience_includes(
        provider.audience, user_kind
    ):
        errors["user_kind"] = "Die Kontoart passt nicht zur Zielgruppe des Anmeldedienstes."
    if not 1 <= expires_in_days <= TOKEN_MAX_DAYS:
        errors["expires_in_days"] = (
            f"Die Gültigkeit muss zwischen 1 und {TOKEN_MAX_DAYS} Tagen liegen."
        )
    clean_name = (name or "").strip()
    if not 1 <= len(clean_name) <= _MAX_NAME_LENGTH:
        errors["name"] = f"Der Name muss 1 bis {_MAX_NAME_LENGTH} Zeichen lang sein."
    if not errors and await _active_count(session, provider.id) >= MAX_ACTIVE_TOKENS:
        errors["tokens"] = (
            f"Es sind höchstens {MAX_ACTIVE_TOKENS} aktive Zugangsschlüssel"
            " pro Anmeldedienst erlaubt."
        )
    if errors:
        raise ScimTokenError(errors)

    raw = new_token()
    now = utc_now()
    row = TiqoraScimToken(
        provider_id=provider.id,
        user_kind=user_kind,
        name=clean_name,
        token_hash=hash_token(raw),
        token_hint=raw[-4:],
        created_by=actor_id,
        created_at=now,
        expires_at=now + timedelta(days=expires_in_days),
    )
    session.add(row)
    await session.flush()
    session.add(
        _admin_event(
            provider_id=provider.id,
            token_id=int(row.id),
            operation="token_create",
            actor_id=actor_id,
            user_kind=user_kind,
        )
    )
    await session.flush()
    return _record(row), raw


async def list_tokens(session: AsyncSession, provider_id: int) -> list[ScimTokenRecord]:
    rows = (
        await session.scalars(
            select(TiqoraScimToken)
            .where(TiqoraScimToken.provider_id == provider_id)
            .order_by(TiqoraScimToken.id.desc())
        )
    ).all()
    return [_record(r) for r in rows]


async def revoke_token(
    session: AsyncSession, *, provider_id: int, token_id: int, actor_id: int
) -> ScimTokenRecord:
    """Revoke a token (idempotent: a second call keeps the first ``revoked_at``, no new event)."""
    row = await session.get(TiqoraScimToken, token_id)
    if row is None or int(row.provider_id) != provider_id:
        raise ScimTokenError({"token": "Zugangsschlüssel nicht gefunden."})
    if row.revoked_at is None:
        row.revoked_at = utc_now()
        session.add(
            _admin_event(
                provider_id=provider_id,
                token_id=int(row.id),
                operation="token_revoke",
                actor_id=actor_id,
                user_kind=row.user_kind,
            )
        )
        await session.flush()
    return _record(row)


def _fail(reason: str, row: TiqoraScimToken | None = None) -> AuthOutcome:
    return AuthOutcome(
        tenant=None,
        token_id=int(row.id) if row is not None else None,
        provider_id=int(row.provider_id) if row is not None else None,
        reason=reason,
    )


async def authenticate_token(session: AsyncSession, raw: str, *, settings: Settings) -> AuthOutcome:
    """Resolve a bearer token to its tenant, or explain (for the audit log only) why not."""
    if not raw.startswith(TOKEN_PREFIX) or len(raw) > _MAX_RAW_LENGTH or not raw.isascii():
        return _fail("malformed")  # no DB query
    digest = hash_token(raw)
    row = (
        await session.execute(select(TiqoraScimToken).where(TiqoraScimToken.token_hash == digest))
    ).scalar_one_or_none()
    if row is None:
        return _fail("unknown")
    if not hmac.compare_digest(row.token_hash, digest):
        return _fail("unknown")
    now = utc_now()
    if row.revoked_at is not None:
        return _fail("revoked", row)
    if row.expires_at <= now:
        return _fail("expired", row)
    provider = await get_provider(session, int(row.provider_id), secret_key=settings.secret_key)
    if provider is None or provider.source != "db" or provider.kind not in SCIM_KINDS:
        return _fail("provider_invalid", row)
    if not provider.enabled:
        return _fail("provider_disabled", row)
    if not provider.scim_enabled:
        return _fail("scim_disabled", row)
    kind: UserKind = "customer" if row.user_kind == "customer" else "agent"
    if not audience_includes(provider.audience, kind):
        return _fail("audience_changed", row)

    # Plain locals: rollback() below expires the ORM row, and touching it afterwards
    # would trigger a lazy refresh (MissingGreenlet on AsyncSession).
    token_id = int(row.id)
    provider_id = int(row.provider_id)
    if row.last_used_at is None or (now - row.last_used_at).total_seconds() >= (
        LAST_USED_WRITE_INTERVAL
    ):
        try:
            await session.execute(
                update(TiqoraScimToken)
                .where(TiqoraScimToken.id == token_id)
                .values(last_used_at=now)
            )
            await session.commit()
        except Exception:  # noqa: BLE001 — a bookkeeping write must not fail the request
            logger.warning("scim_last_used_write_failed", token_id=token_id)
            await session.rollback()
    return AuthOutcome(
        tenant=ScimTenant(provider=provider, user_kind=kind, token_id=token_id),
        token_id=token_id,
        provider_id=provider_id,
        reason="ok",
    )
