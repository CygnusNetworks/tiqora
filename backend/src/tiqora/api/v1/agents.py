"""Global online-agent presence endpoints.

Complements the *per-ticket* presence in :mod:`tiqora.api.v1.events`
(``tiqora:presence:<ticket>:<user>``) with a system-wide "who is online
right now" view backed by Redis keys ``tiqora:online:<user_id>`` (short
TTL, refreshed from the auth path and via ``POST /agents/presence/ping``).

Design notes
------------
* Heartbeat is best-effort and non-fatal: a Redis outage must never break
  an authenticated request (see :func:`touch_online_presence`).
* ``GET /agents/online`` SCANs the keyspace (not ``KEYS``), resolves the
  surviving user ids against the ``users`` table (``valid_id=1``), and
  returns a defensive cap of entries. Redis payload may carry an optional
  ``avatar_url``; login / full name come from the DB.
* No pub/sub signal: the agent shell polls every ~20–30s. That is simpler
  than an ``agents_online_changed`` SSE hook and sufficient for a soft
  presence list.
"""

from __future__ import annotations

import json
from typing import Annotated

import redis.asyncio as redis
import structlog
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select

from tiqora.api.deps import CurrentUser, DbSession, get_redis
from tiqora.db.legacy.user import Users
from tiqora.domain.auth import AuthenticatedUser

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/agents", tags=["agents"])

ONLINE_TTL_SECONDS = 60
ONLINE_KEY_PREFIX = "tiqora:online:"
ONLINE_LIST_CAP = 100

# Znuny's "valid" list id — 1 == valid.
_VALID = 1

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


def online_key(user_id: int) -> str:
    return f"{ONLINE_KEY_PREFIX}{user_id}"


async def touch_online_presence(redis_client: redis.Redis, user: AuthenticatedUser) -> None:
    """Refresh ``tiqora:online:<user_id>`` with a short TTL. Never raises."""
    try:
        name = f"{user.first_name} {user.last_name}".strip() or user.login
        payload = json.dumps(
            {
                "login": user.login,
                "full_name": name,
                "avatar_url": user.avatar_url,
            }
        )
        await redis_client.set(online_key(user.id), payload, ex=ONLINE_TTL_SECONDS)
    except Exception:  # noqa: BLE001 — presence must never break auth
        logger.warning("online_presence_touch_failed", user_id=user.id)


class OnlineAgentOut(BaseModel):
    id: int
    login: str
    full_name: str
    avatar_url: str | None = None


async def _scan_online_user_ids(redis_client: redis.Redis) -> list[tuple[int, str | None]]:
    """Return ``(user_id, avatar_url?)`` for live online keys (capped).

    Avatar is read from the Redis payload when present; login/name are
    resolved from the users table by the caller.
    """
    found: list[tuple[int, str | None]] = []
    pattern = f"{ONLINE_KEY_PREFIX}*"
    async for key in redis_client.scan_iter(match=pattern):
        if len(found) >= ONLINE_LIST_CAP:
            break
        key_s = key.decode("utf-8") if isinstance(key, bytes) else str(key)
        suffix = key_s[len(ONLINE_KEY_PREFIX) :]
        try:
            user_id = int(suffix)
        except (TypeError, ValueError):
            continue
        avatar_url: str | None = None
        raw = await redis_client.get(key)
        if raw is not None:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    av = data.get("avatar_url")
                    if isinstance(av, str) and av.strip():
                        avatar_url = av.strip()
            except (ValueError, TypeError):
                pass
        found.append((user_id, avatar_url))
    return found


@router.post("/presence/ping", status_code=status.HTTP_204_NO_CONTENT)
async def ping_presence(
    user: CurrentUser,
    redis_client: RedisDep,
) -> None:
    """Lightweight liveness heartbeat for idle-but-open agent sessions."""
    await touch_online_presence(redis_client, user)


@router.get("/online", response_model=list[OnlineAgentOut])
async def list_online_agents(
    user: CurrentUser,
    redis_client: RedisDep,
    session: DbSession,
) -> list[OnlineAgentOut]:
    """Currently-online agents (Redis TTL presence, resolved via users table)."""
    del user  # auth gate only; include the caller in the list
    try:
        online = await _scan_online_user_ids(redis_client)
    except Exception:  # noqa: BLE001 — Redis down → empty list, never 500
        logger.warning("online_presence_scan_failed")
        return []

    if not online:
        return []

    user_ids = [uid for uid, _ in online]
    avatars = {uid: av for uid, av in online if av}
    rows = (
        (
            await session.execute(
                select(Users).where(Users.id.in_(user_ids), Users.valid_id == _VALID)
            )
        )
        .scalars()
        .all()
    )
    by_id = {u.id: u for u in rows}

    out: list[OnlineAgentOut] = []
    for uid in user_ids:
        row = by_id.get(uid)
        if row is None:
            continue
        full_name = f"{row.first_name} {row.last_name}".strip() or row.login
        out.append(
            OnlineAgentOut(
                id=row.id,
                login=row.login,
                full_name=full_name,
                avatar_url=avatars.get(uid),
            )
        )
    # Stable order: login ascending for the popover.
    out.sort(key=lambda a: a.login.lower())
    return out
