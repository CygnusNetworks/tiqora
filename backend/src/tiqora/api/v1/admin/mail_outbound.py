"""Admin API for outbound SMTP settings — ``/api/v1/admin/mail/outbound``.

GET returns settings without the decrypted password (``has_password`` only).
PUT upserts the singleton row; password is updated only when a non-empty value
is supplied. POST ``/test`` attempts SMTP connect + AUTH and optionally sends
a test message to an admin-provided address.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import aiosmtplib
import structlog
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.channels.email.smtp import build_message
from tiqora.config import get_settings
from tiqora.domain.mail_outbound import (
    get_mail_outbound_row,
    resolve_outbound_smtp,
    row_to_public_dict,
    upsert_mail_outbound,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/mail", tags=["admin:mail"])

MailSecurity = Literal["none", "starttls", "ssl"]
MailAuthType = Literal["none", "password"]


class MailOutboundOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool
    host: str
    port: int
    security: MailSecurity
    auth_type: MailAuthType
    auth_user: str
    has_password: bool
    from_default: str
    timeout_seconds: int
    change_time: datetime | None = None
    change_by: int | None = None


class MailOutboundUpdate(BaseModel):
    enabled: bool | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    security: MailSecurity | None = None
    auth_type: MailAuthType | None = None
    auth_user: str | None = None
    # Write-only: omit or empty string keeps the stored password.
    auth_password: str | None = None
    from_default: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)


class MailOutboundTestIn(BaseModel):
    """Optional recipient; when set a short test message is sent after AUTH."""

    to_address: str | None = None


class MailOutboundTestOut(BaseModel):
    ok: bool
    message: str
    detail: str | None = None


@router.get("/outbound", response_model=MailOutboundOut)
async def get_mail_outbound(admin: AdminUser, session: DbSession) -> MailOutboundOut:
    _ = admin
    row = await get_mail_outbound_row(session)
    return MailOutboundOut.model_validate(row_to_public_dict(row))


@router.put("/outbound", response_model=MailOutboundOut)
async def put_mail_outbound(
    body: MailOutboundUpdate, admin: AdminUser, session: DbSession
) -> MailOutboundOut:
    settings = get_settings()
    row = await upsert_mail_outbound(
        session,
        settings=settings,
        change_by=admin.id,
        enabled=body.enabled,
        host=body.host,
        port=body.port,
        security=body.security,
        auth_type=body.auth_type,
        auth_user=body.auth_user,
        auth_password=body.auth_password,
        from_default=body.from_default,
        timeout_seconds=body.timeout_seconds,
    )
    return MailOutboundOut.model_validate(row_to_public_dict(row))


@router.post("/outbound/test", response_model=MailOutboundTestOut)
async def test_mail_outbound(
    body: MailOutboundTestIn, admin: AdminUser, session: DbSession
) -> MailOutboundTestOut:
    """Connect + AUTH with the effective config; optionally send a test mail."""
    _ = admin
    resolved = await resolve_outbound_smtp(session)
    if not resolved.enabled:
        return MailOutboundTestOut(
            ok=False,
            message="Outbound mail is not enabled",
            detail="Enable DB settings or set TIQORA_SMTP_ENABLED=1",
        )
    if not resolved.host:
        return MailOutboundTestOut(
            ok=False,
            message="SMTP host is empty",
            detail=None,
        )

    use_tls = resolved.security == "ssl"
    start_tls = True if resolved.security == "starttls" else False if use_tls else None
    username = resolved.auth_user if resolved.auth_type == "password" else None
    password = resolved.auth_password if resolved.auth_type == "password" else None

    try:
        if body.to_address and body.to_address.strip():
            from_addr = resolved.from_default.strip() or "Tiqora <noreply@localhost>"
            message = build_message(
                from_addr=from_addr,
                to_addrs=body.to_address.strip(),
                cc_addrs=None,
                subject="Tiqora SMTP test",
                body="This is a Tiqora outbound SMTP connection test.",
                content_type="text/plain; charset=utf-8",
                in_reply_to=None,
                loop_hint=True,
            )
            await aiosmtplib.send(
                message,
                hostname=resolved.host,
                port=resolved.port,
                username=username or None,
                password=password or None,
                timeout=float(resolved.timeout_seconds),
                use_tls=use_tls,
                start_tls=start_tls,
            )
            return MailOutboundTestOut(
                ok=True,
                message="SMTP connection, authentication, and test send succeeded",
                detail=f"source={resolved.source} host={resolved.host}:{resolved.port}",
            )

        smtp = aiosmtplib.SMTP(
            hostname=resolved.host,
            port=resolved.port,
            timeout=float(resolved.timeout_seconds),
            use_tls=use_tls,
            start_tls=start_tls,
        )
        async with smtp:
            if username:
                await smtp.login(username, password or "")
        return MailOutboundTestOut(
            ok=True,
            message="SMTP connection and authentication succeeded",
            detail=f"source={resolved.source} host={resolved.host}:{resolved.port}",
        )
    except Exception as exc:
        # Never log credentials; aiosmtplib error text is safe for operators.
        logger.warning(
            "mail_outbound_test_failed",
            host=resolved.host,
            port=resolved.port,
            security=resolved.security,
            error=str(exc),
        )
        return MailOutboundTestOut(
            ok=False,
            message="SMTP test failed",
            detail=str(exc),
        )
