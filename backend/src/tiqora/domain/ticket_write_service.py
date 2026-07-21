"""Ticket and article write service — sole write path for Tiqora.

This module ports the full Znuny write semantics for tickets and articles,
using only the invariant modules under ``tiqora.znuny.*`` for side-effects
(ticket numbering, history, escalation, ticket_index, cache_invalidation,
search_flag).  Every public method runs inside a single caller-supplied
transaction; the caller must ``await session.commit()`` or use an outer
``async with session.begin()``.

Key Znuny semantics honoured:
- TicketCreate: TN via ticket_create_number, NewTicket history, escalation
  recompute, ticket_index (StaticDB), cache_invalidation, dynamic fields,
  optional first article.
- ArticleCreate: article + article_data_mime + attachments via storage backend,
  insert_fingerprint, a_message_id + a_message_id_md5, search_flag,
  history (AddNote / PhoneCallAgent / EmailAgent by channel/sender).
- Field mutations: move_queue, change_state, change_priority, change_title,
  set_customer, assign_owner, assign_responsible, lock_ticket, unlock_ticket,
  watch_ticket, unwatch_ticket, archive_ticket, update_dynamic_field.
- merge_tickets: exact port of Kernel/System/Ticket.pm::TicketMerge.
- All write ops emit an event row to tiqora_event_outbox within the same
  transaction (Znuny-style event names: TicketCreate, ArticleCreate, etc.).
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.cache_invalidation import invalidate_ticket_cache
from tiqora.znuny.escalation import escalation_index_build
from tiqora.znuny.history import (
    TYPE_ADD_NOTE,
    TYPE_EMAIL_AGENT,
    TYPE_MISC,
    TYPE_PHONE_CALL_AGENT,
    add_archive_flag_update,
    add_article_history,
    add_customer_update,
    add_dynamic_field_update,
    add_lock,
    add_merged,
    add_move,
    add_new_ticket,
    add_owner_update,
    add_pending_time,
    add_priority_update,
    add_responsible_update,
    add_state_update,
    add_subscribe,
    add_title_update,
    add_unsubscribe,
    history_add,
)
from tiqora.znuny.search_flag import mark_search_rebuild, message_id_md5
from tiqora.znuny.sysconfig import SysConfig
from tiqora.znuny.ticket_index import ticket_accelerator_add, ticket_accelerator_update
from tiqora.znuny.ticket_number import ticket_create_number

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TicketNotFound(Exception):
    """Ticket id does not exist."""


class TicketAccessDenied(Exception):
    """User lacks permission on the ticket's queue group."""


class InvalidInput(Exception):
    """Caller passed an invalid combination of parameters."""


# ---------------------------------------------------------------------------
# Input dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ArticleIn:
    """Parameters for creating one article (MIME backend)."""

    sender_type: str  # "agent" | "customer" | "system"
    is_visible_for_customer: bool
    subject: str
    body: str
    content_type: str = "text/plain; charset=utf-8"
    from_address: str | None = None
    to_address: str | None = None
    cc: str | None = None
    bcc: str | None = None
    reply_to: str | None = None
    message_id: str | None = None
    in_reply_to: str | None = None
    references: str | None = None
    # "email" | "phone" | "note" | "internal" — affects history type
    channel: str = "note"
    # Binary attachments: list of (filename, content_type, content_bytes)
    attachments: list[tuple[str, str, bytes]] = field(default_factory=list)
    # Override the derived ticket_history type (e.g. postmaster auto-responses
    # use SendAutoReply/SendAutoFollowUp/SendAutoReject instead of the
    # channel/sender-derived EmailAgent/EmailCustomer names).
    history_type_override: str | None = None


@dataclass
class TicketIn:
    """Parameters for creating one ticket."""

    title: str
    queue_id: int
    state_id: int
    priority_id: int
    owner_id: int
    lock_id: int = 1  # unlock
    type_id: int | None = None
    service_id: int | None = None
    sla_id: int | None = None
    responsible_id: int | None = None
    customer_id: str | None = None
    customer_user_id: str | None = None
    archive_flag: int = 0
    # Dynamic fields: {field_name: [values]}
    dynamic_fields: dict[str, list[str]] = field(default_factory=dict)
    # Optional first article to attach inline
    article: ArticleIn | None = None


# ---------------------------------------------------------------------------
# Outbox helper
# ---------------------------------------------------------------------------


async def _emit_event(
    session: AsyncSession,
    event_type: str,
    ticket_id: int,
    payload: dict[str, Any] | None = None,
) -> None:
    """Write one row to tiqora_event_outbox within the current transaction."""
    import json

    await session.execute(
        text(
            "INSERT INTO tiqora_event_outbox"
            " (event_type, ticket_id, payload, created, processed)"
            " VALUES (:et, :tid, :pl, current_timestamp, :pr)"
        ),
        {
            "et": event_type,
            "tid": ticket_id,
            "pl": json.dumps(payload or {}),
            "pr": False,
        },
    )


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


async def _lookup_row(session: AsyncSession, table: str, col: str, val: Any) -> dict[str, Any]:
    row = (
        (await session.execute(text(f"SELECT * FROM {table} WHERE {col} = :v LIMIT 1"), {"v": val}))
        .mappings()
        .first()
    )
    if row is None:
        raise InvalidInput(f"No {table} with {col}={val!r}")
    return dict(row)


async def _queue_name(session: AsyncSession, queue_id: int) -> str:
    row = await _lookup_row(session, "queue", "id", queue_id)
    return str(row["name"])


async def _state_name(session: AsyncSession, state_id: int) -> str:
    row = await _lookup_row(session, "ticket_state", "id", state_id)
    return str(row["name"])


async def _state_type_name(session: AsyncSession, state_id: int) -> str:
    row = (
        await session.execute(
            text(
                "SELECT tst.name FROM ticket_state ts"
                " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                " WHERE ts.id = :sid LIMIT 1"
            ),
            {"sid": state_id},
        )
    ).first()
    if row is None:
        return "open"
    return str(row[0])


async def _priority_name(session: AsyncSession, priority_id: int) -> str:
    row = await _lookup_row(session, "ticket_priority", "id", priority_id)
    return str(row["name"])


async def _user_login(session: AsyncSession, user_id: int) -> str:
    row = (
        await session.execute(
            text("SELECT login FROM users WHERE id = :uid LIMIT 1"), {"uid": user_id}
        )
    ).first()
    return str(row[0]) if row else "unknown"


async def _sender_type_id(session: AsyncSession, name: str) -> int:
    row = (
        await session.execute(
            text("SELECT id FROM article_sender_type WHERE name = :n LIMIT 1"), {"n": name}
        )
    ).first()
    if row is None:
        raise InvalidInput(f"Unknown article_sender_type: {name!r}")
    return int(row[0])


