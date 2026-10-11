"""Agent side of the web chat: topics, inbox, first-accept-wins, ending a chat, agent
replies into the chat, the ticket's chat info and agent typing (C6).

Transaction contract — same as :mod:`tiqora.webchat.sessions` (R-TX): every function runs
inside the CALLER's transaction and never calls ``db.begin()``, ``commit()`` or
``rollback()``; it only queues notifications (``notify.queue_wakeup`` /
``queue_agent_event``), which the caller publishes with ``flush_chat_notifications`` after
its commit. On a ``ChatError`` the caller ROLLS BACK: ``accept_session`` may already have
refreshed the session's queue copy when it refuses. ``accept_session`` needs the agent's
state row to exist already (``ensure_agent_state_row`` + commit first, R-REPLY-ACCEPT-FIRST;
``availability.AgentStateMissing`` otherwise).

Lock order (R-LOCK-ORDER), the same in every webchat write:

1. the agent-state row (``accept_session`` only) — ``SELECT … FOR UPDATE``; it serialises
   every accept of one agent (other tabs, the implicit accept of an agent reply), so the
   capacity count taken under it cannot be raced by the same agent;
2. the ``tiqora_chat_session`` row — ``FOR UPDATE`` by primary key;
3. the Znuny ticket row (accept reads its queue ``FOR UPDATE`` by primary key) and the
   ticket writes (``lock_ticket``, ``assign_owner``, ``change_state``);
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

Agent replies (Task 10): an agent's customer-visible ``webchat`` article is also a transcript
line. ``TicketWriteService.add_article`` routes exactly that article (channel ``webchat``,
sender ``agent``, visible for the customer) to ``deliver_agent_chat_reply``; every other
article never reaches the visitor. A reply to a chat nobody accepted yet first takes it with
``accept_waiting_chat_for_reply`` in its OWN committed transaction (R-REPLY-ACCEPT-FIRST,
the reply route calls it before its write transaction); a refused accept refuses the reply.
Agents keep full access while the channel or the config is switched off
(R-DISABLED-SESSIONS): nothing here checks ``channel_enabled``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any, Final

import structlog
from sqlalchemy import bindparam, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import set_committed_value

from tiqora.channels.common import assert_ticket_note_permission
from tiqora.db.engine import get_session_factory
from tiqora.db.tiqora.models import TiqoraChatConfig, TiqoraChatSession
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    InvalidInput,
    TicketAccessDenied,
    TicketNotFound,
    TicketWriteService,
    add_article,
    assign_owner,
    lock_ticket,
)
from tiqora.jobs.service import utc_now
from tiqora.permissions.engine import PermissionEngine
from tiqora.webchat.availability import (
    VISITOR_SEEN_PREFIX,
    VISITOR_TYPING_PREFIX,
    active_count,
    ensure_agent_state_row,
    lock_agent_state,
    topic_ref,
)
from tiqora.webchat.hub import publish_chat_typing
from tiqora.webchat.models import ChatConfigValues
from tiqora.webchat.notify import queue_agent_event, queue_wakeup
from tiqora.webchat.ratelimit import ChatRateLimiter
from tiqora.webchat.schemas import (
    ChatInboxItemOut,
    ChatInboxOut,
    ChatTopicRefOut,
    TicketChatOut,
)
from tiqora.webchat.sessions import (
    LIVE_STATUSES,
    ChatError,
    add_system_message,
    current_queue_id,
    visitor_from_address,
)
from tiqora.webchat.store import (
    config_values,
    get_chat_session,
    insert_message,
    live_session_for_ticket,
)
from tiqora.webchat.text import CONTROL_CHARS, MessageRejected, chat_body_from_article
from tiqora.znuny.sysconfig import SysConfig

__all__ = [
    "AGENT_TYPING_SCOPE",
    "AGENT_TYPING_WINDOW_SECONDS",
    "CHAT_REPLY_CONTENT_TYPE",
    "CLOSED_STATE_NAME",
    "INBOX_WAITING_LIMIT",
    "ChatNotLive",
    "RwQueues",
    "accept_session",
    "accept_waiting_chat_for_reply",
    "agent_chat_body",
    "agent_typing",
    "closed_state_id",
    "deliver_agent_chat_reply",
    "end_session_by_agent",
    "list_agent_topics",
    "list_inbox",
    "ticket_chat_info",
]

logger = structlog.get_logger(__name__)

#: ``ticket_state.name`` an agent's "Chat beenden + Ticket schließen" sets; when it is
#: missing, the first valid state of type ``closed``.
CLOSED_STATE_NAME: Final = "closed successful"
#: Waiting chats listed in the inbox (oldest first), counted after the ``rw`` filter. All
#: waiting rows are loaded: the sweep ends them after ``wait_timeout_seconds`` (≤ 1 h).
INBOX_WAITING_LIMIT: Final = 200
#: An agent reply is stored as plain text (the transcript is plain text end-to-end).
CHAT_REPLY_CONTENT_TYPE: Final = "text/plain; charset=utf-8"
#: At most one agent typing signal per chat per window (``ChatRateLimiter.try_hit``).
AGENT_TYPING_SCOPE: Final = "agent_typing"
AGENT_TYPING_WINDOW_SECONDS: Final = 2
#: Fallback name of an agent attachment whose name is empty after cleaning.
_FALLBACK_FILENAME: Final = "datei"
_MAX_FILENAME: Final = 250


class ChatNotLive(Exception):
    """The ticket has no live (``waiting``/``active``) chat: the reply cannot reach a visitor.

    ``TicketWriteService.add_article`` re-raises it as ``InvalidInput("chat_not_live")``.
    """


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
            if len(waiting) >= INBOX_WAITING_LIMIT:
                break
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
    state = await lock_agent_state(db, user_id)
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
    # (3) the ticket row, locked: its newest queue (MariaDB snapshot may predate a move)
    queue_id = await current_queue_id(db, locked, lock_ticket_row=True)
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

    Who may (R-END-OWN-CHAT): the agent the chat is assigned to, always — also after the
    ticket moved to a queue they no longer hold ``rw`` on, so the chat cannot block their
    capacity; anybody else needs ``rw`` on the ticket's current queue. Closing the ticket
    always needs ``rw`` (``TicketWriteService.change_state``): an assignee without it gets
    ``forbidden`` for ``close_ticket`` and nothing is written — ending without closing works.
    Idempotent: a chat that is no longer live is left alone (no permission check, no
    write). Refusals: ``not_found``, ``forbidden`` — the caller rolls back.
    *factory* only satisfies ``TicketWriteService`` (unused by ``change_state``).
    """
    locked = await get_chat_session(db, session_id, for_update=True)
    if locked is None or locked.purged_at is not None:
        raise ChatError("not_found", 404)
    if locked.status not in LIVE_STATUSES:
        return
    queue_id = await current_queue_id(db, locked)
    may_write = await RwQueues(db, user_id).allows(queue_id)
    own_chat = locked.status == "active" and locked.assigned_user_id == user_id
    if not may_write and (not own_chat or close_ticket):
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


