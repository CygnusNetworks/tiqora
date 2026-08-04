"""Outbound SMTP settings: DB store (admin) with env fallback.

The admin UI writes a single row in ``tiqora_mail_outbound``. Agent email
replies (and the connection test) load that row first when ``enabled``; when
missing or disabled they fall back to ``Settings.smtp_*`` /
``TIQORA_SMTP_ENABLED``. Passwords are Fernet-encrypted at rest via
:mod:`tiqora.crypto.secret`.

OAuth2 outbound (``auth_type=oauth2_token``) references a legacy
``oauth2_token_config`` by **name** — same as Znuny
``SendmailModule::OAuth2TokenConfigName`` — so tokens stay in the shared
``oauth2_token`` table.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings, get_settings
from tiqora.crypto.secret import decrypt_secret, encrypt_secret
from tiqora.db.tiqora.models import TiqoraMailOutbound

SINGLETON_ID = 1

MailSecurity = Literal["none", "starttls", "ssl"]
MailAuthType = Literal["none", "password", "oauth2_token"]
MailConfigSource = Literal["db", "env", "none"]

OAuthTokenGenerator = Callable[[], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class ResolvedOutboundSmtp:
    """Runtime SMTP config with decrypted password / OAuth handle (memory only)."""

    enabled: bool
    host: str
    port: int
    security: MailSecurity
    auth_type: MailAuthType
    auth_user: str
    auth_password: str
    from_default: str
    timeout_seconds: int
    source: MailConfigSource
    oauth2_token_config_name: str = ""
    oauth2_token_config_id: int | None = None


def _normalize_security(value: str | None) -> MailSecurity:
    v = (value or "none").strip().lower()
    if v in ("starttls", "tls"):
        return "starttls"
    if v in ("ssl", "smtps"):
        return "ssl"
    return "none"


def _normalize_auth_type(value: str | None) -> MailAuthType:
    v = (value or "none").strip().lower()
    if v == "password":
        return "password"
    if v in ("oauth2_token", "oauth2", "xoauth2"):
        return "oauth2_token"
    return "none"


async def get_mail_outbound_row(session: AsyncSession) -> TiqoraMailOutbound | None:
    return await session.get(TiqoraMailOutbound, SINGLETON_ID)


async def resolve_outbound_smtp(
    session: AsyncSession,
    settings: Settings | None = None,
) -> ResolvedOutboundSmtp:
    """DB-enabled config wins; otherwise env ``TIQORA_SMTP_*``; else disabled."""
    cfg = settings or get_settings()
    row = await get_mail_outbound_row(session)
    if row is not None and row.enabled:
        password = ""
        if row.auth_password:
            password = decrypt_secret(cfg.secret_key, row.auth_password) or ""
        auth_type = _normalize_auth_type(row.auth_type)
        oauth_name = getattr(row, "oauth2_token_config_name", None) or ""
        oauth_id: int | None = None
        if auth_type == "none":
            password = ""
            user = ""
            oauth_name = ""
        elif auth_type == "oauth2_token":
            password = ""
            user = row.auth_user or ""
            if oauth_name:
                from tiqora.domain.oauth2_mail import get_config_by_name

                cfg_row = await get_config_by_name(session, oauth_name)
                if cfg_row is not None:
                    oauth_id = cfg_row.id
        else:
            user = row.auth_user or ""
            oauth_name = ""
        return ResolvedOutboundSmtp(
            enabled=True,
            host=row.host or "localhost",
            port=int(row.port or 25),
            security=_normalize_security(row.security),
            auth_type=auth_type,
            auth_user=user,
            auth_password=password,
            from_default=row.from_default or "",
            timeout_seconds=int(row.timeout_seconds or 60),
            source="db",
            oauth2_token_config_name=oauth_name,
            oauth2_token_config_id=oauth_id,
        )
    if cfg.smtp_enabled:
        return ResolvedOutboundSmtp(
            enabled=True,
            host=cfg.smtp_host or "localhost",
            port=int(cfg.smtp_port or 25),
            security="starttls" if cfg.smtp_use_tls else "none",
            auth_type="password" if (cfg.smtp_user or cfg.smtp_password) else "none",
            auth_user=cfg.smtp_user or "",
            auth_password=cfg.smtp_password or "",
            from_default="",
            timeout_seconds=60,
            source="env",
        )
    return ResolvedOutboundSmtp(
        enabled=False,
        host=cfg.smtp_host or "localhost",
        port=int(cfg.smtp_port or 25),
        security="starttls" if cfg.smtp_use_tls else "none",
        auth_type="none",
        auth_user="",
        auth_password="",
        from_default="",
        timeout_seconds=60,
        source="none",
    )


def row_to_public_dict(row: TiqoraMailOutbound | None) -> dict[str, object]:
    """Serialize the store for GET — never includes the decrypted password."""
    if row is None:
        return {
            "enabled": False,
            "host": "",
            "port": 25,
            "security": "none",
            "auth_type": "none",
            "auth_user": "",
            "has_password": False,
            "oauth2_token_config_name": "",
            "from_default": "",
            "timeout_seconds": 60,
            "change_time": None,
            "change_by": None,
        }
    return {
        "enabled": bool(row.enabled),
        "host": row.host or "",
        "port": int(row.port or 25),
        "security": _normalize_security(row.security),
        "auth_type": _normalize_auth_type(row.auth_type),
        "auth_user": row.auth_user or "",
        "has_password": bool(row.auth_password),
        "oauth2_token_config_name": getattr(row, "oauth2_token_config_name", None) or "",
        "from_default": row.from_default or "",
        "timeout_seconds": int(row.timeout_seconds or 60),
        "change_time": row.change_time,
        "change_by": row.change_by,
    }


async def upsert_mail_outbound(
    session: AsyncSession,
    *,
    settings: Settings,
    change_by: int,
    enabled: bool | None = None,
    host: str | None = None,
    port: int | None = None,
    security: str | None = None,
    auth_type: str | None = None,
    auth_user: str | None = None,
    auth_password: str | None = None,
    oauth2_token_config_name: str | None = None,
    from_default: str | None = None,
    timeout_seconds: int | None = None,
) -> TiqoraMailOutbound:
    """Create or update the singleton row. Password only written when non-empty."""
    row = await get_mail_outbound_row(session)
    if row is None:
        row = TiqoraMailOutbound(
            id=SINGLETON_ID,
            enabled=False,
            host="",
            port=25,
            security="none",
            auth_type="none",
            auth_user="",
            auth_password="",
            oauth2_token_config_name="",
            from_default="",
            timeout_seconds=60,
        )
        session.add(row)

    if enabled is not None:
        row.enabled = enabled
    if host is not None:
        row.host = host
    if port is not None:
        row.port = port
    if security is not None:
        row.security = _normalize_security(security)
    if auth_type is not None:
        row.auth_type = _normalize_auth_type(auth_type)
    if auth_user is not None:
        row.auth_user = auth_user
    if auth_password is not None and auth_password != "":
        row.auth_password = encrypt_secret(settings.secret_key, auth_password)
    if oauth2_token_config_name is not None:
        row.oauth2_token_config_name = oauth2_token_config_name
    if from_default is not None:
        row.from_default = from_default
    if timeout_seconds is not None:
        row.timeout_seconds = timeout_seconds

    row.change_time = datetime.now(UTC).replace(tzinfo=None)
    row.change_by = change_by
    await session.commit()
    await session.refresh(row)
    return row


def make_oauth_token_generator(
    session: AsyncSession, config_id: int
) -> OAuthTokenGenerator:
    """Build an aiosmtplib ``oauth_token_generator`` for *config_id*."""

    async def _gen() -> str:
        from tiqora.domain.oauth2_mail import get_access_token

        return await get_access_token(session, config_id=config_id, user_id=1)

    return _gen


__all__ = [
    "SINGLETON_ID",
    "MailAuthType",
    "MailConfigSource",
    "MailSecurity",
    "OAuthTokenGenerator",
    "ResolvedOutboundSmtp",
    "get_mail_outbound_row",
    "make_oauth_token_generator",
    "resolve_outbound_smtp",
    "row_to_public_dict",
    "upsert_mail_outbound",
]
