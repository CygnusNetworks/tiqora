"""Agent chat availability: online markers, per-agent state, business hours, topic availability.

Who counts as "can take this chat" (``eligible_agent_ids``): an agent row with ``available``
set, whose topic filter admits the topic, who has an online marker in Redis (written by the
agent SSE stream, ``mark_agent_online``), who holds ``rw`` on the topic's queue, and who is
below ``max_concurrent`` active chats. The visitor-facing ``config_availability`` is that
answer per topic, cached for ``AVAILABILITY_CACHE_SECONDS`` and forced to "all offline"
outside the business hours of the config's calendar.

No SQL JOIN between ``tiqora_*`` and Znuny tables: agent rows, session counts and queue
permissions are separate statements; the permission engine reads the Znuny tables.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import structlog
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraChatAgentState, TiqoraChatConfig, TiqoraChatSession
from tiqora.permissions.engine import PermissionEngine
from tiqora.webchat.bot import bot_availability
from tiqora.webchat.models import ChatConfigValues, ChatTopic
from tiqora.znuny.escalation import calendar_config, working_seconds_between
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

AGENT_ONLINE_PREFIX: Final = "tiqora:agent_online:"
AGENT_ONLINE_TTL_SECONDS: Final = 60
AVAILABILITY_CACHE_SECONDS: Final = 10
AVAILABILITY_CACHE_PREFIX: Final = "tiqora:chat:avail:"
VISITOR_SEEN_PREFIX: Final = "tiqora:chat:seen:"  # TTL 90 s, refreshed by the visitor stream
VISITOR_TYPING_PREFIX: Final = "tiqora:chat:vtyping:"  # TTL 6 s
DEFAULT_MAX_CONCURRENT: Final = 3
MAX_CONCURRENT_LIMIT: Final = 20


@dataclass(frozen=True, slots=True)
class AgentChatState:
    user_id: int
    available: bool
    topics: tuple[str, ...]
    max_concurrent: int


def topic_ref(config_id: int, topic_key: str) -> str:
    return f"{config_id}:{topic_key}"


# -- online markers -------------------------------------------------------------------------


async def mark_agent_online(client: Any, user_id: int) -> None:
    """Refresh the agent's online marker (TTL 60 s). Best-effort: never raises."""
    try:
        await client.set(f"{AGENT_ONLINE_PREFIX}{user_id}", "1", ex=AGENT_ONLINE_TTL_SECONDS)
    except Exception:  # noqa: BLE001 — a marker write must never break the agent event stream
        logger.warning("chat_agent_online_mark_failed", user_id=user_id)


async def _online_ids(client: Any, user_ids: Sequence[int]) -> set[int]:
    """Subset of *user_ids* with a marker (one ``MGET`` round-trip). Redis error -> none online."""
    if not user_ids:
        return set()
    try:
        values = await client.mget(*[f"{AGENT_ONLINE_PREFIX}{uid}" for uid in user_ids])
    except Exception:  # noqa: BLE001 — unknown presence counts as offline (fail closed)
        logger.warning("chat_agent_online_lookup_failed")
        return set()
    return {uid for uid, value in zip(user_ids, values, strict=True) if value is not None}


# -- agent state ----------------------------------------------------------------------------


def _parse_topics(raw: str | None) -> tuple[str, ...]:
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        return ()
    if not isinstance(data, list):
        return ()
    return tuple(str(item) for item in data)


def _state_from_row(row: TiqoraChatAgentState) -> AgentChatState:
    return AgentChatState(
        user_id=int(row.user_id),
        available=bool(row.available),
        topics=_parse_topics(row.topics),
        max_concurrent=int(row.max_concurrent),
    )


