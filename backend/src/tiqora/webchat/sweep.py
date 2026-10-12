"""Housekeeping sweep of the web chat: waiting timeouts, idle chats, retention.

One tick runs four bounded steps (``SWEEP_BATCH`` rows each). Every row is handled in its
own short transaction: lock the session row by primary key (R-LOCK-ORDER), re-check the
state (a visitor or an agent may have changed it since the batch select), write, commit,
then publish the queued wake / agent events. The sweep writes ``tiqora_*`` tables only —
except the ticket state of a bot-solved chat, through the Znuny write service — and loads
keys first (no JOIN with Znuny tables).

* ``timed_out``: ``waiting`` longer than the config's ``wait_timeout_seconds`` ->
  ``ended`` / ``no_agent`` + system line ``no_agent`` ``{"has_email": bool}``.
* ``abandoned``: the visitor is gone (no ``tiqora:chat:seen:<sid>`` marker AND a stale
  ``visitor_last_seen``; R-LASTSEEN: a brief marker gap with a fresh ``visitor_last_seen``
  keeps the chat) -> ``abandoned`` / ``idle`` + system line ``idle_closed``.
  A chat the AI still answers (``bot``) goes with the ``active`` cutoff and, when the AI had
  answered it alone, parks its ticket like a visitor's goodbye (``close_bot_resolved_ticket``).
* ``handed_off``: the AI worker is gone (no ``BOT_ALIVE_KEY``: stopped, or
  ``daemon.ai_worker.enabled`` switched off) and a ``bot`` chat has a visitor line older than
  ``ORPHAN_BOT_SECONDS`` without an answer -> handed to the team (``unavailable``), or ended
  ``no_agent`` when nobody is there. A chat is never left waiting for an assistant that
  cannot answer (Review Focus 2).
* ``purged``: ``ended_at`` older than the config's ``retention_days`` -> messages deleted,
  visitor data (name, email, page, user agent, IP hash, visitor id, token hash) cleared,
  ``purged_at`` set (the row stays as a tombstone for the ticket link).

Sessions of a disabled or deleted config are swept like any other (R-DISABLED-SESSIONS):
a deleted config falls back to the default values.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Final

import structlog
from sqlalchemy import delete, distinct, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.tiqora.models import TiqoraChatMessage, TiqoraChatSession
from tiqora.jobs.service import utc_now
from tiqora.webchat.availability import VISITOR_SEEN_PREFIX
from tiqora.webchat.bot import BOT_ALIVE_KEY, close_bot_resolved_ticket, hand_off_chat
from tiqora.webchat.models import ChatConfigValues
from tiqora.webchat.notify import flush_chat_notifications, queue_agent_event
from tiqora.webchat.sessions import _agent_event, add_system_message, current_queue_id
from tiqora.webchat.store import config_values, get_chat_session, load_config
from tiqora.znuny.sysconfig import SysConfig

__all__ = [
    "ACTIVE_IDLE_SECONDS",
    "ORPHAN_BOT_SECONDS",
    "SWEEP_BATCH",
    "WAITING_IDLE_SECONDS",
    "run_webchat_sweep_tick",
]

logger = structlog.get_logger(__name__)

#: A waiting visitor must show a sign of life within this window (marker or last seen).
WAITING_IDLE_SECONDS: Final = 120
#: An active chat may be quiet this long without the visitor's stream.
ACTIVE_IDLE_SECONDS: Final = 900
#: A visitor line of a ``bot`` chat may wait this long for a stopped AI worker.
ORPHAN_BOT_SECONDS: Final = 60
#: Rows handled per step and tick.
SWEEP_BATCH: Final = 200

_FINAL_STATUSES: Final = ("ended", "abandoned")


class _Configs:
    """Config values, loaded once per config id and tick."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory
        self._values: dict[int, ChatConfigValues] = {}

    async def get(self, config_id: int) -> ChatConfigValues:
        cached = self._values.get(config_id)
        if cached is not None:
            return cached
        async with self._factory() as db:
            row = await load_config(db, config_id)
        values = config_values(row) if row is not None else _default_values()
        self._values[config_id] = values
        return values


def _default_values() -> ChatConfigValues:
    return ChatConfigValues.model_construct(
        **{
            name: field.get_default(call_default_factory=True)
            for name, field in ChatConfigValues.model_fields.items()
            if name != "topics"
        },
        topics=[],
    )


async def _marker_present(redis_client: Any, session_id: str) -> bool:
    try:
        return bool(await redis_client.exists(f"{VISITOR_SEEN_PREFIX}{session_id}"))
    except Exception:  # noqa: BLE001 - without Redis only visitor_last_seen decides
        return False