# -- agent replies -------------------------------------------------------------------------


def _clean_filename(name: str) -> str:
    cleaned = CONTROL_CHARS.sub("", name or "").replace("/", "_").replace("\\", "_").strip()
    return cleaned[:_MAX_FILENAME] or _FALLBACK_FILENAME


def agent_chat_body(body: str, content_type: str, attachment_names: Sequence[str] = ()) -> str:
    """The transcript text of an agent reply (pure): HTML → plain text, control and bidi
    characters dropped, ≤ ``MAX_AGENT_MESSAGE_LENGTH``. A reply that only carries files uses
    their names (like a visitor upload without caption). ``InvalidInput("message_rejected")``
    when it is empty or too long — checked BEFORE the implicit accept and any write.
    """
    try:
        return chat_body_from_article(body, content_type)
    except MessageRejected as exc:
        names = [n for n in attachment_names if n]
        if exc.code == "empty" and names:
            return chat_body_from_article(", ".join(names), CHAT_REPLY_CONTENT_TYPE)
        raise InvalidInput("message_rejected") from exc


async def accept_waiting_chat_for_reply(
    db: AsyncSession, sysconfig: SysConfig, *, ticket_id: int, user_id: int, now: datetime
) -> None:
    """R-REPLY-ACCEPT-FIRST: the ticket's chat is still ``waiting`` → *user_id* accepts it
    in its own transaction, committed before the reply's write transaction starts.

    Call it with NO transaction open on *db* (``RuntimeError`` otherwise); it ends every
    transaction it starts (commit or rollback). The caller publishes the queued accept
    notifications with ``flush_chat_notifications`` right after — also when the reply then
    fails, the chat stays accepted. No live chat, or an active one: nothing happens (the
    reply itself decides). Refusals of the accept refuse the reply, nothing is written:
    ``InvalidInput(code)`` with ``forbidden`` (403, checked before anything is written),
    ``at_capacity`` / ``already_taken`` (409); a chat purged or ended meanwhile
    (``not_found`` / ``not_waiting``) is left to the reply, which answers ``chat_not_live``.
    """
    if db.in_transaction():
        raise RuntimeError("accept_waiting_chat_for_reply needs a session without a transaction")
    chat = await live_session_for_ticket(db, ticket_id)
    if chat is None or chat.status != "waiting":
        await db.rollback()
        return
    session_id = chat.id
    if not await RwQueues(db, user_id).allows(await _ticket_queue_id(db, chat)):
        # Refused before writing anything (no agent-state row for an unauthorised caller);
        # accept_session re-checks under its locks.
        await db.rollback()
        raise InvalidInput("forbidden")
    # The state row must exist (and be committed) before accept_session locks it.
    await ensure_agent_state_row(db, user_id, now=now)
    await db.commit()
    try:
        await accept_session(db, sysconfig, session_id=session_id, user_id=user_id, now=now)
    except ChatError as exc:
        await db.rollback()
        if exc.code in ("not_found", "not_waiting"):
            # Purged or ended meanwhile: the reply re-reads the chat under its lock and
            # answers ``chat_not_live`` — one code for "the chat is over".
            return
        raise InvalidInput(exc.code) from exc
    await db.commit()
    logger.info("webchat_reply_accepted_chat", session_id=session_id, user_id=user_id)


