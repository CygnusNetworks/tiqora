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

from tiqora.api.deps import CurrentUser, get_redis
from tiqora.channels.phone.cti import ActiveCall, dismiss_call, list_active_calls

router = APIRouter(prefix="/phone", tags=["phone"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


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