async def _in_transaction(
    factory: async_sessionmaker[AsyncSession],
    redis_client: Any,
    session_id: str,
    work: Callable[[AsyncSession, TiqoraChatSession], Awaitable[bool]],
) -> bool:
    """Lock the session row, let *work* re-check and write; commit and publish when it did."""
    async with factory() as db:
        try:
            locked = await get_chat_session(db, session_id, for_update=True)
            if locked is None or locked.purged_at is not None or not await work(db, locked):
                await db.rollback()
                return False
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("webchat_sweep_row_failed", session_id=session_id)
            return False
        await flush_chat_notifications(db, redis_client)
        return True


async def _ids(db: AsyncSession, *conditions: Any, limit: int) -> list[str]:
    stmt = (
        select(TiqoraChatSession.id)
        .where(*conditions)
        .order_by(TiqoraChatSession.created)
        .limit(limit)
    )
    return [str(i) for i in (await db.execute(stmt)).scalars()]


async def _end_event(db: AsyncSession, chat: TiqoraChatSession) -> None:
    queue_id = await current_queue_id(db, chat)
    queue_agent_event(db, _agent_event(chat, "chat_ended", queue_id))


async def _time_out_waiting(
    factory: async_sessionmaker[AsyncSession],
    redis_client: Any,
    configs: _Configs,
    now: datetime,
) -> int:
    async with factory() as db:
        config_ids = [
            int(i)
            for i in (
                await db.execute(
                    select(distinct(TiqoraChatSession.config_id)).where(
                        TiqoraChatSession.status == "waiting",
                        TiqoraChatSession.waiting_since.is_not(None),
                    )
                )
            ).scalars()
        ]
    done = 0
    for config_id in config_ids:
        timeout = int((await configs.get(config_id)).wait_timeout_seconds)
        cutoff = now - timedelta(seconds=timeout)
        async with factory() as db:
            ids = await _ids(
                db,
                TiqoraChatSession.config_id == config_id,
                TiqoraChatSession.status == "waiting",
                TiqoraChatSession.waiting_since <= cutoff,
                limit=SWEEP_BATCH - done,
            )

        async def work(
            db: AsyncSession, chat: TiqoraChatSession, cutoff: datetime = cutoff
        ) -> bool:
            if (
                chat.status != "waiting"
                or chat.waiting_since is None
                or chat.waiting_since > cutoff
            ):
                return False
            chat.status = "ended"
            chat.end_reason = "no_agent"
            chat.ended_at = now
            await db.flush([chat])
            await add_system_message(
                db,
                chat=chat,
                code="no_agent",
                params={"has_email": bool(chat.visitor_email)},
                now=now,
            )
            await _end_event(db, chat)
            return True

        for session_id in ids:
            if await _in_transaction(factory, redis_client, session_id, work):
                done += 1
        if done >= SWEEP_BATCH:
            break
    return done


def _idle_cutoff(status: str, now: datetime) -> datetime:
    seconds = WAITING_IDLE_SECONDS if status == "waiting" else ACTIVE_IDLE_SECONDS
    return now - timedelta(seconds=seconds)


async def _abandon_idle(
    factory: async_sessionmaker[AsyncSession], redis_client: Any, now: datetime
) -> int:
    async with factory() as db:
        candidates = (
            await db.execute(
                select(TiqoraChatSession.id)
                .where(
                    (
                        (TiqoraChatSession.status == "waiting")
                        & (TiqoraChatSession.visitor_last_seen < _idle_cutoff("waiting", now))
                    )
                    | (
                        TiqoraChatSession.status.in_(("active", "bot"))
                        & (TiqoraChatSession.visitor_last_seen < _idle_cutoff("active", now))
                    )
                )
                .order_by(TiqoraChatSession.visitor_last_seen)
                .limit(SWEEP_BATCH)
            )
        ).scalars()
        ids = [str(i) for i in candidates]

    async def work(db: AsyncSession, chat: TiqoraChatSession) -> bool:
        if chat.status not in ("waiting", "active", "bot"):
            return False
        if chat.visitor_last_seen >= _idle_cutoff(str(chat.status), now):
            return False
        was_bot = chat.status == "bot"
        chat.status = "abandoned"
        chat.end_reason = "idle"
        chat.ended_at = now
        await db.flush([chat])
        if was_bot:
            # A chat the AI answered alone and the visitor left: park the ticket like a goodbye.
            await close_bot_resolved_ticket(db, SysConfig(db), chat=chat)
        await add_system_message(db, chat=chat, code="idle_closed", params={}, now=now)
        await _end_event(db, chat)
        return True

    done = 0
    for session_id in ids:
        if await _marker_present(redis_client, session_id):
            continue
        if await _in_transaction(factory, redis_client, session_id, work):
            done += 1
    return done