async def deliver_agent_chat_reply(
    db: AsyncSession,
    sysconfig: SysConfig,
    *,
    ticket_id: int,
    user_id: int,
    article: ArticleIn,
    now: datetime | None = None,
) -> int:
    """An agent's customer-visible webchat article: stored like every article AND posted
    into the ticket's live chat. Returns the article id.

    Runs inside the caller's transaction (``TicketWriteService.add_article`` already checked
    ``note`` on the ticket's queue — any agent with ``note`` may answer an active chat, as on
    Telegram). Lock order (R-LOCK-ORDER): the session row by primary key, then the article
    (``add_article``, which also registers the ``WebChat`` channel row, R-CHANNEL-ROW), then
    the transcript line (``insert_message``). The article is plain text
    (``chat_body_from_article``) with ``subject`` = the given one or the ticket title and
    ``To`` = the visitor's address; the line carries the agent's first name when the config
    shows names, and the files as ``{index, filename, content_type, size}`` in article order
    (the visitor download indexes the stored attachments by that position).

    Refusals before any write: ``InvalidInput("message_rejected")``; ``ChatNotLive`` when the
    ticket has no live chat; ``InvalidInput("chat_not_accepted")`` when the chat is still
    ``waiting`` — callers take it first with ``accept_waiting_chat_for_reply``.
    """
    files = [(_clean_filename(n), ct, data) for n, ct, data in article.attachments]
    body = agent_chat_body(article.body, article.content_type, [f[0] for f in files])
    chat = await live_session_for_ticket(db, ticket_id)
    if chat is None:
        raise ChatNotLive(ticket_id)
    locked = await get_chat_session(db, chat.id, for_update=True)
    if locked is None or locked.purged_at is not None or locked.status not in LIVE_STATUSES:
        raise ChatNotLive(ticket_id)
    if locked.status != "active":
        raise InvalidInput("chat_not_accepted")

    title = (await _ticket_rows(db, [ticket_id])).get(ticket_id, {}).get("title", "")
    reply = replace(
        article,
        body=body,
        content_type=CHAT_REPLY_CONTENT_TYPE,
        subject=article.subject or title,
        to_address=visitor_from_address(
            locked.visitor_name, locked.visitor_email, locked.visitor_id
        ),
        attachments=files,
    )
    article_id = await add_article(
        db, ticket_id=ticket_id, article=reply, user_id=user_id, sysconfig=sysconfig
    )
    listing = [
        {"index": i, "filename": name, "content_type": ct, "size": len(data)}
        for i, (name, ct, data) in enumerate(files)
    ]
    await insert_message(
        db,
        chat=locked,
        sender="agent",
        body=body,
        user_id=user_id,
        article_id=article_id,
        attachments=listing,
        meta={"agent_name": await _joined_name(db, locked, user_id)},
        now=now or utc_now(),
    )
    queue_wakeup(db, locked.id)
    logger.info("webchat_agent_reply", session_id=locked.id, article_id=article_id)
    return article_id


