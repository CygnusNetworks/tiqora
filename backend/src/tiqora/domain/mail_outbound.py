"""Outbound SMTP settings: DB store (admin) with env fallback.

The admin UI writes a single row in ``tiqora_mail_outbound``. Agent email
replies (and the connection test) load that row first when ``enabled``; when
missing or disabled they fall back to ``Settings.smtp_*`` /
``TIQORA_SMTP_ENABLED``. Passwords are Fernet-encrypted at rest via
:mod:`tiqora.crypto.secret`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings, get_settings
from tiqora.crypto.secret import decrypt_secret, encrypt_secret
from tiqora.db.tiqora.models import TiqoraMailOutbound

SINGLETON_ID = 1

MailSecurity = Literal["none", "starttls", "ssl"]
MailAuthType = Literal["none", "password"]
MailConfigSource = Literal["db", "env", "none"]


@dataclass(frozen=True, slots=True)
class ResolvedOutboundSmtp:
    """Runtime SMTP config with decrypted password (memory only)."""

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


def _normalize_security(value: str | None) -> MailSecurity:
    v = (value or "none").strip().lower()
    if v in ("starttls", "tls"):
        return "starttls"
    if v in ("ssl", "smtps"):
        return "ssl"
    return "none"


def _normalize_auth_type(value: str | None) -> MailAuthType:
    v = (value or "none").strip().lower()
    return "password" if v == "password" else "none"


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
        if auth_type == "none":
            password = ""
            user = ""
        else:
            user = row.auth_user or ""
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
    if from_default is not None:
        row.from_default = from_default
    if timeout_seconds is not None:
        row.timeout_seconds = timeout_seconds

    row.change_time = datetime.now(UTC).replace(tzinfo=None)
    row.change_by = change_by
    await session.commit()
    await session.refresh(row)
    return row


__all__ = [
    "SINGLETON_ID",
    "MailAuthType",
    "MailConfigSource",
    "MailSecurity",
    "ResolvedOutboundSmtp",
    "get_mail_outbound_row",
    "resolve_outbound_smtp",
    "row_to_public_dict",
    "upsert_mail_outbound",
]