async def _hand_off_orphaned_bot_chats(
    factory: async_sessionmaker[AsyncSession], redis_client: Any, now: datetime
) -> int:
    """Hand ``bot`` chats to the team while no AI worker answers (see the module doc)."""
    try:
        if await redis_client.exists(BOT_ALIVE_KEY):
            return 0
    except Exception:  # noqa: BLE001 - unknown worker state: leave the chats alone this tick
        logger.warning("webchat_sweep_bot_alive_check_failed")
        return 0
    cutoff = now - timedelta(seconds=ORPHAN_BOT_SECONDS)
    pending_old = exists(
        select(TiqoraChatMessage.id).where(
            TiqoraChatMessage.session_id == TiqoraChatSession.id,
            TiqoraChatMessage.sender == "visitor",
            TiqoraChatMessage.seq > TiqoraChatSession.bot_answered_seq,
            TiqoraChatMessage.created <= cutoff,
        )
    )
    async with factory() as db:
        ids = await _ids(
            db,
            TiqoraChatSession.status == "bot",
            TiqoraChatSession.last_visitor_seq > TiqoraChatSession.bot_answered_seq,
            pending_old,
            limit=SWEEP_BATCH,
        )
    done = 0
    for session_id in ids:
        async with factory() as db:
            try:
                # locks the row and re-checks ``bot`` itself (``noop`` when an agent was faster)
                result = await hand_off_chat(
                    db,
                    SysConfig(db),
                    redis_client,
                    session_id=session_id,
                    reason="unavailable",
                    note=None,
                    now=now,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                logger.exception("webchat_sweep_row_failed", session_id=session_id)
                continue
            await flush_chat_notifications(db, redis_client)
        if result != "noop":
            done += 1
    return done


async def _purge_expired(
    factory: async_sessionmaker[AsyncSession],
    redis_client: Any,
    configs: _Configs,
    now: datetime,
) -> int:
    final_open = (
        TiqoraChatSession.status.in_(_FINAL_STATUSES),
        TiqoraChatSession.purged_at.is_(None),
        TiqoraChatSession.ended_at.is_not(None),
    )
    async with factory() as db:
        config_ids = [
            int(i)
            for i in (
                await db.execute(select(distinct(TiqoraChatSession.config_id)).where(*final_open))
            ).scalars()
        ]
    done = 0
    for config_id in config_ids:
        days = int((await configs.get(config_id)).retention_days)
        cutoff = now - timedelta(days=days)
        async with factory() as db:
            ids = await _ids(
                db,
                TiqoraChatSession.config_id == config_id,
                *final_open,
                TiqoraChatSession.ended_at <= cutoff,
                limit=SWEEP_BATCH - done,
            )

        async def work(
            db: AsyncSession, chat: TiqoraChatSession, cutoff: datetime = cutoff
        ) -> bool:
            if (
                chat.status not in _FINAL_STATUSES
                or chat.ended_at is None
                or chat.ended_at > cutoff
            ):
                return False
            await db.execute(
                delete(TiqoraChatMessage)
                .where(TiqoraChatMessage.session_id == chat.id)
                .execution_options(synchronize_session=False)
            )
            await db.execute(
                update(TiqoraChatSession)
                .where(TiqoraChatSession.id == chat.id)
                .values(
                    visitor_name=None,
                    visitor_email=None,
                    page_url=None,
                    user_agent=None,
                    ip_hash=None,
                    visitor_id="",
                    token_hash="",
                    purged_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            return True

        for session_id in ids:
            if await _in_transaction(factory, redis_client, session_id, work):
                done += 1
        if done >= SWEEP_BATCH:
            break
    return done


async def run_webchat_sweep_tick(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    redis_client: Any,
    now: datetime | None = None,
) -> dict[str, int]:
    """One sweep tick; ``{"timed_out": n, "abandoned": n, "handed_off": n, "purged": n}``."""
    moment = now if now is not None else utc_now()
    configs = _Configs(session_factory)
    timed_out = await _time_out_waiting(session_factory, redis_client, configs, moment)
    abandoned = await _abandon_idle(session_factory, redis_client, moment)
    handed_off = await _hand_off_orphaned_bot_chats(session_factory, redis_client, moment)
    purged = await _purge_expired(session_factory, redis_client, configs, moment)
    if timed_out or abandoned or handed_off or purged:
        logger.info(
            "webchat_sweep",
            timed_out=timed_out,
            abandoned=abandoned,
            handed_off=handed_off,
            purged=purged,
        )
    return {
        "timed_out": timed_out,
        "abandoned": abandoned,
        "handed_off": handed_off,
        "purged": purged,
    }
