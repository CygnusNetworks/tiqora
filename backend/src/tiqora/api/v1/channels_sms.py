"""SMS channel HTTP surface: inbound webhook (shared-secret auth) + agent-triggered
outbound send (session/API-key auth). Mounted at ``/api/v1/channels/sms``.

Disabled by default — see ``channel.sms.enabled`` in :mod:`tiqora.channels.common`
and ``docs/channels.md``.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.channels.common import channel_enabled
from tiqora.channels.sms.service import (
    CHANNEL_NAME,
    SmsInboundAuthError,
    build_gateway,
    process_inbound_sms,
    send_outbound_sms,
    verify_inbound_secret,
)
from tiqora.db.engine import get_session_factory
from tiqora.znuny.sysconfig import SysConfig

router = APIRouter(prefix="/channels/sms", tags=["channels:sms"])


class SmsInboundRequest(BaseModel):
    from_number: str
    to_number: str | None = None
    body: str


class SmsInboundResponse(BaseModel):
    ticket_id: int
    article_id: int
    created: bool


class SmsSendRequest(BaseModel):
    ticket_id: int
    to_number: str
    body: str


class SmsSendResponse(BaseModel):
    article_id: int


async def _require_enabled(session: DbSession) -> None:
    if not await channel_enabled(session, CHANNEL_NAME):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SMS channel disabled")


@router.post("/inbound", response_model=SmsInboundResponse)
async def inbound_sms(
    body: SmsInboundRequest,
    session: DbSession,
    x_tiqora_sms_secret: str | None = Header(default=None),
) -> SmsInboundResponse:
    """Receive one inbound SMS from the configured gateway driver."""
    await _require_enabled(session)
    try:
        await verify_inbound_secret(session, x_tiqora_sms_secret)
    except SmsInboundAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    factory = get_session_factory()
    sysconfig = SysConfig(session)
    result = await process_inbound_sms(
        session,
        factory,
        sysconfig,
        from_number=body.from_number,
        to_number=body.to_number,
        body=body.body,
        user_id=1,  # system/postmaster-equivalent actor, matches email pipeline convention
    )
    await session.commit()
    return SmsInboundResponse(
        ticket_id=result.ticket_id, article_id=result.article_id, created=result.created
    )


@router.post("/send", response_model=SmsSendResponse)
async def send_sms(body: SmsSendRequest, user: CurrentUser, session: DbSession) -> SmsSendResponse:
    """Agent-triggered outbound SMS: appends an article and delivers via the
    configured gateway (``channel.sms.outbound_webhook_url``)."""
    await _require_enabled(session)
    gateway = await build_gateway(session)
    if gateway is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="SMS outbound gateway not configured"
        )
    sysconfig = SysConfig(session)
    article_id = await send_outbound_sms(
        session,
        sysconfig,
        gateway,
        ticket_id=body.ticket_id,
        to_number=body.to_number,
        body=body.body,
        user_id=user.id,
    )
    await session.commit()
    return SmsSendResponse(article_id=article_id)
