"""Znuny-compatible ticket history helpers.

Behavioural port of ``Kernel/System/Ticket.pm::HistoryAdd`` and its call sites
(plus ``Kernel/System/DynamicField/ObjectType/Ticket.pm`` for
``TicketDynamicFieldUpdate``). The ``%%``-separated name strings are parsed by
Znuny (merge chains, first response, owner/move lists) and must match exactly.

Format strings verified against znuny-6.5.22 source:

======================  =====================================================
History type            Name format
======================  =====================================================
NewTicket               ``%%TN%%Queue%%Priority%%State%%TicketID``
StateUpdate             ``%%OldState%%NewState%%``
Move                    ``%%NewQueue%%NewQueueID%%OldQueue%%OldQueueID``
TitleUpdate             ``%%OldTitle%%NewTitle`` (new truncated to 50 + ``...``)
TypeUpdate              ``%%NewType%%NewTypeID%%OldType%%OldTypeID``
ServiceUpdate           ``%%NewService%%NewServiceID%%OldService%%OldServiceID``
SLAUpdate               ``%%NewSLA%%NewSLAID%%OldSLA%%OldSLAID``
PriorityUpdate          ``%%OldPrio%%OldPrioID%%NewPrio%%NewPrioID``
OwnerUpdate             ``%%NewUser%%NewUserID``
ResponsibleUpdate       ``%%NewUser%%NewUserID``
Lock / Unlock           ``%%lock`` / ``%%unlock`` (the raw lock string)
CustomerUpdate          ``%%CustomerID=X;CustomerUser=Y;`` (only set parts)
SetPendingTime          ``%%YYYY-MM-DD HH:MM`` (each part %02d)
Subscribe/Unsubscribe   ``%%UserFullname``
ArchiveFlagUpdate       ``%%y`` / ``%%n``
TicketDynamicFieldUpdate ``%%FieldName%%N%%Value%%V%%OldValue%%OV``
======================  =====================================================

Snapshot semantics (HistoryAdd): any of queue_id/type_id/owner_id/priority_id/
state_id not supplied by the caller is read from the ticket's **current** row;
``type_id`` falls back to 1 when NULL (Znuny default ticket type).
Name is truncated to 200 chars. ``create_by = change_by = user_id``.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# History type names as stored in ticket_history_type.name (Znuny seed data).
TYPE_NEW_TICKET: Final = "NewTicket"
TYPE_STATE_UPDATE: Final = "StateUpdate"
TYPE_MOVE: Final = "Move"
TYPE_TITLE_UPDATE: Final = "TitleUpdate"
TYPE_TYPE_UPDATE: Final = "TypeUpdate"
TYPE_SERVICE_UPDATE: Final = "ServiceUpdate"
TYPE_SLA_UPDATE: Final = "SLAUpdate"
TYPE_PRIORITY_UPDATE: Final = "PriorityUpdate"
TYPE_OWNER_UPDATE: Final = "OwnerUpdate"
TYPE_RESPONSIBLE_UPDATE: Final = "ResponsibleUpdate"
TYPE_LOCK: Final = "Lock"
TYPE_UNLOCK: Final = "Unlock"
TYPE_CUSTOMER_UPDATE: Final = "CustomerUpdate"
TYPE_SET_PENDING_TIME: Final = "SetPendingTime"
TYPE_SUBSCRIBE: Final = "Subscribe"
TYPE_UNSUBSCRIBE: Final = "Unsubscribe"
TYPE_DYNAMIC_FIELD_UPDATE: Final = "TicketDynamicFieldUpdate"
TYPE_ARCHIVE_FLAG_UPDATE: Final = "ArchiveFlagUpdate"
TYPE_MERGED: Final = "Merged"
TYPE_MISC: Final = "Misc"
TYPE_SEND_ANSWER: Final = "SendAnswer"
TYPE_EMAIL_AGENT: Final = "EmailAgent"
TYPE_EMAIL_CUSTOMER: Final = "EmailCustomer"
TYPE_PHONE_CALL_AGENT: Final = "PhoneCallAgent"
TYPE_PHONE_CALL_CUSTOMER: Final = "PhoneCallCustomer"
TYPE_ADD_NOTE: Final = "AddNote"
TYPE_FOLLOW_UP: Final = "FollowUp"
TYPE_WEB_REQUEST_CUSTOMER: Final = "WebRequestCustomer"

# Process-level cache: ticket_history_type.name → id. Znuny caches these too;
# refreshing requires a process restart (types are effectively immutable).
_history_type_cache: dict[str, int] = {}


def clear_history_type_cache() -> None:
    """Drop the process-level history-type id cache (mainly for tests)."""
    _history_type_cache.clear()


async def resolve_history_type_id(session: AsyncSession, name: str) -> int:
    """Resolve ``ticket_history_type.id`` by name (cached for process lifetime)."""
    cached = _history_type_cache.get(name)
    if cached is not None:
        return cached
    row = (
        await session.execute(
            text("SELECT id FROM ticket_history_type WHERE name = :name LIMIT 1"),
            {"name": name},
        )
    ).first()
    if row is None:
        raise ValueError(f"Unknown ticket history type: {name!r}")
    type_id = int(row[0])
    _history_type_cache[name] = type_id
    return type_id


async def _ticket_snapshot(session: AsyncSession, ticket_id: int) -> tuple[int, int, int, int, int]:
    """Return (queue_id, type_id, owner_id, priority_id, state_id) from the ticket row.

    ``type_id`` falls back to 1 when NULL (Znuny default ticket type; the
    ``ticket_history.type_id`` column is NOT NULL).
    """
    row = (
        await session.execute(
            text(
                "SELECT queue_id, type_id, user_id, ticket_priority_id, ticket_state_id"
                " FROM ticket WHERE id = :tid LIMIT 1"
            ),
            {"tid": ticket_id},
        )
    ).first()
    if row is None:
        raise ValueError(f"Ticket {ticket_id} not found")
    return (
        int(row[0]),
        int(row[1]) if row[1] is not None else 1,
        int(row[2]),
        int(row[3]),
        int(row[4]),
    )


async def history_add(
    session: AsyncSession,
    *,
    ticket_id: int,
    history_type: str,
    name: str,
    user_id: int,
    article_id: int | None = None,
    queue_id: int | None = None,
    type_id: int | None = None,
    owner_id: int | None = None,
    priority_id: int | None = None,
    state_id: int | None = None,
) -> None:
    """Write one ``ticket_history`` row (port of ``Ticket.pm::HistoryAdd``)."""
    name = name[:200]

    if None in (queue_id, type_id, owner_id, priority_id, state_id):
        snap = await _ticket_snapshot(session, ticket_id)
        queue_id = queue_id if queue_id is not None else snap[0]
        type_id = type_id if type_id is not None else snap[1]
        owner_id = owner_id if owner_id is not None else snap[2]
        priority_id = priority_id if priority_id is not None else snap[3]
        state_id = state_id if state_id is not None else snap[4]

    history_type_id = await resolve_history_type_id(session, history_type)

    await session.execute(
        text(
            "INSERT INTO ticket_history"
            " (name, history_type_id, ticket_id, article_id, queue_id, owner_id,"
            "  priority_id, state_id, type_id, create_time, create_by, change_time, change_by)"
            " VALUES (:name, :htid, :tid, :aid, :qid, :oid, :pid, :sid, :typid,"
            "  current_timestamp, :uid, current_timestamp, :uid)"
        ),
        {
            "name": name,
            "htid": history_type_id,
            "tid": ticket_id,
            "aid": article_id,
            "qid": queue_id,
            "oid": owner_id,
            "pid": priority_id,
            "sid": state_id,
            "typid": type_id,
            "uid": user_id,
        },
    )


# ---------------------------------------------------------------------------
# Typed helpers — exact name formats from the Znuny call sites
# ---------------------------------------------------------------------------


async def add_new_ticket(
    session: AsyncSession,
    *,
    ticket_id: int,
    tn: str,
    queue: str,
    priority: str,
    state: str,
    user_id: int,
    queue_id: int | None = None,
) -> None:
    """NewTicket (TicketCreate): ``%%TN%%Queue%%Priority%%State%%TicketID``."""
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_NEW_TICKET,
        name=f"%%{tn}%%{queue}%%{priority}%%{state}%%{ticket_id}",
        user_id=user_id,
        queue_id=queue_id,
    )


async def add_state_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    old_state: str,
    new_state: str,
    user_id: int,
    article_id: int | None = None,
    queue_id: int | None = None,
    state_id: int | None = None,
) -> None:
    """StateUpdate (TicketStateSet): ``%%OldState%%NewState%%``."""
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_STATE_UPDATE,
        name=f"%%{old_state}%%{new_state}%%",
        user_id=user_id,
        article_id=article_id,
        queue_id=queue_id,
        state_id=state_id,
    )


async def add_move(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_queue: str,
    new_queue_id: int,
    old_queue: str,
    old_queue_id: int,
    user_id: int,
) -> None:
    """Move (TicketQueueSet): ``%%NewQueue%%NewQueueID%%OldQueue%%OldQueueID``."""
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_MOVE,
        name=f"%%{new_queue}%%{new_queue_id}%%{old_queue}%%{old_queue_id}",
        user_id=user_id,
        queue_id=new_queue_id,
    )


async def add_title_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    old_title: str,
    new_title: str,
    user_id: int,
) -> None:
    """TitleUpdate: ``%%OldTitle%%NewTitle`` — new title truncated to 50 + ``...``."""
    truncated = new_title[:50]
    if len(new_title) > 50:
        truncated += "..."
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_TITLE_UPDATE,
        name=f"%%{old_title}%%{truncated}",
        user_id=user_id,
    )


async def add_type_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_type: str,
    new_type_id: int | str,
    old_type: str,
    old_type_id: int | str,
    user_id: int,
) -> None:
    """TypeUpdate: ``%%NewType%%NewTypeID%%OldType%%OldTypeID``.

    Znuny uses ``'NULL'`` for missing type names and ``''`` for missing ids.
    """
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_TYPE_UPDATE,
        name=f"%%{new_type}%%{new_type_id}%%{old_type}%%{old_type_id}",
        user_id=user_id,
    )


async def add_service_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_service: str,
    new_service_id: int | str,
    old_service: str,
    old_service_id: int | str,
    user_id: int,
) -> None:
    """ServiceUpdate: ``%%NewService%%NewServiceID%%OldService%%OldServiceID``.

    On ticket create Znuny writes ``%%Service%%ServiceID%%NULL%%`` (old = NULL/'').
    """
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_SERVICE_UPDATE,
        name=f"%%{new_service}%%{new_service_id}%%{old_service}%%{old_service_id}",
        user_id=user_id,
    )


async def add_sla_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_sla: str,
    new_sla_id: int | str,
    old_sla: str,
    old_sla_id: int | str,
    user_id: int,
) -> None:
    """SLAUpdate: ``%%NewSLA%%NewSLAID%%OldSLA%%OldSLAID``."""
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_SLA_UPDATE,
        name=f"%%{new_sla}%%{new_sla_id}%%{old_sla}%%{old_sla_id}",
        user_id=user_id,
    )


async def add_priority_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    old_priority: str,
    old_priority_id: int,
    new_priority: str,
    new_priority_id: int,
    user_id: int,
    queue_id: int | None = None,
) -> None:
    """PriorityUpdate: ``%%OldPriority%%OldPriorityID%%NewPriority%%NewPriorityID``."""
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_PRIORITY_UPDATE,
        name=f"%%{old_priority}%%{old_priority_id}%%{new_priority}%%{new_priority_id}",
        user_id=user_id,
        queue_id=queue_id,
    )


async def add_owner_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_user: str,
    new_user_id: int,
    user_id: int,
) -> None:
    """OwnerUpdate: ``%%NewUser%%NewUserID`` (new_user is the login)."""
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_OWNER_UPDATE,
        name=f"%%{new_user}%%{new_user_id}",
        user_id=user_id,
    )


async def add_responsible_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_user: str,
    new_user_id: int,
    user_id: int,
) -> None:
    """ResponsibleUpdate: ``%%NewUser%%NewUserID``."""
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_RESPONSIBLE_UPDATE,
        name=f"%%{new_user}%%{new_user_id}",
        user_id=user_id,
    )


async def add_lock(
    session: AsyncSession,
    *,
    ticket_id: int,
    lock: str,
    user_id: int,
) -> None:
    """Lock/Unlock (TicketLockSet): ``%%lock`` / ``%%unlock``.

    History type is chosen from the lock string like Znuny does.
    """
    lock_lower = lock.lower()
    if lock_lower == "unlock":
        history_type = TYPE_UNLOCK
    elif lock_lower == "lock":
        history_type = TYPE_LOCK
    else:
        history_type = TYPE_MISC
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=history_type,
        name=f"%%{lock}",
        user_id=user_id,
    )


async def add_customer_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    customer_id: str | None,
    customer_user: str | None,
    user_id: int,
) -> None:
    """CustomerUpdate (TicketCustomerSet): ``%%CustomerID=X;CustomerUser=Y;``.

    Only the parts that were actually updated appear (Znuny builds the string
    incrementally per updated column).
    """
    history = ""
    if customer_id is not None:
        history += f"CustomerID={customer_id};"
    if customer_user is not None:
        history += f"CustomerUser={customer_user};"
    if not history:
        return
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_CUSTOMER_UPDATE,
        name=f"%%{history}",
        user_id=user_id,
    )


async def add_pending_time(
    session: AsyncSession,
    *,
    ticket_id: int,
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    user_id: int,
) -> None:
    """SetPendingTime (TicketPendingTimeSet): ``%%YYYY-MM-DD HH:MM`` (all %02d)."""
    name = f"%%{year:02d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_SET_PENDING_TIME,
        name=name,
        user_id=user_id,
    )


async def add_subscribe(
    session: AsyncSession,
    *,
    ticket_id: int,
    user_fullname: str,
    user_id: int,
) -> None:
    """Subscribe (TicketWatchSubscribe / watcher add): ``%%UserFullname``."""
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_SUBSCRIBE,
        name=f"%%{user_fullname}",
        user_id=user_id,
    )


async def add_unsubscribe(
    session: AsyncSession,
    *,
    ticket_id: int,
    user_fullname: str,
    user_id: int,
) -> None:
    """Unsubscribe (TicketWatchUnsubscribe / watcher delete): ``%%UserFullname``."""
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_UNSUBSCRIBE,
        name=f"%%{user_fullname}",
        user_id=user_id,
    )


async def add_dynamic_field_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    field_name: str,
    value: str,
    old_value: str,
    user_id: int,
    queue_id: int | None = None,
) -> None:
    """TicketDynamicFieldUpdate: ``%%FieldName%%N%%Value%%V%%OldValue%%OV``.

    From ``Kernel/System/DynamicField/ObjectType/Ticket.pm``; empty values are
    passed as ``''`` (Perl ``//= ''``).
    """
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_DYNAMIC_FIELD_UPDATE,
        name=f"%%FieldName%%{field_name}%%Value%%{value}%%OldValue%%{old_value}",
        user_id=user_id,
        queue_id=queue_id,
    )


async def add_archive_flag_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    archive_flag: str,
    user_id: int,
) -> None:
    """ArchiveFlagUpdate (TicketArchiveFlagSet): ``%%y`` / ``%%n``."""
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_ARCHIVE_FLAG_UPDATE,
        name=f"%%{archive_flag}",
        user_id=user_id,
    )


async def add_article_history(
    session: AsyncSession,
    *,
    ticket_id: int,
    article_id: int,
    history_type: str,
    name: str,
    user_id: int,
) -> None:
    """Article-linked history (SendAnswer, EmailAgent, EmailCustomer,
    PhoneCallAgent, PhoneCallCustomer, AddNote, FollowUp, WebRequestCustomer…).

    Znuny article backends pass ``HistoryComment`` as the name (free text, e.g.
    a truncated subject); there is no fixed ``%%`` format for these types.
    """
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=history_type,
        name=name,
        user_id=user_id,
        article_id=article_id,
    )


async def add_merged(
    session: AsyncSession,
    *,
    ticket_id: int,
    main_tn: str,
    main_ticket_id: int,
    merge_tn: str,
    merge_ticket_id: int,
    user_id: int,
) -> None:
    """Merged (TicketMerge): ``%%MergeTN%%MergeTicketID%%MainTN%%MainTicketID``.

    Written on the merged (source) ticket; Znuny's merge-chain walker parses
    this exact format.
    """
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_MERGED,
        name=f"%%{merge_tn}%%{merge_ticket_id}%%{main_tn}%%{main_ticket_id}",
        user_id=user_id,
    )


__all__ = [
    "TYPE_ADD_NOTE",
    "TYPE_ARCHIVE_FLAG_UPDATE",
    "TYPE_CUSTOMER_UPDATE",
    "TYPE_DYNAMIC_FIELD_UPDATE",
    "TYPE_EMAIL_AGENT",
    "TYPE_EMAIL_CUSTOMER",
    "TYPE_FOLLOW_UP",
    "TYPE_LOCK",
    "TYPE_MERGED",
    "TYPE_MISC",
    "TYPE_MOVE",
    "TYPE_NEW_TICKET",
    "TYPE_OWNER_UPDATE",
    "TYPE_PHONE_CALL_AGENT",
    "TYPE_PHONE_CALL_CUSTOMER",
    "TYPE_PRIORITY_UPDATE",
    "TYPE_RESPONSIBLE_UPDATE",
    "TYPE_SEND_ANSWER",
    "TYPE_SERVICE_UPDATE",
    "TYPE_SET_PENDING_TIME",
    "TYPE_SLA_UPDATE",
    "TYPE_STATE_UPDATE",
    "TYPE_SUBSCRIBE",
    "TYPE_TITLE_UPDATE",
    "TYPE_TYPE_UPDATE",
    "TYPE_UNLOCK",
    "TYPE_UNSUBSCRIBE",
    "TYPE_WEB_REQUEST_CUSTOMER",
    "add_archive_flag_update",
    "add_article_history",
    "add_customer_update",
    "add_dynamic_field_update",
    "add_lock",
    "add_merged",
    "add_move",
    "add_new_ticket",
    "add_owner_update",
    "add_pending_time",
    "add_priority_update",
    "add_responsible_update",
    "add_service_update",
    "add_sla_update",
    "add_state_update",
    "add_subscribe",
    "add_title_update",
    "add_type_update",
    "add_unsubscribe",
    "clear_history_type_cache",
    "history_add",
]
