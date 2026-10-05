"""Agent side of the CTI call popup, mounted at ``/api/v1/phone``.

``GET /phone/calls/active`` restores the popup cards after a reload;
``POST /phone/calls/{call_id}/dismiss`` hides a card in all of the agent's
tabs. The PBX webhook feeding the state is ``POST /channels/phone/events``
(:mod:`tiqora.api.v1.channels_phone`); the logic is in
:mod:`tiqora.channels.phone.cti`.
"""

from __future__ import annotations

from typing import Annotated

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from tiqora.api.deps import CurrentUser, DbSession, get_redis
from tiqora.channels.phone.cti import (
    ActiveCall,
    dismiss_call,
    extensions_for_user,
    list_active_calls,
)
from tiqora.channels.phone.originate import (
    OriginateError,
    load_originate_config,
    normalize_dial_number,
    originate,
)

router = APIRouter(prefix="/phone", tags=["phone"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]

DIAL_RATE_SECONDS = 5


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="call state store unavailable"
    )


@router.get("/calls/active", response_model=list[ActiveCall])
async def active_calls(user: CurrentUser, redis_client: RedisDep) -> list[ActiveCall]:
    """The agent's ringing/answered calls and those ended ≤ 15 min ago."""
    try:
        return await list_active_calls(redis_client, user.id)
    except (redis.RedisError, ConnectionError, OSError) as exc:
        raise _unavailable(exc) from exc


@router.post("/calls/{call_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss(call_id: str, user: CurrentUser, redis_client: RedisDep) -> None:
    try:
        found = await dismiss_call(redis_client, call_id, user.id)
    except (redis.RedisError, ConnectionError, OSError) as exc:
        raise _unavailable(exc) from exc
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found")


class DialRequest(BaseModel):
    number: str = Field(..., min_length=1, max_length=64)
    ticket_id: int | None = None
    #: shown on the desk phone while it rings (customer name)
    name: str = Field("", max_length=100)


class DialOut(BaseModel):
    extension: str
    number: str


@router.post("/dial", response_model=DialOut, status_code=status.HTTP_202_ACCEPTED)
async def dial(
    body: DialRequest, user: CurrentUser, session: DbSession, redis_client: RedisDep
) -> DialOut:
    """Click-to-dial: ring the agent's desk phone, then dial ``number``.

    Session only: an API key (whatever its scopes) must not ring desk phones
    and dial out over the trunk."""
    if user.auth_method == "api_key":
        raise HTTPException(status_code=403, detail="click-to-dial needs an agent session")
    config = await load_originate_config(session)
    if config is None:
        raise HTTPException(status_code=404, detail="click-to-dial is not configured")
    extensions = await extensions_for_user(session, user.id)
    if not extensions:
        raise HTTPException(status_code=409, detail="no phone extension set for this agent")
    try:
        number = normalize_dial_number(body.number, config.internal)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        fresh = await redis_client.set(f"tiqora:dial:{user.id}", "1", ex=DIAL_RATE_SECONDS, nx=True)
    except (redis.RedisError, ConnectionError, OSError) as exc:
        raise _unavailable(exc) from exc
    if not fresh:
        raise HTTPException(status_code=429, detail="dial request already running")
    try:
        await originate(config, extensions[0], number, body.name)
    except OriginateError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return DialOut(extension=extensions[0], number=number)
