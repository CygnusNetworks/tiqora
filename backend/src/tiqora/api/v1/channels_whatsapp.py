"""WhatsApp Business (Meta Cloud API) HTTP surface, mounted at
``/api/v1/channels/whatsapp``. Disabled by default — see
``channel.whatsapp.enabled`` and docs/channels.md.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.channels.common import channel_enabled, channel_setting
from tiqora.channels.whatsapp.service import (
    CHANNEL_NAME,
    build_gateway,
    process_webhook_payload,
    send_outbound_template,
    send_outbound_text,
    verify_webhook_signature,
)
from tiqora.db.engine import get_session_factory
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/channels/whatsapp", tags=["channels:whatsapp"])


class WhatsAppWebhookResponse(BaseModel):
    processed: int


class WhatsAppSendTextRequest(BaseModel):
    ticket_id: int
    to: str
    body: str


class WhatsAppSendTemplateRequest(BaseModel):
    ticket_id: int
    to: str
    template_name: str
    language_code: str = "en_US"


class WhatsAppSendResponse(BaseModel):
    article_id: int


async def _require_enabled(session: DbSession) -> None:
    if not await channel_enabled(session, CHANNEL_NAME):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="WhatsApp channel disabled"
        )


@router.get("/webhook")
async def verify_webhook(
    session: DbSession,
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> PlainTextResponse:
    """Meta's webhook subscription handshake."""
    await _require_enabled(session)
    expected_token = await channel_setting(session, CHANNEL_NAME, "verify_token")
    if hub_mode != "subscribe" or not expected_token or hub_verify_token != expected_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verify_token mismatch")
    return PlainTextResponse(hub_challenge or "")


@router.post("/webhook", response_model=WhatsAppWebhookResponse)
async def receive_webhook(
    request: Request,
    session: DbSession,
    x_hub_signature_256: str | None = Header(default=None),
) -> WhatsAppWebhookResponse:
    """Meta message delivery webhook (HMAC-SHA256 signed with the app secret)."""
    await _require_enabled(session)
    raw_body = await request.body()
    app_secret = await channel_setting(session, CHANNEL_NAME, "app_secret")
    if not verify_webhook_signature(app_secret, raw_body, x_hub_signature_256):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature")

    payload: dict[str, Any] = await request.json()
    factory = get_session_factory()
    sysconfig = SysConfig(session)
    gateway = await build_gateway(session)
    results = await process_webhook_payload(
        session, factory, sysconfig, gateway, payload, user_id=1
    )
    await session.commit()
    return WhatsAppWebhookResponse(processed=len(results))


@router.post("/send", response_model=WhatsAppSendResponse)
async def send_text(
    body: WhatsAppSendTextRequest, user: CurrentUser, session: DbSession
) -> WhatsAppSendResponse:
    await _require_enabled(session)
    gateway = await build_gateway(session)
    if gateway is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="WhatsApp gateway not configured"
        )
    sysconfig = SysConfig(session)
    article_id = await send_outbound_text(
        session,
        sysconfig,
        gateway,
        ticket_id=body.ticket_id,
        to=body.to,
        body=body.body,
        user_id=user.id,
    )
    await session.commit()
    return WhatsAppSendResponse(article_id=article_id)


@router.post("/send-template", response_model=WhatsAppSendResponse)
async def send_template(
    body: WhatsAppSendTemplateRequest, user: CurrentUser, session: DbSession
) -> WhatsAppSendResponse:
    await _require_enabled(session)
    gateway = await build_gateway(session)
    if gateway is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="WhatsApp gateway not configured"
        )
    sysconfig = SysConfig(session)
    article_id = await send_outbound_template(
        session,
        sysconfig,
        gateway,
        ticket_id=body.ticket_id,
        to=body.to,
        template_name=body.template_name,
        language_code=body.language_code,
        user_id=user.id,
    )
    await session.commit()
    return WhatsAppSendResponse(article_id=article_id)
