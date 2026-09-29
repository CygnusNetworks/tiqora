"""Phone/CTI note channel HTTP surface, mounted at ``/api/v1/channels/phone``.

Intended for CTI integrations (Asterisk AMI/AGI hangup hooks, a generic
click-to-log button) — shared-secret auth like the SMS/WhatsApp inbound
webhooks, since these callers are usually unattended integrations rather
than logged-in agents. Disabled by default — see ``channel.phone.enabled``.
"""

from __future__ import annotations

import json
from typing import Annotated

import redis.asyncio as redis
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ValidationError

from tiqora.api.deps import DbSession, get_redis
from tiqora.channels.common import channel_enabled, channel_setting, verify_shared_secret
from tiqora.channels.phone.cti import CallEventIn, apply_call_event, users_for_extension
from tiqora.channels.phone.service import CHANNEL_NAME, log_phone_call
from tiqora.db.engine import get_session_factory
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

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


async def _require_secret(session: DbSession, provided: str | None) -> None:
    expected = await channel_setting(session, CHANNEL_NAME, "inbound_shared_secret")
    if not verify_shared_secret(expected, provided):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing shared secret"
        )


@router.post("/note", response_model=PhoneNoteResponse)
async def log_note(
    body: PhoneNoteRequest,
    session: DbSession,
    x_tiqora_phone_secret: str | None = Header(default=None),
) -> PhoneNoteResponse:
    await _require_enabled(session)
    await _require_secret(session, x_tiqora_phone_secret)
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


class CallEventAccepted(BaseModel):
    accepted: bool
    #: Number of agents the event was pushed to (0 = unknown extension, ignored).
    delivered_to: int


_CALL_EVENT_SCHEMA = CallEventIn.model_json_schema()


async def _parse_call_event(request: Request) -> CallEventIn:
    """JSON or form-encoded body (Asterisk ``CURL()`` posts form data)."""
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    try:
        if content_type in ("application/x-www-form-urlencoded", "multipart/form-data"):
            form = await request.form()
            data: object = {k: v for k, v in form.items() if isinstance(v, str) and v != ""}
        else:
            data = json.loads(await request.body() or b"null")
        return CallEventIn.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(include_url=False, include_context=False, include_input=False),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid request body"
        ) from exc


@router.post(
    "/events",
    response_model=CallEventAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": _CALL_EVENT_SCHEMA},
                "application/x-www-form-urlencoded": {"schema": _CALL_EVENT_SCHEMA},
            },
        }
    },
)
async def call_event(
    request: Request,
    session: DbSession,
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    x_tiqora_phone_secret: str | None = Header(default=None),
) -> CallEventAccepted:
    """PBX call events (ringing / answered / hangup) for the agent call popup.

    Body as JSON or form-encoded (same fields). The extension is mapped to
    agents via their ``TiqoraPhoneExtension`` preference; an event nobody is
    assigned to is accepted and ignored. See :mod:`tiqora.channels.phone.cti`.
    """
    await _require_enabled(session)
    await _require_secret(session, x_tiqora_phone_secret)
    body = await _parse_call_event(request)

    async def _resolve(extension: str) -> list[int]:
        return await users_for_extension(session, extension)

    try:
        result = await apply_call_event(redis_client, body, resolve_users=_resolve)
    except (redis.RedisError, ConnectionError, OSError) as exc:
        logger.warning("cti.redis_unavailable", call_id=body.call_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="call state store unavailable"
        ) from exc
    return CallEventAccepted(accepted=True, delivered_to=len(result.recipients))