async def _channel_id(session: AsyncSession, name: str) -> int:
    """Return communication_channel.id by channel name."""
    row = (
        await session.execute(
            text("SELECT id FROM communication_channel WHERE name = :n LIMIT 1"), {"n": name}
        )
    ).first()
    if row is None:
        # Fallback: use id=1 (Internal) if unknown
        return 1
    return int(row[0])


async def _dynamic_field_id(session: AsyncSession, field_name: str) -> int | None:
    row = (
        await session.execute(
            text(
                "SELECT id FROM dynamic_field WHERE name = :n AND object_type = 'Ticket'"
                " AND valid_id = 1 LIMIT 1"
            ),
            {"n": field_name},
        )
    ).first()
    return int(row[0]) if row else None


async def _ticket_must_exist(session: AsyncSession, ticket_id: int) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT t.id, t.tn, t.queue_id, t.ticket_state_id, t.ticket_priority_id,"
                    " t.user_id, t.responsible_user_id, t.ticket_lock_id, t.type_id,"
                    " t.customer_id, t.customer_user_id, t.archive_flag, t.title"
                    " FROM ticket t WHERE t.id = :tid LIMIT 1"
                ),
                {"tid": ticket_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise TicketNotFound(ticket_id)
    return dict(row)


# ---------------------------------------------------------------------------
# Sub-task 1: create_ticket
# ---------------------------------------------------------------------------


async def create_ticket(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    *,
    params: TicketIn,
    user_id: int,
) -> int:
    """Create a ticket and return its id.

    Full invariant bundle:
    - TN via ticket_create_number (counter algorithm)
    - INSERT ticket row
    - NewTicket history (exact Znuny name format)
    - escalation_index_build
    - ticket_accelerator_add (StaticDB only)
    - invalidate_ticket_cache
    - dynamic fields (multivalue delete+insert)
    - optional first article (calls add_article)
    - emits TicketCreate outbox event
    """
    tn = await ticket_create_number(session_factory, sysconfig)

    title = (params.title or "")[:255]
    responsible_id = params.responsible_id if params.responsible_id is not None else 1
    type_id = params.type_id if params.type_id is not None else 1

    await session.execute(
        text(
            "INSERT INTO ticket"
            " (tn, title, type_id, queue_id, ticket_lock_id,"
            "  user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
            "  escalation_time, escalation_update_time, escalation_response_time,"
            "  escalation_solution_time, timeout, service_id, sla_id, until_time,"
            "  archive_flag, customer_id, customer_user_id,"
            "  create_time, create_by, change_time, change_by)"
            " VALUES"
            " (:tn, :title, :type_id, :queue_id, :lock_id,"
            "  :owner_id, :resp_id, :priority_id, :state_id,"
            "  0, 0, 0, 0, 0, :svc_id, :sla_id, 0,"
            "  :archive, :cid, :cuid,"
            "  current_timestamp, :uid, current_timestamp, :uid)"
        ),
        {
            "tn": tn,
            "title": title,
            "type_id": type_id,
            "queue_id": params.queue_id,
            "lock_id": params.lock_id,
            "owner_id": params.owner_id,
            "resp_id": responsible_id,
            "priority_id": params.priority_id,
            "state_id": params.state_id,
            "svc_id": params.service_id,
            "sla_id": params.sla_id,
            "archive": params.archive_flag,
            "cid": params.customer_id,
            "cuid": params.customer_user_id,
            "uid": user_id,
        },
    )

    # Read back ticket id
    row = (
        await session.execute(text("SELECT id FROM ticket WHERE tn = :tn LIMIT 1"), {"tn": tn})
    ).first()
    if row is None:
        raise RuntimeError("Ticket insert succeeded but id read-back failed")
    ticket_id = int(row[0])

    # NewTicket history
    queue_name = await _queue_name(session, params.queue_id)
    priority_name = await _priority_name(session, params.priority_id)
    state_name = await _state_name(session, params.state_id)
    await add_new_ticket(
        session,
        ticket_id=ticket_id,
        tn=tn,
        queue=queue_name,
        priority=priority_name,
        state=state_name,
        user_id=user_id,
        queue_id=params.queue_id,
    )

    # Customer data history: Znuny TicketCreate calls TicketCustomerSet when
    # CustomerID/CustomerUser is given, writing a CustomerUpdate row with BOTH
    # parts (empty string for a missing one — both params are defined in that
    # internal call). Golden-master validated against Znuny 6.5.22.
    if params.customer_id is not None or params.customer_user_id is not None:
        await add_customer_update(
            session,
            ticket_id=ticket_id,
            customer_id=params.customer_id or "",
            customer_user=params.customer_user_id or "",
            user_id=user_id,
        )

    # Escalation
    await escalation_index_build(session, ticket_id, user_id, sysconfig)

    # Ticket index (StaticDB)
    await ticket_accelerator_add(session, ticket_id, sysconfig)

    # Cache invalidation
    await invalidate_ticket_cache(session, ticket_id)

    # Dynamic fields
    for fname, values in params.dynamic_fields.items():
        await update_dynamic_field(
            session, ticket_id=ticket_id, field_name=fname, values=values, user_id=user_id
        )

    # Optional first article
    if params.article is not None:
        await add_article(
            session,
            ticket_id=ticket_id,
            article=params.article,
            user_id=user_id,
            sysconfig=sysconfig,
        )

    # Outbox event
    await _emit_event(session, "TicketCreate", ticket_id, {"tn": tn, "queue_id": params.queue_id})

    return ticket_id


# ---------------------------------------------------------------------------
# Sub-task 2: add_article
# ---------------------------------------------------------------------------


async def add_article(
    session: AsyncSession,
    *,
    ticket_id: int,
    article: ArticleIn,
    user_id: int,
    sysconfig: SysConfig,
) -> int:
    """Add an article (MIME) to an existing ticket. Returns article_id.

    Ports MIMEBase.pm::ArticleCreate:
    - Generates insert_fingerprint (PID + random + MessageID)
    - Computes a_message_id_md5
    - Inserts article (meta) + article_data_mime
    - Stores attachments in article_data_mime_attachment
    - Sets search_index_needs_rebuild=1 via mark_search_rebuild
    - Writes history (AddNote / PhoneCallAgent / EmailAgent)
    - Emits ArticleCreate outbox event
    """
    sender_type_id = await _sender_type_id(session, article.sender_type)

    # Resolve communication channel id by channel name
    channel_name_map: dict[str, str] = {
        "email": "Email",
        "phone": "Phone",
        "note": "Internal",
        "internal": "Internal",
        "sms": "SMS",
        "whatsapp": "WhatsApp",
    }
    ch_name = channel_name_map.get(article.channel.lower(), "Internal")
    comm_channel_id = await _channel_id(session, ch_name)

    # Generate fingerprint (Znuny: PID-RandomString-MessageID)
    fingerprint = f"{__import__('os').getpid()}-{secrets.token_hex(16)}-{article.message_id or ''}"
    fingerprint = fingerprint[:64]

    # Insert meta article row
    await session.execute(
        text(
            "INSERT INTO article"
            " (ticket_id, article_sender_type_id, communication_channel_id,"
            "  is_visible_for_customer, search_index_needs_rebuild, insert_fingerprint,"
            "  create_time, create_by, change_time, change_by)"
            " VALUES"
            " (:tid, :stid, :ccid, :vis, 1, :fp,"
            "  current_timestamp, :uid, current_timestamp, :uid)"
        ),
        {
            "tid": ticket_id,
            "stid": sender_type_id,
            "ccid": comm_channel_id,
            "vis": 1 if article.is_visible_for_customer else 0,
            "fp": fingerprint,
            "uid": user_id,
        },
    )

    # Read back article id via fingerprint
    art_row = (
        await session.execute(
            text("SELECT id FROM article WHERE insert_fingerprint = :fp LIMIT 1"),
            {"fp": fingerprint},
        )
    ).first()
    if art_row is None:
        raise RuntimeError("Article insert succeeded but id read-back failed")
    article_id = int(art_row[0])

    # Compute MD5 of MessageID
    msg_id_md5 = message_id_md5(article.message_id) if article.message_id else None

    incoming_time = int(time.time())

    # Insert MIME data
    await session.execute(
        text(
            "INSERT INTO article_data_mime"
            " (article_id, a_from, a_reply_to, a_to, a_cc, a_bcc,"
            "  a_subject, a_message_id, a_message_id_md5, a_in_reply_to, a_references,"
            "  a_content_type, a_body, incoming_time,"
            "  create_time, create_by, change_time, change_by)"
            " VALUES"
            " (:aid, :frm, :rto, :to, :cc, :bcc,"
            "  :subj, :msgid, :msgmd5, :inrto, :refs,"
            "  :ct, :body, :itime,"
            "  current_timestamp, :uid, current_timestamp, :uid)"
        ),
        {
            "aid": article_id,
            "frm": article.from_address,
            "rto": article.reply_to,
            "to": article.to_address,
            "cc": article.cc,
            "bcc": article.bcc,
            "subj": article.subject,
            "msgid": article.message_id,
            "msgmd5": msg_id_md5,
            "inrto": article.in_reply_to,
            "refs": article.references,
            "ct": article.content_type,
            "body": article.body,
            "itime": incoming_time,
            "uid": user_id,
        },
    )

    # Store attachments
    for filename, content_type, content in article.attachments:
        size_str = str(len(content))
        await session.execute(
            text(
                "INSERT INTO article_data_mime_attachment"
                " (article_id, filename, content_size, content_type, content,"
                "  create_time, create_by, change_time, change_by)"
                " VALUES"
                " (:aid, :fn, :sz, :ct, :content,"
                "  current_timestamp, :uid, current_timestamp, :uid)"
            ),
            {
                "aid": article_id,
                "fn": filename,
                "sz": size_str,
                "ct": content_type,
                "content": content,
                "uid": user_id,
            },
        )

    # Search rebuild flag
    await mark_search_rebuild(session, article_id)

    # History type by channel and sender (or explicit override)
    history_type: str
    channel_lower = article.channel.lower()
    if article.history_type_override:
        history_type = article.history_type_override
    elif channel_lower == "email":
        if article.sender_type == "agent":
            history_type = TYPE_EMAIL_AGENT
        else:
            from tiqora.znuny.history import TYPE_EMAIL_CUSTOMER

            history_type = TYPE_EMAIL_CUSTOMER
    elif channel_lower == "phone":
        if article.sender_type == "agent":
            history_type = TYPE_PHONE_CALL_AGENT
        else:
            from tiqora.znuny.history import TYPE_PHONE_CALL_CUSTOMER

            history_type = TYPE_PHONE_CALL_CUSTOMER
    else:
        history_type = TYPE_ADD_NOTE

    history_comment = f"%% {article.subject[:100]}" if article.subject else "%%"
    await add_article_history(
        session,
        ticket_id=ticket_id,
        article_id=article_id,
        history_type=history_type,
        name=history_comment,
        user_id=user_id,
    )

    # Unlock-timeout reset (port of MIMEBase::ArticleCreate → Ticket.pm::
    # TicketUnlockTimeoutUpdate). Golden-master validated against 6.5.22:
    # - agent article: always reset ticket.timeout to the article's
    #   incoming_time; when the value actually changes this writes a
    #   Misc "Reset of unlock time." history row.
    # - customer article: same reset, but only when the previous
    #   non-system article was from an agent.
    reset_unlock = False
    if article.sender_type == "agent":
        reset_unlock = True
    elif article.sender_type == "customer":
        prev = (
            await session.execute(
                text(
                    "SELECT st.name FROM article a"
                    " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
                    " WHERE a.ticket_id = :tid AND a.id <> :aid AND st.name <> 'system'"
                    " ORDER BY a.id DESC LIMIT 1"
                ),
                {"tid": ticket_id, "aid": article_id},
            )
        ).first()
        reset_unlock = prev is not None and prev[0] == "agent"
    if reset_unlock:
        current_timeout = (
            await session.execute(
                text("SELECT timeout FROM ticket WHERE id = :tid"), {"tid": ticket_id}
            )
        ).scalar_one()
        if int(current_timeout or 0) != incoming_time:
            await session.execute(
                text(
                    "UPDATE ticket SET timeout = :to, change_time = current_timestamp,"
                    " change_by = :uid WHERE id = :tid"
                ),
                {"to": incoming_time, "uid": user_id, "tid": ticket_id},
            )
            await history_add(
                session,
                ticket_id=ticket_id,
                history_type=TYPE_MISC,
                name="Reset of unlock time.",
                user_id=user_id,
            )

    # Outbox event
    await _emit_event(
        session, "ArticleCreate", ticket_id, {"article_id": article_id, "channel": article.channel}
    )

    # Invalidate Znuny's on-disk ticket/article cache so the new article shows
    # up in the Znuny GUI (the TiqoraSync daemon polls tiqora_cache_invalidation).
    # Without this, field changes propagate but externally-added articles do not.
    await invalidate_ticket_cache(session, ticket_id)

    return article_id


# ---------------------------------------------------------------------------
# Sub-task 3: field mutations
# ---------------------------------------------------------------------------


async def move_queue(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_queue_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Move ticket to a new queue."""
    t = await _ticket_must_exist(session, ticket_id)
    old_queue_id = int(t["queue_id"])
    old_queue = await _queue_name(session, old_queue_id)
    new_queue = await _queue_name(session, new_queue_id)

    await session.execute(
        text(
            "UPDATE ticket SET queue_id = :qid, change_time = current_timestamp, change_by = :uid"
            " WHERE id = :tid"
        ),
        {"qid": new_queue_id, "uid": user_id, "tid": ticket_id},
    )
    await add_move(
        session,
        ticket_id=ticket_id,
        new_queue=new_queue,
        new_queue_id=new_queue_id,
        old_queue=old_queue,
        old_queue_id=old_queue_id,
        user_id=user_id,
    )
    await ticket_accelerator_update(session, ticket_id, sysconfig)
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(session, "TicketQueueUpdate", ticket_id, {"queue_id": new_queue_id})


async def change_state(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_state_id: int,
    user_id: int,
    sysconfig: SysConfig,
    pending_time: datetime | None = None,
) -> None:
    """Change ticket state; zero escalation on close; handle pending_time."""
    t = await _ticket_must_exist(session, ticket_id)
    old_state_name = await _state_name(session, int(t["ticket_state_id"]))
    new_state_name = await _state_name(session, new_state_id)
    new_state_type = await _state_type_name(session, new_state_id)

    until_time = 0
    if pending_time is not None and new_state_type.lower().startswith("pending"):
        until_time = int(pending_time.timestamp())

    await session.execute(
        text(
            "UPDATE ticket SET ticket_state_id = :sid, until_time = :ut,"
            " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
        ),
        {"sid": new_state_id, "ut": until_time, "uid": user_id, "tid": ticket_id},
    )
    await add_state_update(
        session,
        ticket_id=ticket_id,
        old_state=old_state_name,
        new_state=new_state_name,
        user_id=user_id,
        state_id=new_state_id,
    )
    if pending_time is not None and new_state_type.lower().startswith("pending"):
        await add_pending_time(
            session,
            ticket_id=ticket_id,
            year=pending_time.year,
            month=pending_time.month,
            day=pending_time.day,
            hour=pending_time.hour,
            minute=pending_time.minute,
            user_id=user_id,
        )
    elif not new_state_type.lower().startswith("pending"):
        # Port of Ticket::EventModulePost###3300-TicketPendingTimeReset:
        # on every TicketStateUpdate to a non-pending state, Znuny resets the
        # pending time via TicketPendingTimeSet('0000-00-00 00:00:00'), which
        # writes a SetPendingTime history row `%%00-00-00 00:00` even if
        # until_time was already 0. Golden-master validated against 6.5.22.
        await add_pending_time(
            session,
            ticket_id=ticket_id,
            year=0,
            month=0,
            day=0,
            hour=0,
            minute=0,
            user_id=user_id,
        )
    # Recompute escalation (close zeroes it out)
    await escalation_index_build(session, ticket_id, user_id, sysconfig)
    await ticket_accelerator_update(session, ticket_id, sysconfig)
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(session, "TicketStateUpdate", ticket_id, {"state_id": new_state_id})


async def change_priority(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_priority_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Change ticket priority."""
    t = await _ticket_must_exist(session, ticket_id)
    old_prio_id = int(t["ticket_priority_id"])
    old_prio = await _priority_name(session, old_prio_id)
    new_prio = await _priority_name(session, new_priority_id)

    await session.execute(
        text(
            "UPDATE ticket SET ticket_priority_id = :pid, change_time = current_timestamp,"
            " change_by = :uid WHERE id = :tid"
        ),
        {"pid": new_priority_id, "uid": user_id, "tid": ticket_id},
    )
    await add_priority_update(
        session,
        ticket_id=ticket_id,
        old_priority=old_prio,
        old_priority_id=old_prio_id,
        new_priority=new_prio,
        new_priority_id=new_priority_id,
        user_id=user_id,
    )
    await ticket_accelerator_update(session, ticket_id, sysconfig)
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(session, "TicketPriorityUpdate", ticket_id, {"priority_id": new_priority_id})


async def change_title(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_title: str,
    user_id: int,
) -> None:
    """Change ticket title."""
    t = await _ticket_must_exist(session, ticket_id)
    old_title = str(t.get("title") or "")
    title_truncated = new_title[:255]

    await session.execute(
        text(
            "UPDATE ticket SET title = :title, change_time = current_timestamp,"
            " change_by = :uid WHERE id = :tid"
        ),
        {"title": title_truncated, "uid": user_id, "tid": ticket_id},
    )
    await add_title_update(
        session, ticket_id=ticket_id, old_title=old_title, new_title=new_title, user_id=user_id
    )
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(session, "TicketTitleUpdate", ticket_id, {"title": title_truncated})


async def set_customer(
    session: AsyncSession,
    *,
    ticket_id: int,
    customer_id: str | None,
    customer_user_id: str | None,
    user_id: int,
) -> None:
    """Set ticket customer."""
    await _ticket_must_exist(session, ticket_id)
    await session.execute(
        text(
            "UPDATE ticket SET customer_id = :cid, customer_user_id = :cuid,"
            " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
        ),
        {"cid": customer_id, "cuid": customer_user_id, "uid": user_id, "tid": ticket_id},
    )
    await add_customer_update(
        session,
        ticket_id=ticket_id,
        customer_id=customer_id,
        customer_user=customer_user_id,
        user_id=user_id,
    )
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(
        session,
        "TicketCustomerUpdate",
        ticket_id,
        {"customer_id": customer_id, "customer_user_id": customer_user_id},
    )


async def assign_owner(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_owner_id: int,
    user_id: int,
    sysconfig: SysConfig,
    lock: bool = False,
) -> None:
    """Assign ticket owner (port of ``Ticket.pm::TicketOwnerSet``).

    Golden-master validated behaviours (Znuny 6.5.22):

    - No-op (no UPDATE, no history) when the ticket already has that owner —
      Znuny returns 2 without any write.
    - ``TicketOwnerSet`` itself never locks the ticket; auto-locking is a
      *frontend* behaviour (AgentTicketOwner with a lock config), not part of
      the core write. ``lock=True`` remains available for callers that want
      the explicit combined behaviour (writes the same rows Znuny's frontend
      would via a separate TicketLockSet call), but defaults to False.
    """
    t = await _ticket_must_exist(session, ticket_id)

    # Znuny: "check if update is needed!" — same owner is a silent no-op.
    if int(t["user_id"]) == new_owner_id:
        return

    new_login = await _user_login(session, new_owner_id)

    # Optional explicit lock (frontend-equivalent): lock if currently unlocked.
    current_lock_id = int(t["ticket_lock_id"])
    new_lock_id = current_lock_id
    if lock and current_lock_id == 1:  # 1=unlock
        new_lock_id = 2  # 2=lock

    await session.execute(
        text(
            "UPDATE ticket SET user_id = :oid, ticket_lock_id = :lid,"
            " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
        ),
        {"oid": new_owner_id, "lid": new_lock_id, "uid": user_id, "tid": ticket_id},
    )
    await add_owner_update(
        session,
        ticket_id=ticket_id,
        new_user=new_login,
        new_user_id=new_owner_id,
        user_id=user_id,
    )
    if new_lock_id != current_lock_id:
        await add_lock(session, ticket_id=ticket_id, lock="lock", user_id=user_id)
    await ticket_accelerator_update(session, ticket_id, sysconfig)
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(session, "TicketOwnerUpdate", ticket_id, {"owner_id": new_owner_id})


async def assign_responsible(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_responsible_id: int,
    user_id: int,
) -> None:
    """Assign ticket responsible."""
    await _ticket_must_exist(session, ticket_id)
    new_login = await _user_login(session, new_responsible_id)

    await session.execute(
        text(
            "UPDATE ticket SET responsible_user_id = :rid,"
            " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
        ),
        {"rid": new_responsible_id, "uid": user_id, "tid": ticket_id},
    )
    await add_responsible_update(
        session,
        ticket_id=ticket_id,
        new_user=new_login,
        new_user_id=new_responsible_id,
        user_id=user_id,
    )
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(
        session, "TicketResponsibleUpdate", ticket_id, {"responsible_id": new_responsible_id}
    )


async def lock_ticket(
    session: AsyncSession,
    *,
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Lock a ticket."""
    await _ticket_must_exist(session, ticket_id)
    await session.execute(
        text(
            "UPDATE ticket SET ticket_lock_id = 2, change_time = current_timestamp,"
            " change_by = :uid WHERE id = :tid"
        ),
        {"uid": user_id, "tid": ticket_id},
    )
    await add_lock(session, ticket_id=ticket_id, lock="lock", user_id=user_id)
    await ticket_accelerator_update(session, ticket_id, sysconfig)
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(session, "TicketLockUpdate", ticket_id, {"lock": "lock"})


async def unlock_ticket(
    session: AsyncSession,
    *,
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Unlock a ticket."""
    await _ticket_must_exist(session, ticket_id)
    await session.execute(
        text(
            "UPDATE ticket SET ticket_lock_id = 1, change_time = current_timestamp,"
            " change_by = :uid WHERE id = :tid"
        ),
        {"uid": user_id, "tid": ticket_id},
    )
    await add_lock(session, ticket_id=ticket_id, lock="unlock", user_id=user_id)
    await ticket_accelerator_update(session, ticket_id, sysconfig)
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(session, "TicketLockUpdate", ticket_id, {"lock": "unlock"})


async def watch_ticket(
    session: AsyncSession,
    *,
    ticket_id: int,
    watcher_user_id: int,
    user_id: int,
) -> None:
    """Subscribe a user to a ticket (watcher)."""
    await _ticket_must_exist(session, ticket_id)
    # Upsert: ignore if already watching
    row = (
        await session.execute(
            text("SELECT 1 FROM ticket_watcher WHERE ticket_id = :tid AND user_id = :wuid LIMIT 1"),
            {"tid": ticket_id, "wuid": watcher_user_id},
        )
    ).first()
    if row is None:
        await session.execute(
            text(
                "INSERT INTO ticket_watcher (ticket_id, user_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:tid, :wuid, current_timestamp, :uid, current_timestamp, :uid)"
            ),
            {"tid": ticket_id, "wuid": watcher_user_id, "uid": user_id},
        )
    # Get full name for history
    name_row = (
        await session.execute(
            text("SELECT first_name, last_name FROM users WHERE id = :uid LIMIT 1"),
            {"uid": watcher_user_id},
        )
    ).first()
    fullname = f"{name_row[0]} {name_row[1]}".strip() if name_row else str(watcher_user_id)
    await add_subscribe(session, ticket_id=ticket_id, user_fullname=fullname, user_id=user_id)
    await _emit_event(session, "TicketSubscribe", ticket_id, {"watcher_user_id": watcher_user_id})


async def unwatch_ticket(
    session: AsyncSession,
    *,
    ticket_id: int,
    watcher_user_id: int,
    user_id: int,
) -> None:
    """Unsubscribe a user from a ticket."""
    await _ticket_must_exist(session, ticket_id)
    await session.execute(
        text("DELETE FROM ticket_watcher WHERE ticket_id = :tid AND user_id = :wuid"),
        {"tid": ticket_id, "wuid": watcher_user_id},
    )
    name_row = (
        await session.execute(
            text("SELECT first_name, last_name FROM users WHERE id = :uid LIMIT 1"),
            {"uid": watcher_user_id},
        )
    ).first()
    fullname = f"{name_row[0]} {name_row[1]}".strip() if name_row else str(watcher_user_id)
    await add_unsubscribe(session, ticket_id=ticket_id, user_fullname=fullname, user_id=user_id)
    await _emit_event(session, "TicketUnsubscribe", ticket_id, {"watcher_user_id": watcher_user_id})


async def archive_ticket(
    session: AsyncSession,
    *,
    ticket_id: int,
    archive: bool,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Set archive flag on a ticket."""
    await _ticket_must_exist(session, ticket_id)
    flag_int = 1 if archive else 0
    flag_str = "y" if archive else "n"
    await session.execute(
        text(
            "UPDATE ticket SET archive_flag = :fl, change_time = current_timestamp,"
            " change_by = :uid WHERE id = :tid"
        ),
        {"fl": flag_int, "uid": user_id, "tid": ticket_id},
    )
    await add_archive_flag_update(
        session, ticket_id=ticket_id, archive_flag=flag_str, user_id=user_id
    )
    await ticket_accelerator_update(session, ticket_id, sysconfig)
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(session, "TicketArchiveFlagUpdate", ticket_id, {"archive": archive})


async def update_dynamic_field(
    session: AsyncSession,
    *,
    ticket_id: int,
    field_name: str,
    values: list[str],
    user_id: int,
) -> None:
    """Set a dynamic field to new values (multivalue: delete + insert).

    Znuny: DynamicFieldValueSet deletes all existing values then inserts new ones.
    Old value read before delete for history.
    """
    field_id = await _dynamic_field_id(session, field_name)
    if field_id is None:
        return  # Silently ignore unknown fields (Znuny behaviour)

    # Read old values for history
    old_rows = (
        await session.execute(
            text(
                "SELECT COALESCE(value_text, CAST(value_int AS CHAR), '') as v"
                " FROM dynamic_field_value WHERE field_id = :fid AND object_id = :oid"
                " ORDER BY id"
            ),
            {"fid": field_id, "oid": ticket_id},
        )
    ).fetchall()
    old_value = ", ".join(str(r[0]) for r in old_rows if r[0] is not None)

    # Delete existing
    await session.execute(
        text("DELETE FROM dynamic_field_value WHERE field_id = :fid AND object_id = :oid"),
        {"fid": field_id, "oid": ticket_id},
    )

    # Insert new values
    for v in values:
        await session.execute(
            text(
                "INSERT INTO dynamic_field_value (field_id, object_id, value_text)"
                " VALUES (:fid, :oid, :val)"
            ),
            {"fid": field_id, "oid": ticket_id, "val": v},
        )

    new_value = ", ".join(values)
    await add_dynamic_field_update(
        session,
        ticket_id=ticket_id,
        field_name=field_name,
        value=new_value,
        old_value=old_value,
        user_id=user_id,
    )
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(
        session,
        "TicketDynamicFieldUpdate",
        ticket_id,
        {"field": field_name, "values": values},
    )


# ---------------------------------------------------------------------------
# Sub-task 4: merge_tickets — exact port of TicketMerge
# ---------------------------------------------------------------------------


async def merge_tickets(
    session: AsyncSession,
    *,
    main_ticket_id: int,
    merge_ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Merge merge_ticket_id into main_ticket_id.

    Exact port of Kernel/System/Ticket.pm::TicketMerge:
    1. Move articles (ticket_id) from merge → main
    2. Move article_search_index rows
    3. Move article-linked ticket_history rows (article_id IS NOT NULL)
    4. Move time_accounting rows
    5. Add merge-note article to merge ticket
    6. Write Merged history on both tickets
    7. Transfer watchers (skip duplicates)
    8. Update main ticket change_time
    9. Set merge ticket state to first 'merged' state type
    10. Unlock merge ticket
    11. Emits TicketMerge outbox event
    """
    main = await _ticket_must_exist(session, main_ticket_id)
    merge = await _ticket_must_exist(session, merge_ticket_id)
    main_tn = str(main["tn"])
    merge_tn = str(merge["tn"])

    # 1. Move articles
    await session.execute(
        text(
            "UPDATE article SET ticket_id = :main, change_time = current_timestamp,"
            " change_by = :uid WHERE ticket_id = :merge"
        ),
        {"main": main_ticket_id, "uid": user_id, "merge": merge_ticket_id},
    )

    # 2. Move article_search_index
    await session.execute(
        text("UPDATE article_search_index SET ticket_id = :main WHERE ticket_id = :merge"),
        {"main": main_ticket_id, "merge": merge_ticket_id},
    )

    # 3. Move article-linked history rows (WHERE article_id IS NOT NULL AND article_id != 0)
    await session.execute(
        text(
            "UPDATE ticket_history SET ticket_id = :main, change_time = current_timestamp,"
            " change_by = :uid WHERE ticket_id = :merge"
            " AND article_id IS NOT NULL AND article_id != 0"
        ),
        {"main": main_ticket_id, "uid": user_id, "merge": merge_ticket_id},
    )

    # 4. Move time_accounting
    await session.execute(
        text(
            "UPDATE time_accounting SET ticket_id = :main, change_time = current_timestamp,"
            " change_by = :uid WHERE ticket_id = :merge"
        ),
        {"main": main_ticket_id, "uid": user_id, "merge": merge_ticket_id},
    )

    # 5. Add merge-note article to merge ticket (internal channel, agent sender)
    merge_note = ArticleIn(
        sender_type="agent",
        is_visible_for_customer=True,
        subject="Ticket Merged",
        body=f"Ticket {merge_tn} was merged into ticket {main_tn}.",
        content_type="text/plain; charset=ascii",
        channel="note",
    )
    await add_article(
        session,
        ticket_id=merge_ticket_id,
        article=merge_note,
        user_id=user_id,
        sysconfig=sysconfig,
    )

    # 6. Merged history on both tickets (same name format on both — Znuny does this)
    await add_merged(
        session,
        ticket_id=merge_ticket_id,
        main_tn=main_tn,
        main_ticket_id=main_ticket_id,
        merge_tn=merge_tn,
        merge_ticket_id=merge_ticket_id,
        user_id=user_id,
    )
    await add_merged(
        session,
        ticket_id=main_ticket_id,
        main_tn=main_tn,
        main_ticket_id=main_ticket_id,
        merge_tn=merge_tn,
        merge_ticket_id=merge_ticket_id,
        user_id=user_id,
    )

    # 7. Transfer watchers: remove duplicates, then reassign remaining
    main_watcher_rows = (
        await session.execute(
            text("SELECT user_id FROM ticket_watcher WHERE ticket_id = :tid"),
            {"tid": main_ticket_id},
        )
    ).fetchall()
    main_watcher_ids = {int(r[0]) for r in main_watcher_rows}

    # Delete from merge ticket any that already watch main
    for wid in main_watcher_ids:
        await session.execute(
            text("DELETE FROM ticket_watcher WHERE ticket_id = :tid AND user_id = :wuid"),
            {"tid": merge_ticket_id, "wuid": wid},
        )

    # Move remaining merge watchers to main
    await session.execute(
        text("UPDATE ticket_watcher SET ticket_id = :main WHERE ticket_id = :merge"),
        {"main": main_ticket_id, "merge": merge_ticket_id},
    )

    # 8. Update main ticket change_time
    await session.execute(
        text("UPDATE ticket SET change_time = current_timestamp, change_by = :uid WHERE id = :tid"),
        {"uid": user_id, "tid": main_ticket_id},
    )

    # 9. Set merge ticket state to first 'merged' state type
    merged_state_row = (
        await session.execute(
            text(
                "SELECT ts.id, ts.name FROM ticket_state ts"
                " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                " WHERE tst.name = 'merged' AND ts.valid_id = 1"
                " ORDER BY ts.id LIMIT 1"
            )
        )
    ).first()
    if merged_state_row is not None:
        merged_state_id = int(merged_state_row[0])
        await change_state(
            session,
            ticket_id=merge_ticket_id,
            new_state_id=merged_state_id,
            user_id=user_id,
            sysconfig=sysconfig,
        )

    # 10. Unlock merge ticket
    await unlock_ticket(session, ticket_id=merge_ticket_id, user_id=user_id, sysconfig=sysconfig)

    # Cache invalidation for both
    await invalidate_ticket_cache(session, main_ticket_id)
    await invalidate_ticket_cache(session, merge_ticket_id)

    # 11. Outbox event
    await _emit_event(
        session,
        "TicketMerge",
        merge_ticket_id,
        {"main_ticket_id": main_ticket_id, "merge_ticket_id": merge_ticket_id},
    )


async def forward_article(
    session: AsyncSession,
    *,
    ticket_id: int,
    subject: str,
    body: str,
    to_address: str,
    cc: str | None,
    user_id: int,
    sysconfig: SysConfig,
) -> int:
    """Forward: add a customer-visible email article, history type 'Forward'.

    Simplification vs Znuny's AgentTicketForward: attachments from the source
    article are NOT carried over (no MTA integration in this vertical).
    """
    article = ArticleIn(
        sender_type="agent",
        is_visible_for_customer=True,
        subject=subject,
        body=body,
        content_type="text/plain; charset=utf-8",
        to_address=to_address,
        cc=cc,
        channel="email",
        history_type_override="Forward",
    )
    return await add_article(
        session, ticket_id=ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
    )


async def bounce_article(
    session: AsyncSession,
    *,
    ticket_id: int,
    subject: str,
    body: str,
    content_type: str,
    to_address: str,
    user_id: int,
    sysconfig: SysConfig,
) -> int:
    """Bounce: resend the original article body verbatim, history type 'Bounce'.

    Faithful subset of Znuny's AgentTicketBounce — no RFC822 header
    preservation / true remail (no outbound MTA in this vertical); the body is
    resent unchanged to the new recipient.
    """
    article = ArticleIn(
        sender_type="agent",
        is_visible_for_customer=True,
        subject=subject,
        body=body,
        content_type=content_type,
        to_address=to_address,
        channel="email",
        history_type_override="Bounce",
    )
    return await add_article(
        session, ticket_id=ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
    )


async def _link_object_id(session: AsyncSession, name: str) -> int:
    """Resolve link_object.id by name, inserting the row if absent (Ticket)."""
    row = (
        await session.execute(
            text("SELECT id FROM link_object WHERE name = :n LIMIT 1"), {"n": name}
        )
    ).first()
    if row is not None:
        return int(row[0])
    await session.execute(text("INSERT INTO link_object (name) VALUES (:n)"), {"n": name})
    row = (
        await session.execute(
            text("SELECT id FROM link_object WHERE name = :n LIMIT 1"), {"n": name}
        )
    ).first()
    if row is None:
        raise RuntimeError("link_object insert read-back failed")
    return int(row[0])


async def _link_type_id(session: AsyncSession, name: str) -> int:
    row = (
        await session.execute(text("SELECT id FROM link_type WHERE name = :n LIMIT 1"), {"n": name})
    ).first()
    if row is None:
        raise InvalidInput(f"Unknown link type: {name}")
    return int(row[0])


async def _link_state_id(session: AsyncSession, name: str) -> int:
    row = (
        await session.execute(
            text("SELECT id FROM link_state WHERE name = :n LIMIT 1"), {"n": name}
        )
    ).first()
    if row is None:
        raise InvalidInput(f"Unknown link state: {name}")
    return int(row[0])


async def link_tickets(
    session: AsyncSession,
    *,
    source_ticket_id: int,
    target_ticket_id: int,
    link_type: str,
    user_id: int,
) -> None:
    """Insert a ticket↔ticket link_relation row (idempotent).

    Ports the ``link_relation`` write of Kernel/System/LinkObject.pm::LinkAdd
    (Ticket↔Ticket, state 'Valid'); resolves object/type/state ids by name.
    """
    obj_id = await _link_object_id(session, "Ticket")
    type_id = await _link_type_id(session, link_type)
    state_id = await _link_state_id(session, "Valid")
    existing = (
        await session.execute(
            text(
                "SELECT 1 FROM link_relation WHERE source_object_id = :o AND source_key = :sk"
                " AND target_object_id = :o AND target_key = :tk AND type_id = :t LIMIT 1"
            ),
            {"o": obj_id, "sk": str(source_ticket_id), "tk": str(target_ticket_id), "t": type_id},
        )
    ).first()
    if existing is not None:
        return
    await session.execute(
        text(
            "INSERT INTO link_relation (source_object_id, source_key, target_object_id,"
            " target_key, type_id, state_id, create_time, create_by)"
            " VALUES (:o, :sk, :o, :tk, :t, :s, current_timestamp, :uid)"
        ),
        {
            "o": obj_id,
            "sk": str(source_ticket_id),
            "tk": str(target_ticket_id),
            "t": type_id,
            "s": state_id,
            "uid": user_id,
        },
    )
    await _emit_event(session, "LinkAdd", source_ticket_id, {"target_ticket_id": target_ticket_id})


# ---------------------------------------------------------------------------
# Permission-aware write service (used by REST endpoints)
# ---------------------------------------------------------------------------


class TicketWriteService:
    """Session-scoped service for all ticket mutations, with permission checks."""

    def __init__(
        self,
        session: AsyncSession,
        session_factory: async_sessionmaker[AsyncSession],
        sysconfig: SysConfig,
        mail_sender: Any | None = None,
    ) -> None:
        self._session = session
        self._factory = session_factory
        self._sysconfig = sysconfig
        self._mail_sender = mail_sender
        self._perms = PermissionEngine(session)

    async def _assert_rw(self, user_id: int, queue_id: int) -> None:
        if not await self._perms.check(user_id, queue_id, "rw"):
            raise TicketAccessDenied(f"user {user_id} lacks rw on queue {queue_id}")

    async def _assert_create(self, user_id: int, queue_id: int) -> None:
        if not await self._perms.check(user_id, queue_id, "create"):
            raise TicketAccessDenied(f"user {user_id} lacks create on queue {queue_id}")

    def _resolve_mail_sender(self) -> Any:
        """Return the test/injectable sender override, or ``None``.

        When ``None``, :func:`deliver_agent_email_reply` builds an
        :class:`SmtpMailSender` from DB outbound settings (preferred) or
        env ``TIQORA_SMTP_*``.
        """
        return self._mail_sender

    async def create_ticket(self, user_id: int, params: TicketIn) -> int:
        await self._assert_create(user_id, params.queue_id)
        return await create_ticket(
            self._session,
            self._factory,
            self._sysconfig,
            params=params,
            user_id=user_id,
        )

    async def add_article(self, user_id: int, ticket_id: int, article: ArticleIn) -> int:
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        # Outgoing agent email: SMTP deliver then store (see outbound_reply).
        if article.channel.lower() == "email" and article.sender_type == "agent":
            from tiqora.channels.email.outbound_reply import deliver_agent_email_reply

            return await deliver_agent_email_reply(
                self._session,
                self._sysconfig,
                self._resolve_mail_sender(),
                ticket_id=ticket_id,
                queue_id=int(t["queue_id"]),
                user_id=user_id,
                article=article,
            )
        return await add_article(
            self._session,
            ticket_id=ticket_id,
            article=article,
            user_id=user_id,
            sysconfig=self._sysconfig,
        )

    async def move_queue(self, user_id: int, ticket_id: int, new_queue_id: int) -> None:
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        await move_queue(
            self._session,
            ticket_id=ticket_id,
            new_queue_id=new_queue_id,
            user_id=user_id,
            sysconfig=self._sysconfig,
        )

    async def change_state(
        self,
        user_id: int,
        ticket_id: int,
        new_state_id: int,
        pending_time: datetime | None = None,
    ) -> None:
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        await change_state(
            self._session,
            ticket_id=ticket_id,
            new_state_id=new_state_id,
            user_id=user_id,
            sysconfig=self._sysconfig,
            pending_time=pending_time,
        )

    async def merge_tickets(self, user_id: int, main_ticket_id: int, merge_ticket_id: int) -> None:
        main = await _ticket_must_exist(self._session, main_ticket_id)
        merge = await _ticket_must_exist(self._session, merge_ticket_id)
        await self._assert_rw(user_id, int(main["queue_id"]))
        await self._assert_rw(user_id, int(merge["queue_id"]))
        await merge_tickets(
            self._session,
            main_ticket_id=main_ticket_id,
            merge_ticket_id=merge_ticket_id,
            user_id=user_id,
            sysconfig=self._sysconfig,
        )

    async def forward_article(
        self,
        user_id: int,
        ticket_id: int,
        *,
        subject: str,
        body: str,
        to_address: str,
        cc: str | None = None,
    ) -> int:
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        return await forward_article(
            self._session,
            ticket_id=ticket_id,
            subject=subject,
            body=body,
            to_address=to_address,
            cc=cc,
            user_id=user_id,
            sysconfig=self._sysconfig,
        )

    async def bounce_article(
        self,
        user_id: int,
        ticket_id: int,
        article_id: int,
        *,
        to_address: str,
        state_id: int | None = None,
    ) -> int:
        """Resend the given article's body verbatim to ``to_address``."""
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        mime = (
            await self._session.execute(
                text(
                    "SELECT a_subject, a_body, a_content_type FROM article_data_mime"
                    " WHERE article_id = :aid LIMIT 1"
                ),
                {"aid": article_id},
            )
        ).first()
        if mime is None:
            raise TicketNotFound(f"article {article_id}")
        aid = await bounce_article(
            self._session,
            ticket_id=ticket_id,
            subject=str(mime[0] or ""),
            body=str(mime[1] or ""),
            content_type=str(mime[2] or "text/plain; charset=utf-8"),
            to_address=to_address,
            user_id=user_id,
            sysconfig=self._sysconfig,
        )
        if state_id is not None:
            await change_state(
                self._session,
                ticket_id=ticket_id,
                new_state_id=state_id,
                user_id=user_id,
                sysconfig=self._sysconfig,
            )
        return aid

    async def split_article(
        self,
        user_id: int,
        ticket_id: int,
        article_id: int,
        *,
        queue_id: int,
        title: str | None = None,
    ) -> int:
        """Create a new ticket seeded from an existing article; link the two.

        Returns the new ticket id. Requires ``rw`` on the source ticket and
        ``create`` on the target queue.
        """
        src = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(src["queue_id"]))
        await self._assert_create(user_id, queue_id)
        mime = (
            await self._session.execute(
                text(
                    "SELECT a_subject, a_body, a_content_type, a_from, a_to"
                    " FROM article_data_mime WHERE article_id = :aid LIMIT 1"
                ),
                {"aid": article_id},
            )
        ).first()
        if mime is None:
            raise TicketNotFound(f"article {article_id}")
        seeded = ArticleIn(
            sender_type="customer",
            is_visible_for_customer=True,
            subject=str(mime[0] or ""),
            body=str(mime[1] or ""),
            content_type=str(mime[2] or "text/plain; charset=utf-8"),
            from_address=mime[3],
            to_address=mime[4],
            channel="note",
        )
        new_title = title or str(src.get("title") or mime[0] or "Split ticket")
        new_ticket_id = await create_ticket(
            self._session,
            self._factory,
            self._sysconfig,
            params=TicketIn(
                title=new_title,
                queue_id=queue_id,
                state_id=int(src["ticket_state_id"]),
                priority_id=int(src["ticket_priority_id"]),
                owner_id=user_id,
                customer_id=src.get("customer_id"),
                customer_user_id=src.get("customer_user_id"),
                article=seeded,
            ),
            user_id=user_id,
        )
        await link_tickets(
            self._session,
            source_ticket_id=ticket_id,
            target_ticket_id=new_ticket_id,
            link_type="ParentChild",
            user_id=user_id,
        )
        return new_ticket_id

    async def link_tickets(
        self, user_id: int, ticket_id: int, target_ticket_id: int, link_type: str = "Normal"
    ) -> None:
        src = await _ticket_must_exist(self._session, ticket_id)
        tgt = await _ticket_must_exist(self._session, target_ticket_id)
        await self._assert_rw(user_id, int(src["queue_id"]))
        await self._assert_rw(user_id, int(tgt["queue_id"]))
        await link_tickets(
            self._session,
            source_ticket_id=ticket_id,
            target_ticket_id=target_ticket_id,
            link_type=link_type,
            user_id=user_id,
        )

    async def list_links(self, user_id: int, ticket_id: int) -> list[dict[str, Any]]:
        src = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(src["queue_id"]))
        obj_id = await _link_object_id(self._session, "Ticket")
        rows = (
            (
                await self._session.execute(
                    text(
                        "SELECT lr.source_key, lr.target_key, lt.name AS ltype, ls.name AS lstate"
                        " FROM link_relation lr"
                        " JOIN link_type lt ON lt.id = lr.type_id"
                        " JOIN link_state ls ON ls.id = lr.state_id"
                        " WHERE lr.source_object_id = :o AND lr.target_object_id = :o"
                        " AND (lr.source_key = :k OR lr.target_key = :k)"
                    ),
                    {"o": obj_id, "k": str(ticket_id)},
                )
            )
            .mappings()
            .all()
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            other = r["target_key"] if r["source_key"] == str(ticket_id) else r["source_key"]
            other_id = int(other)
            trow = (
                await self._session.execute(
                    text("SELECT tn, title FROM ticket WHERE id = :id LIMIT 1"),
                    {"id": other_id},
                )
            ).first()
            out.append(
                {
                    "source_key": r["source_key"],
                    "target_key": r["target_key"],
                    "link_type": r["ltype"],
                    "state": r["lstate"],
                    "other_ticket_id": other_id,
                    "other_tn": trow[0] if trow else None,
                    "other_title": trow[1] if trow else None,
                }
            )
        return out


__all__ = [
    "ArticleIn",
    "InvalidInput",
    "TicketAccessDenied",
    "TicketIn",
    "TicketNotFound",
    "TicketWriteService",
    "add_article",
    "archive_ticket",
    "assign_owner",
    "assign_responsible",
    "bounce_article",
    "change_priority",
    "change_state",
    "change_title",
    "create_ticket",
    "forward_article",
    "link_tickets",
    "lock_ticket",
    "merge_tickets",
    "move_queue",
    "set_customer",
    "unlock_ticket",
    "unwatch_ticket",
    "update_dynamic_field",
    "watch_ticket",
]
