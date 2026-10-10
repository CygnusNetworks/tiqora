"""Agent side of the web chat: topics, inbox, first-accept-wins and ending a chat (C6).

Transaction contract — same as :mod:`tiqora.webchat.sessions` (R-TX): every function runs
inside the CALLER's transaction and never calls ``db.begin()``, ``commit()`` or
``rollback()``; it only queues notifications (``notify.queue_wakeup`` /
``queue_agent_event``), which the caller publishes with ``flush_chat_notifications`` after
its commit. On a ``ChatError`` the caller ROLLS BACK: ``accept_session`` may already have
inserted the agent's default state row (and refreshed the session's queue copy) when it
refuses with ``at_capacity`` / ``already_taken``.

Lock order (R-LOCK-ORDER), the same in every webchat write:

1. the agent-state row (``accept_session`` only) — ``SELECT … FOR UPDATE``; it serialises
   every accept of one agent (other tabs, the implicit accept of an agent reply), so the
   capacity count taken under it cannot be raced by the same agent;
2. the ``tiqora_chat_session`` row — ``FOR UPDATE`` by primary key;
3. Znuny ticket writes (``lock_ticket``, ``assign_owner``, ``change_state``);
4. ``insert_message`` (``add_system_message``).

Only primary-key locks: a locking RANGE read (e.g. "the agent's active chats ``FOR
SHARE``") takes next-key/gap locks on MariaDB's REPEATABLE READ, and the accept's own
UPDATE moves the row into such a gap of the ``(assigned_user_id, status)`` index — two
concurrent accepts then deadlock (seen in ``test_first_accept_wins_both_dialects``).

Why the capacity count is still current on MariaDB: a plain SELECT there reads the snapshot
taken at the transaction's FIRST plain read. ``accept_session`` therefore starts with the
agent-state LOCKING read (which does not take a snapshot) and counts right after it, so the
snapshot is taken after every earlier accept of that agent has committed. Call it at the
start of a transaction — the router's ``get_current_user`` rolls its reads back first; a
caller that has read before must commit first (R-TX). PostgreSQL (READ COMMITTED) takes a
new snapshot per statement anyway. The session status is decided from the row read ``FOR
UPDATE`` (always the newest committed version), never from an earlier plain read.

Queue (R-QUEUE-SOT): permissions and the ``queue_id`` of agent-bus events always come from
the ticket's CURRENT queue (read by id), never from ``tiqora_chat_session.queue_id``, which
is refreshed under the session lock.

No SQL JOIN between ``tiqora_*`` and Znuny tables: sessions are loaded first, then
``SELECT … FROM ticket WHERE id IN (…)``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Final

import structlog
from sqlalchemy import bindparam, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import set_committed_value

from tiqora.db.engine import get_session_factory
from tiqora.db.tiqora.models import TiqoraChatConfig, TiqoraChatSession
from tiqora.domain.ticket_write_service import (
    TicketAccessDenied,
    TicketWriteService,
    assign_owner,
    lock_ticket,
)
from tiqora.permissions.engine import PermissionEngine
from tiqora.webchat.availability import active_count, lock_agent_state, topic_ref
from tiqora.webchat.models import ChatConfigValues
from tiqora.webchat.notify import queue_agent_event
from tiqora.webchat.schemas import ChatInboxItemOut, ChatInboxOut, ChatTopicRefOut
from tiqora.webchat.sessions import (
    LIVE_STATUSES,
    ChatError,
    add_system_message,
    current_queue_id,
)
from tiqora.webchat.store import config_values, get_chat_session
from tiqora.znuny.sysconfig import SysConfig

__all__ = [
    "CLOSED_STATE_NAME",
    "INBOX_WAITING_LIMIT",
    "RwQueues",
    "accept_session",
    "closed_state_id",
    "end_session_by_agent",
    "list_agent_topics",
    "list_inbox",
]

logger = structlog.get_logger(__name__)

#: ``ticket_state.name`` an agent's "Chat beenden + Ticket schließen" sets; when it is
#: missing, the first valid state of type ``closed``.
CLOSED_STATE_NAME: Final = "closed successful"
#: Waiting chats listed in the inbox (oldest first).
INBOX_WAITING_LIMIT: Final = 200


class RwQueues:
    """``rw`` per queue for one agent, cached for one request (``PermissionEngine.check``)."""

    def __init__(self, db: AsyncSession, user_id: int) -> None:
        self._engine = PermissionEngine(db)
        self._user_id = user_id
        self._cache: dict[int, bool] = {}

    async def allows(self, queue_id: int) -> bool:
        if queue_id not in self._cache:
            self._cache[queue_id] = await self._engine.check(self._user_id, queue_id, "rw")
        return self._cache[queue_id]


# -- small DB helpers --------------------------------------------------------------------------


async def _ticket_rows(db: AsyncSession, ticket_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    """``{id: {"tn", "title", "queue_id"}}`` of the given tickets (one IN-list read)."""
    ids = sorted({int(t) for t in ticket_ids})
    if not ids:
        return {}
    rows = await db.execute(
        text("SELECT id, tn, title, queue_id FROM ticket WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        ),
        {"ids": ids},
    )
    return {
        int(r[0]): {"tn": str(r[1]), "title": str(r[2] or ""), "queue_id": int(r[3])}
        for r in rows.all()
    }


async def _ticket_queue_id(db: AsyncSession, chat: TiqoraChatSession) -> int:
    """The ticket's current queue (plain read, no lock); the session's copy if it is gone."""
    row = (await _ticket_rows(db, [int(chat.ticket_id)])).get(int(chat.ticket_id))
    return int(row["queue_id"]) if row is not None else int(chat.queue_id)


async def _configs(db: AsyncSession, config_ids: Iterable[int]) -> dict[int, TiqoraChatConfig]:
    ids = sorted({int(c) for c in config_ids})
    if not ids:
        return {}
    rows = await db.execute(select(TiqoraChatConfig).where(TiqoraChatConfig.id.in_(ids)))
    return {int(c.id): c for c in rows.scalars().all()}


def _topic_label(values: ChatConfigValues | None, key: str | None) -> str | None:
    if values is None or key is None:
        return None
    topic = next((t for t in values.topics if t.key == key), None)
    return topic.label if topic is not None else None


async def _agent_first_name(db: AsyncSession, user_id: int) -> str | None:
    row = (
        await db.execute(text("SELECT first_name FROM users WHERE id = :u"), {"u": user_id})
    ).first()
    name = str(row[0]).strip() if row is not None and row[0] else ""
    return name or None


async def _joined_name(db: AsyncSession, chat: TiqoraChatSession, user_id: int) -> str | None:
    """The agent's first name for ``agent_joined`` — only when the config shows names."""
    cfg = (await _configs(db, [int(chat.config_id)])).get(int(chat.config_id))
    if cfg is None or not config_values(cfg).show_agent_names:
        return None
    return await _agent_first_name(db, user_id)


