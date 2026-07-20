"""Phone/CTI note channel HTTP surface, mounted at ``/api/v1/channels/phone``.

Intended for CTI integrations (Asterisk AMI/AGI hangup hooks, a generic
click-to-log button) — shared-secret auth like the SMS/WhatsApp inbound
webhooks, since these callers are usually unattended integrations rather
than logged-in agents. Disabled by default — see ``channel.phone.enabled``.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from tiqora.api.deps import DbSession
from tiqora.channels.common import channel_enabled, channel_setting, verify_shared_secret
from tiqora.channels.phone.service import CHANNEL_NAME, log_phone_call
from tiqora.db.engine import get_session_factory
from tiqora.znuny.sysconfig import SysConfig

router = APIRouter(prefix="/channels/phone", tags=["channels:phone"])


class PhoneNoteRequest(BaseModel):
    direction: str  # "inbound" | "outbound"
    caller_number: str
    note: str
    ticket_id: int | None = None
    subject: str | None = None
    agent_user_id: int | None = None


class PhoneNoteResponse(BaseModel):
    ticket_id: int
    article_id: int
    created: bool


async def _require_enabled(session: DbSession) -> None:
    if not await channel_enabled(session, CHANNEL_NAME):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Phone channel disabled")


@router.post("/note", response_model=PhoneNoteResponse)
async def log_note(
    body: PhoneNoteRequest,
    session: DbSession,
    x_tiqora_phone_secret: str | None = Header(default=None),
) -> PhoneNoteResponse:
    await _require_enabled(session)
    expected = await channel_setting(session, CHANNEL_NAME, "inbound_shared_secret")
    if not verify_shared_secret(expected, x_tiqora_phone_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing shared secret"
        )
    if body.direction not in ("inbound", "outbound"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="direction must be 'inbound' or 'outbound'",
        )

    factory = get_session_factory()
    sysconfig = SysConfig(session)
    result = await log_phone_call(
        session,
        factory,
        sysconfig,
        direction=body.direction,
        caller_number=body.caller_number,
        note=body.note,
        ticket_id=body.ticket_id,
        user_id=body.agent_user_id or 1,
        subject=body.subject,
    )
    await session.commit()
    return PhoneNoteResponse(
        ticket_id=result.ticket_id, article_id=result.article_id, created=result.created
    )