async def get_agent_state(db: AsyncSession, user_id: int) -> AgentChatState:
    """The agent's chat state; defaults (not available, no topic filter, 3) without a row."""
    row = (
        await db.execute(
            select(TiqoraChatAgentState).where(TiqoraChatAgentState.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return AgentChatState(user_id, False, (), DEFAULT_MAX_CONCURRENT)
    return _state_from_row(row)


async def ensure_agent_state_row(db: AsyncSession, user_id: int, *, now: datetime) -> None:
    """Insert the default row in a SAVEPOINT; a concurrent insert (IntegrityError) is ignored."""
    exists = (
        await db.execute(
            select(TiqoraChatAgentState.user_id).where(TiqoraChatAgentState.user_id == user_id)
        )
    ).first()
    if exists is not None:
        return
    try:
        async with db.begin_nested():
            await db.execute(
                insert(TiqoraChatAgentState).values(
                    user_id=user_id,
                    available=False,
                    topics="[]",
                    max_concurrent=DEFAULT_MAX_CONCURRENT,
                    changed=now,
                )
            )
    except IntegrityError:
        pass


class AgentStateMissing(RuntimeError):
    """``lock_agent_state`` found no row: create it first in its own committed transaction."""


async def lock_agent_state(db: AsyncSession, user_id: int) -> AgentChatState:
    """Lock step 1 (R-LOCK-ORDER): the agent's state row ``FOR UPDATE``.

    A locking read: it returns the newest committed row and, on MariaDB, does not take the
    transaction's snapshot (see ``webchat.agent``). The row must already exist —
    ``ensure_agent_state_row`` + commit BEFORE the transaction that locks it (the accept
    endpoint does; R-REPLY-ACCEPT-FIRST). Creating it here would need a locking read of a
    missing row, which takes a gap lock on MariaDB: two first-time agents then deadlock on
    their INSERTs (1213). So a missing row raises ``AgentStateMissing`` instead.
    """
    row = (
        await db.execute(
            select(TiqoraChatAgentState)
            .where(TiqoraChatAgentState.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        raise AgentStateMissing(
            f"chat agent state row of user {user_id} missing — call ensure_agent_state_row"
            " and commit before accept_session"
        )
    return _state_from_row(row)


async def set_agent_state(
    db: AsyncSession,
    user_id: int,
    *,
    available: bool,
    topics: Sequence[str],
    max_concurrent: int,
    now: datetime,
) -> AgentChatState:
    """Upsert the agent's state (caller commits). ``max_concurrent`` must be 1..20.

    Lock order (R-LOCK-ORDER): the agent-state row is the first lock a chat write takes; the
    UPDATE below locks it until the caller's commit.
    """
    if not 1 <= max_concurrent <= MAX_CONCURRENT_LIMIT:
        raise ValueError(f"max_concurrent must be between 1 and {MAX_CONCURRENT_LIMIT}")
    unique_topics = list(dict.fromkeys(str(t) for t in topics))
    await ensure_agent_state_row(db, user_id, now=now)
    await db.execute(
        update(TiqoraChatAgentState)
        .where(TiqoraChatAgentState.user_id == user_id)
        .values(
            available=available,
            topics=json.dumps(unique_topics),
            max_concurrent=max_concurrent,
            changed=now,
        )
    )
    return AgentChatState(user_id, available, tuple(unique_topics), max_concurrent)


async def active_count(db: AsyncSession, user_id: int) -> int:
    """Chats the agent currently holds (``active`` sessions assigned to them)."""
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(TiqoraChatSession)
                .where(
                    TiqoraChatSession.assigned_user_id == user_id,
                    TiqoraChatSession.status == "active",
                )
            )
        ).scalar_one()
    )


async def _active_counts(db: AsyncSession, user_ids: Sequence[int]) -> Counter[int]:
    if not user_ids:
        return Counter()
    rows = await db.execute(
        select(TiqoraChatSession.assigned_user_id, func.count())
        .where(
            TiqoraChatSession.assigned_user_id.in_(list(user_ids)),
            TiqoraChatSession.status == "active",
        )
        .group_by(TiqoraChatSession.assigned_user_id)
    )
    return Counter({int(uid): int(n) for uid, n in rows.all() if uid is not None})


# -- business hours -------------------------------------------------------------------------


async def within_business_hours(sysconfig: SysConfig, calendar: str | None, now: datetime) -> bool:
    """Whether the chat is open at *now* (naive UTC).

    ``None`` means no calendar: always open. ``""`` selects the default Znuny calendar. A
    calendar without any working hours counts as always open (deviation 16: Znuny would
    compute 0 working minutes, which for a chat would silently hide it). Otherwise the minute
    starting at *now* must contain working time, using the same Znuny calendar logic as SLAs
    (``calendar_config`` / ``working_seconds_between``, honouring vacation days and the
    calendar time zone).
    """
    if calendar is None:
        return True
    hours, vacation, vacation_once, tz_name = await calendar_config(sysconfig, calendar or None)
    if not any(hours.values()):
        return True
    epoch = int(now.replace(tzinfo=UTC).timestamp())
    return working_seconds_between(epoch, epoch + 60, hours, vacation, vacation_once, tz_name) > 0


# -- eligibility ----------------------------------------------------------------------------


async def eligible_agent_ids(
    db: AsyncSession, client: Any, *, config_id: int, topic: ChatTopic
) -> list[int]:
    """Agents who can take a chat on *topic* now, ascending by id."""
    rows = (
        (
            await db.execute(
                select(TiqoraChatAgentState).where(TiqoraChatAgentState.available.is_(True))
            )
        )
        .scalars()
        .all()
    )
    ref = topic_ref(config_id, topic.key)
    states = [s for s in map(_state_from_row, rows) if not s.topics or ref in s.topics]
    if not states:
        return []
    ids = sorted(s.user_id for s in states)
    online = await _online_ids(client, ids)
    candidates = [uid for uid in ids if uid in online]
    engine = PermissionEngine(db)
    permitted = [uid for uid in candidates if await engine.check(uid, topic.queue_id, "rw")]
    counts = await _active_counts(db, permitted)
    limits = {s.user_id: s.max_concurrent for s in states}
    return [uid for uid in permitted if counts[uid] < limits[uid]]


async def config_availability(
    db: AsyncSession,
    client: Any,
    sysconfig: SysConfig,
    *,
    config: TiqoraChatConfig,
    values: ChatConfigValues,
    now: datetime,
    settings: Settings,
) -> dict[str, bool]:
    """``{topic.key: online}``; all False outside business hours.

    With ``offline_mode == "ai"`` a ``first_line`` topic whose bot is available stays online
    without a human, also outside business hours (every other topic behaves like ``form``).

    Cached as JSON in Redis for ``AVAILABILITY_CACHE_SECONDS``; any Redis error computes
    uncached.
    """
    key = f"{AVAILABILITY_CACHE_PREFIX}{config.id}"
    try:
        cached = await client.get(key)
        if cached is not None:
            data = json.loads(cached)
            if isinstance(data, dict):
                return {str(k): bool(v) for k, v in data.items()}
    except Exception:  # noqa: BLE001 — cache trouble must not hide the chat
        logger.warning("chat_availability_cache_read_failed", config_id=config.id)

    open_now = await within_business_hours(sysconfig, values.business_calendar, now)
    result: dict[str, bool] = {}
    for topic in values.topics:
        humans = open_now and bool(
            await eligible_agent_ids(db, client, config_id=int(config.id), topic=topic)
        )
        if not humans and values.offline_mode == "ai" and topic.ai_mode == "first_line":
            bot = await bot_availability(
                db, client, settings, config_id=int(config.id), topic=topic, queue_id=topic.queue_id
            )
            humans = bot.ok
        result[topic.key] = humans

    try:
        await client.set(key, json.dumps(result), ex=AVAILABILITY_CACHE_SECONDS)
    except Exception:  # noqa: BLE001
        logger.warning("chat_availability_cache_write_failed", config_id=config.id)
    return result