async def closed_state_id(db: AsyncSession) -> int | None:
    """``closed successful``, else the first valid state whose type is ``closed``."""
    row = (
        await db.execute(
            text("SELECT id FROM ticket_state WHERE name = :n"), {"n": CLOSED_STATE_NAME}
        )
    ).first()
    if row is not None:
        return int(row[0])
    # Both tables are Znuny's (no tiqora_* JOIN): the type ids first, then the states.
    type_ids = [
        int(r[0])
        for r in (
            await db.execute(text("SELECT id FROM ticket_state_type WHERE name = 'closed'"))
        ).all()
    ]
    if not type_ids:
        return None
    row = (
        await db.execute(
            text(
                "SELECT id FROM ticket_state WHERE valid_id = 1 AND type_id IN :ids ORDER BY id"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": type_ids},
        )
    ).first()
    return int(row[0]) if row is not None else None


def _refusal_for(status: str) -> ChatError:
    return ChatError("already_taken", 409) if status == "active" else ChatError("not_waiting", 409)


# -- topics and inbox ----------------------------------------------------------------------


async def list_agent_topics(
    db: AsyncSession, *, user_id: int, rw: RwQueues | None = None
) -> list[ChatTopicRefOut]:
    """Topics of the valid chat configs whose queue the agent holds ``rw`` on."""
    checker = rw or RwQueues(db, user_id)
    configs = (
        (
            await db.execute(
                select(TiqoraChatConfig)
                .where(TiqoraChatConfig.valid.is_(True))
                .order_by(TiqoraChatConfig.name, TiqoraChatConfig.id)
            )
        )
        .scalars()
        .all()
    )
    out: list[ChatTopicRefOut] = []
    for cfg in configs:
        for topic in config_values(cfg).topics:
            if await checker.allows(int(topic.queue_id)):
                out.append(
                    ChatTopicRefOut(
                        ref=topic_ref(int(cfg.id), topic.key),
                        config_name=str(cfg.name),
                        label=topic.label,
                        queue_id=int(topic.queue_id),
                    )
                )
    return out


async def list_inbox(db: AsyncSession, *, user_id: int) -> ChatInboxOut:
    """Waiting chats in queues the agent holds ``rw`` on (the ticket's CURRENT queue) and
    the agent's own active chats; ``tn``/``title`` from the ticket (IN-list read)."""
    waiting_rows = list(
        (
            await db.execute(
                select(TiqoraChatSession)
                .where(
                    TiqoraChatSession.status == "waiting",
                    TiqoraChatSession.purged_at.is_(None),
                )
                .order_by(TiqoraChatSession.waiting_since, TiqoraChatSession.created)
                .limit(INBOX_WAITING_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    mine_rows = list(
        (
            await db.execute(
                select(TiqoraChatSession)
                .where(
                    TiqoraChatSession.status == "active",
                    TiqoraChatSession.assigned_user_id == user_id,
                )
                .order_by(TiqoraChatSession.accepted_at, TiqoraChatSession.created)
            )
        )
        .scalars()
        .all()
    )
    all_rows = [*waiting_rows, *mine_rows]
    tickets = await _ticket_rows(db, (int(c.ticket_id) for c in all_rows))
    configs = await _configs(db, (int(c.config_id) for c in all_rows))
    values = {cid: config_values(cfg) for cid, cfg in configs.items()}
    rw = RwQueues(db, user_id)

    def item(chat: TiqoraChatSession, ticket: dict[str, Any]) -> ChatInboxItemOut:
        return ChatInboxItemOut(
            session_id=chat.id,
            ticket_id=int(chat.ticket_id),
            tn=ticket["tn"],
            title=ticket["title"],
            topic_label=_topic_label(values.get(int(chat.config_id)), chat.topic_key),
            visitor_name=chat.visitor_name,
            status="active" if chat.status == "active" else "waiting",
            waiting_since=chat.waiting_since,
            accepted_at=chat.accepted_at,
        )

    waiting: list[ChatInboxItemOut] = []
    for chat in waiting_rows:
        ticket = tickets.get(int(chat.ticket_id))
        if ticket is not None and await rw.allows(int(ticket["queue_id"])):
            waiting.append(item(chat, ticket))
    mine = [
        item(chat, tickets[int(chat.ticket_id)])
        for chat in mine_rows
        if int(chat.ticket_id) in tickets
    ]
    return ChatInboxOut(waiting=waiting, mine=mine)


# -- accept --------------------------------------------------------------------------------


async def accept_session(
    db: AsyncSession, sysconfig: SysConfig, *, session_id: str, user_id: int, now: datetime
) -> TiqoraChatSession:
    """First accept wins: the chat becomes ``active`` with *user_id* as its agent.

    Run it at the START of a transaction — ``RuntimeError`` when the session is already in
    one (see the module docstring: the agent-state locking read must come before the
    transaction's first plain read on MariaDB). Commit earlier reads first (R-TX).

    In order: lock the agent's state row and count the agent's active chats under it;
    ``rw`` on the ticket's current queue (else ``forbidden``); ``at_capacity`` when the
    count is ``≥ max_concurrent``; lock the session row and require ``waiting``
    (``already_taken`` when another agent got it, ``not_waiting`` when it ended);
    ``UPDATE … WHERE status = 'waiting'``; the ticket like Znuny's frontend: unlocked →
    ``lock_ticket`` then ``assign_owner`` (history Lock, Misc, OwnerUpdate), locked by
    someone else → ``assign_owner`` only; system line ``agent_joined`` (``name`` = first
    name when the config shows names); wake + agent event ``chat_assigned`` with
    ``user_id`` and the current queue. Refusals: ``not_found``, ``forbidden``,
    ``at_capacity``, ``already_taken``, ``not_waiting`` — the caller rolls back.
    """
    if db.in_transaction():
        # A plain read earlier in this transaction would pin MariaDB's snapshot before the
        # agent-state lock and make the capacity count stale (silently over capacity).
        raise RuntimeError("accept_session must start its own transaction (commit first)")
    # (1) agent state FIRST, then the capacity count (the transaction's first plain read)
    state = await lock_agent_state(db, user_id, now=now)
    held = await active_count(db, user_id)

    chat = await get_chat_session(db, session_id)
    if chat is None or chat.purged_at is not None:
        raise ChatError("not_found", 404)
    rw = RwQueues(db, user_id)
    if not await rw.allows(await _ticket_queue_id(db, chat)):
        raise ChatError("forbidden", 403)
    if held >= state.max_concurrent:
        raise ChatError("at_capacity", 409)

    # (2) the session row
    locked = await get_chat_session(db, session_id, for_update=True)
    if locked is None or locked.purged_at is not None:
        raise ChatError("not_found", 404)
    if locked.status != "waiting":
        raise _refusal_for(locked.status)
    queue_id = await current_queue_id(db, locked)
    if not await rw.allows(queue_id):
        raise ChatError("forbidden", 403)
    result = await db.execute(
        update(TiqoraChatSession)
        .where(TiqoraChatSession.id == locked.id, TiqoraChatSession.status == "waiting")
        .values(status="active", assigned_user_id=user_id, accepted_at=now)
        .execution_options(synchronize_session=False)
    )
    if getattr(result, "rowcount", 1) != 1:  # unreachable while the row lock is held
        raise ChatError("already_taken", 409)
    set_committed_value(locked, "status", "active")
    set_committed_value(locked, "assigned_user_id", user_id)
    set_committed_value(locked, "accepted_at", now)

    # (3) the ticket: Lock (if unlocked), then OwnerUpdate (if another owner) — both no-ops
    # when already true, exactly like Znuny's TicketLockSet / TicketOwnerSet.
    ticket_id = int(locked.ticket_id)
    await lock_ticket(db, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig)
    await assign_owner(
        db, ticket_id=ticket_id, new_owner_id=user_id, user_id=user_id, sysconfig=sysconfig
    )

    # (4) transcript line + notifications (queued; published after the caller's commit)
    name = await _joined_name(db, locked, user_id)
    await add_system_message(db, chat=locked, code="agent_joined", params={"name": name}, now=now)
    queue_agent_event(
        db,
        {
            "type": "chat_assigned",
            "session_id": locked.id,
            "ticket_id": ticket_id,
            "queue_id": queue_id,
            "user_id": user_id,
        },
    )
    logger.info("webchat_session_accepted", session_id=locked.id, user_id=user_id)
    return locked


# -- end -----------------------------------------------------------------------------------


async def end_session_by_agent(
    db: AsyncSession,
    sysconfig: SysConfig,
    *,
    session_id: str,
    user_id: int,
    close_ticket: bool,
    now: datetime,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """An agent ends a live chat (``waiting`` or ``active``): ``ended`` / ``agent`` /
    ``ended_at``; with *close_ticket* the ticket goes to ``closed successful`` through
    ``TicketWriteService.change_state`` (the close screen's lock/owner/unlock parity);
    system line ``chat_ended``; wake + agent event ``chat_ended``.

    Who may: any agent with ``rw`` on the ticket's current queue (the same right as
    accepting). Idempotent: a chat that is no longer live is left alone (no permission
    check, no write). Refusals: ``not_found``, ``forbidden`` — the caller rolls back.
    *factory* only satisfies ``TicketWriteService`` (unused by ``change_state``).
    """
    locked = await get_chat_session(db, session_id, for_update=True)
    if locked is None or locked.purged_at is not None:
        raise ChatError("not_found", 404)
    if locked.status not in LIVE_STATUSES:
        return
    queue_id = await current_queue_id(db, locked)
    if not await RwQueues(db, user_id).allows(queue_id):
        raise ChatError("forbidden", 403)

    locked.status = "ended"
    locked.end_reason = "agent"
    locked.ended_at = now
    await db.flush([locked])

    ticket_id = int(locked.ticket_id)
    if close_ticket:
        state_id = await closed_state_id(db)
        if state_id is None:
            logger.warning("webchat_closed_state_missing", ticket_id=ticket_id)
        else:
            writer = TicketWriteService(db, factory or get_session_factory(), sysconfig)
            try:
                await writer.change_state(user_id, ticket_id, state_id)
            except TicketAccessDenied as exc:
                raise ChatError("forbidden", 403) from exc

    await add_system_message(db, chat=locked, code="chat_ended", params={}, now=now)
    queue_agent_event(
        db,
        {
            "type": "chat_ended",
            "session_id": locked.id,
            "ticket_id": ticket_id,
            "queue_id": queue_id,
            "user_id": user_id,
        },
    )
    logger.info("webchat_session_ended_by_agent", session_id=locked.id, user_id=user_id)