# -- ticket chat info and typing -------------------------------------------------------------


async def _redis_flag(client: Any, key: str) -> bool:
    try:
        return bool(await client.exists(key))
    except Exception:  # noqa: BLE001 - presence markers are best-effort
        return False


async def _agent_full_name(db: AsyncSession, user_id: int) -> str | None:
    row = (
        await db.execute(
            text("SELECT first_name, last_name, login FROM users WHERE id = :u"), {"u": user_id}
        )
    ).first()
    if row is None:
        return None
    name = " ".join(str(p).strip() for p in (row[0], row[1]) if p and str(p).strip())
    return name or (str(row[2]) if row[2] else None)


async def ticket_chat_info(
    db: AsyncSession, client: Any, *, ticket_id: int, user_id: int
) -> TicketChatOut:
    """The ticket's newest (not purged) chat for the agent: ``ro`` on the ticket's CURRENT
    queue (``TicketNotFound`` / ``TicketAccessDenied``), ``ChatError("not_found")`` without a
    chat. ``visitor_online`` / ``visitor_typing`` come from the Redis markers (``False`` when
    Redis fails)."""
    ticket = (await _ticket_rows(db, [ticket_id])).get(ticket_id)
    if ticket is None:
        raise TicketNotFound(ticket_id)
    if not await PermissionEngine(db).check(user_id, int(ticket["queue_id"]), "ro"):
        raise TicketAccessDenied(f"user {user_id} lacks ro on ticket {ticket_id}")
    chat = (
        await db.execute(
            select(TiqoraChatSession)
            .where(TiqoraChatSession.ticket_id == ticket_id, TiqoraChatSession.purged_at.is_(None))
            .order_by(TiqoraChatSession.created.desc(), TiqoraChatSession.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if chat is None:
        raise ChatError("not_found", 404)
    cfg = (await _configs(db, [int(chat.config_id)])).get(int(chat.config_id))
    assigned = chat.assigned_user_id
    return TicketChatOut(
        session_id=chat.id,
        status=chat.status,
        end_reason=chat.end_reason,
        visitor_name=chat.visitor_name,
        visitor_email=chat.visitor_email,
        visitor_email_verified=False,
        topic_label=_topic_label(config_values(cfg) if cfg else None, chat.topic_key),
        page_url=chat.page_url,
        language=chat.language,
        waiting_since=chat.waiting_since,
        accepted_at=chat.accepted_at,
        ended_at=chat.ended_at,
        assigned_user_id=assigned,
        assigned_name=await _agent_full_name(db, int(assigned)) if assigned else None,
        visitor_online=await _redis_flag(client, f"{VISITOR_SEEN_PREFIX}{chat.id}"),
        visitor_typing=await _redis_flag(client, f"{VISITOR_TYPING_PREFIX}{chat.id}"),
    )


async def agent_typing(db: AsyncSession, client: Any, *, ticket_id: int, user_id: int) -> None:
    """The agent is typing in the ticket's live chat: ``note`` on the ticket's queue
    (``TicketNotFound`` / ``TicketAccessDenied``), ``ChatNotLive`` without a live chat; at
    most one ``typing`` signal per chat per 2 s reaches the visitor stream, the rest is
    dropped. A ``waiting`` chat gets no signal (nobody joined yet). Nothing is stored."""
    await assert_ticket_note_permission(db, user_id=user_id, ticket_id=ticket_id)
    chat = await live_session_for_ticket(db, ticket_id)
    if chat is None:
        raise ChatNotLive(ticket_id)
    if chat.status != "active":
        return
    limiter = ChatRateLimiter(client)
    if await limiter.try_hit(
        AGENT_TYPING_SCOPE, chat.id, window_seconds=AGENT_TYPING_WINDOW_SECONDS
    ):
        await publish_chat_typing(client, chat.id)
