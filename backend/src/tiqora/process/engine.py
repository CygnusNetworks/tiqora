"""ProcessManagement (BPM) execution engine: transitions, actions, activity dialogs.

This is the "write" half of ``tiqora.process`` — subtask 1 (``config.py``,
``graph.py``, ``ticket_state.py``) is read-only parsing/resolution; this
module starts processes, evaluates transition conditions, dispatches
TransitionActions, and drives Activity Dialog submission.

History fidelity — READ THIS FIRST
-----------------------------------
Znuny does **not** have a dedicated "ProcessManagement" ticket_history type.
When AgentTicketProcess starts a process it sets the two well-known Dynamic
Fields (``ProcessManagementProcessID``/``ProcessManagementActivityID``) via
the normal ``DynamicFieldValueSet``, which writes an ordinary
``TicketDynamicFieldUpdate`` history row — nothing process-specific. When a
transition fires, each ``TransitionAction`` module (``TicketStateSet``,
``TicketQueueSet``, ...) just calls the corresponding normal
``Kernel::System::Ticket`` setter (``TicketStateSet``, ``TicketQueueSet``,
...), which writes its own ordinary history row (``StateUpdate``, ``Move``,
...) exactly as if an agent had made that change by hand.

Tiqora mirrors this exactly: every mutation below is done by calling the
existing ``tiqora.domain.ticket_write_service`` module functions
(``move_queue``, ``change_state``, ``update_dynamic_field``, ...) — the same
functions the REST ticket-mutation endpoints use — which already write the
correct Znuny-shaped history rows. This module never writes a
``ticket_history`` row directly, and does **not** invent a synthetic
"ProcessManagement" history type. That is intentional fidelity to Znuny's
actual behaviour, not a gap.

Session/commit convention
--------------------------
Every function in this module only ``session.flush()``es (via the reused
``ticket_write_service`` functions, which themselves never commit) — the
caller owns the transaction and must ``await session.commit()``, matching
the convention documented at the top of ``ticket_write_service.py`` and used
by ``calendar/service.py``.

Deferred/unsupported scope (documented, not silently missing)
---------------------------------------------------------------
- Condition type ``Module`` (custom Perl module) is treated as non-matching
  with a logged warning — see ``conditions.py``. Ordered comparisons
  (``GreaterThan`` / ``GreaterThanOrEqual`` / ``LessThan`` /
  ``LessThanOrEqual``) are implemented per Znuny
  ``TransitionValidation::Base``.
- TransitionAction modules other than the implemented handlers (see
  ``_ACTION_HANDLERS``) — remaining deferred: ``ExecuteInvoker``,
  ``Appointment*`` (Create/Update/Remove), ``ConfigItemUpdate``.
  Unsupported modules are logged and no-op'd, and collected into
  ``ActivityDialogSubmitResult.unsupported_actions``.
- Activity Dialog ``PendingTimeDiff`` is not a dialog field (a transition action's own
  ``PendingTimeDiff`` on ``TicketStateSet`` is honoured).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.channels.email.placeholder import PlaceholderContext
from tiqora.db.legacy.queue import Queue, Service, Sla
from tiqora.db.legacy.ticket import TicketPriority, TicketState, TicketType
from tiqora.db.legacy.user import Users
from tiqora.domain.dynamic_field_values import load_ticket_dynamic_field
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    TicketIn,
    _state_type_name,  # noqa: PLC2701
    _ticket_must_exist,  # noqa: PLC2701
    add_article,
    assign_owner,
    assign_responsible,
    change_priority,
    change_service,
    change_sla,
    change_state,
    change_title,
    change_type,
    create_ticket,
    link_tickets,
    lock_ticket,
    move_queue,
    set_customer,
    unlock_ticket,
    update_dynamic_field,
    watch_ticket,
)
from tiqora.process.actor import ProcessActor
from tiqora.process.availability import RUNNING_STATES, assert_can_submit
from tiqora.process.conditions import (
    ConditionConfigError,
    ConditionData,
    as_integer,
    check_transition,
    evaluate_transition,
    get_condition_data,
    resolve_transition_tags,
    value_validate,
)
from tiqora.process.config import ActivityDialogConfig, TransitionActionConfig, TransitionConfig
from tiqora.process.exceptions import (
    ActivityDialogNotFound,
    ProcessNotFound,
    ProcessPermissionDenied,
    RequiredFieldMissing,
    TicketAlreadyInProcess,
    TicketNotInProcess,
    UnresolvedFieldValue,
)
from tiqora.process.fields import (
    canonical_field_name,
    dialog_field_order,
    effective_field_values,
    is_present,
    missing_required_fields,
)
from tiqora.process.graph import ProcessRepository
from tiqora.process.smart_tags import (
    build_tag_context,
    render_tags,
    replace_action_config,
    split_scalar_list,
)
from tiqora.process.ticket_state import (
    ACTIVITY_ID_DF_NAME,
    PROCESS_ID_DF_NAME,
    TicketProcessCandidates,
    get_ticket_process_state,
)
from tiqora.znuny.cache_invalidation import invalidate_ticket_cache
from tiqora.znuny.history import add_pending_time
from tiqora.znuny.sysconfig import SysConfig

logger = logging.getLogger(__name__)

__all__ = [
    "ActivityDialogSubmitResult",
    "TransitionOutcome",
    "apply_activity_dialog",
    "evaluate_transition",
    "execute_transition_action",
    "get_ticket_attrs",
    "run_transitions",
    "start_process",
    "submit_activity_dialog",
]


# ---------------------------------------------------------------------------
# 1. start_process
# ---------------------------------------------------------------------------


async def start_process(
    session: AsyncSession,
    *,
    ticket_id: int,
    process_entity_id: str,
    user_id: int,
    sysconfig: SysConfig,  # noqa: ARG001 -- kept for signature symmetry with the other engine entry points
    repository: ProcessRepository | None = None,
) -> None:
    """Start *process_entity_id* on *ticket_id* at its ``StartActivity``.

    Sets ``DynamicField_ProcessManagementProcessID`` and
    ``DynamicField_ProcessManagementActivityID`` via ``update_dynamic_field``
    — each call already writes the correct ``TicketDynamicFieldUpdate``
    history row, so no additional history handling is needed here.

    Raises :class:`ProcessNotFound` if the process (or its ``StartActivity``)
    does not exist, and :class:`TicketAlreadyInProcess` if the ticket already
    has process Dynamic Field values set — Znuny's AgentTicketProcess does
    not offer starting a second process on a ticket already in one; this is
    a deliberate, documented simplification (not re-verified against a live
    Znuny instance for this port, but consistent with the single-process
    Dynamic Field pair modelling the ticket <-> process link).
    """
    repository = repository or ProcessRepository(session)
    graph = await repository.get_process(process_entity_id)
    if graph is None or graph.config.start_activity is None:
        raise ProcessNotFound(process_entity_id)

    existing_state = await get_ticket_process_state(session, ticket_id)
    if existing_state is not None:
        raise TicketAlreadyInProcess(ticket_id)

    await update_dynamic_field(
        session,
        ticket_id=ticket_id,
        field_name=PROCESS_ID_DF_NAME,
        values=[process_entity_id],
        user_id=user_id,
    )
    await update_dynamic_field(
        session,
        ticket_id=ticket_id,
        field_name=ACTIVITY_ID_DF_NAME,
        values=[graph.config.start_activity],
        user_id=user_id,
    )


# ---------------------------------------------------------------------------
# 2 & 3. condition data + transition evaluation (see ``conditions.py``)
# ---------------------------------------------------------------------------

# DynamicFieldPendingTimeSet offset: ``1d 5h 12m 500s`` (parts optional).
_OFFSET_RE = re.compile(
    r"^(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s\s*)?$",
    re.IGNORECASE,
)


async def get_ticket_attrs(session: AsyncSession, ticket_id: int) -> dict[str, str]:
    """Flat ``{name: str}`` view of :func:`get_condition_data` (lists joined with ``", "``,
    unset values ``""``) for the actions that only need strings."""
    data = await get_condition_data(session, ticket_id)
    return {
        name: ", ".join(value) if isinstance(value, list) else (value or "")
        for name, value in data.items()
    }


# ---------------------------------------------------------------------------
# 4. TransitionAction dispatch
# ---------------------------------------------------------------------------


async def _resolve_queue_id(session: AsyncSession, name: str | None, id_: int | None) -> int | None:
    if id_ is not None:
        return id_
    if not name:
        return None
    return (
        await session.execute(select(Queue.id).where(Queue.name == name, Queue.valid_id == 1))
    ).scalar_one_or_none()


async def _resolve_state_id(session: AsyncSession, name: str | None, id_: int | None) -> int | None:
    if id_ is not None:
        return id_
    if not name:
        return None
    return (
        await session.execute(select(TicketState.id).where(TicketState.name == name))
    ).scalar_one_or_none()


async def _resolve_priority_id(
    session: AsyncSession, name: str | None, id_: int | None
) -> int | None:
    if id_ is not None:
        return id_
    if not name:
        return None
    return (
        await session.execute(select(TicketPriority.id).where(TicketPriority.name == name))
    ).scalar_one_or_none()


async def _resolve_user_id(session: AsyncSession, login: str | None, id_: int | None) -> int | None:
    if id_ is not None:
        return id_
    if not login:
        return None
    return (
        await session.execute(select(Users.id).where(Users.login == login, Users.valid_id == 1))
    ).scalar_one_or_none()


async def _resolve_type_id(session: AsyncSession, name: str | None, id_: int | None) -> int | None:
    if id_ is not None:
        return id_
    if not name:
        return None
    return (
        await session.execute(
            select(TicketType.id).where(TicketType.name == name, TicketType.valid_id == 1)
        )
    ).scalar_one_or_none()


async def _resolve_service_id(
    session: AsyncSession, name: str | None, id_: int | None
) -> int | None:
    if id_ is not None:
        return id_
    if not name:
        return None
    return (
        await session.execute(select(Service.id).where(Service.name == name, Service.valid_id == 1))
    ).scalar_one_or_none()


async def _resolve_sla_id(session: AsyncSession, name: str | None, id_: int | None) -> int | None:
    if id_ is not None:
        return id_
    if not name:
        return None
    return (
        await session.execute(select(Sla.id).where(Sla.name == name, Sla.valid_id == 1))
    ).scalar_one_or_none()


async def _action_ticket_state_set(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    state_id = await _resolve_state_id(session, config.get("State"), config.get("StateID"))
    if state_id is None:
        raise RequiredFieldMissing(
            "TicketStateSet: Config must set 'State' or 'StateID' to a known state"
        )
    pending_time: datetime | None = None
    diff = config.get("PendingTimeDiff")
    if diff is not None:
        pending_time = datetime.now(UTC) + timedelta(seconds=int(diff))
    await change_state(
        session,
        ticket_id=ticket_id,
        new_state_id=int(state_id),
        user_id=user_id,
        sysconfig=sysconfig,
        pending_time=pending_time,
    )


async def _action_ticket_queue_set(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    queue_id = await _resolve_queue_id(session, config.get("Queue"), config.get("QueueID"))
    if queue_id is None:
        raise RequiredFieldMissing(
            "TicketQueueSet: Config must set 'Queue' or 'QueueID' to a known queue"
        )
    await move_queue(
        session,
        ticket_id=ticket_id,
        new_queue_id=int(queue_id),
        user_id=user_id,
        sysconfig=sysconfig,
    )


async def _action_ticket_owner_set(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    owner_id = await _resolve_user_id(session, config.get("Owner"), config.get("OwnerID"))
    if owner_id is None:
        raise RequiredFieldMissing(
            "TicketOwnerSet: Config must set 'Owner' or 'OwnerID' to a known user"
        )
    await assign_owner(
        session,
        ticket_id=ticket_id,
        new_owner_id=int(owner_id),
        user_id=user_id,
        sysconfig=sysconfig,
    )


async def _action_ticket_priority_set(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    priority_id = await _resolve_priority_id(
        session, config.get("Priority"), config.get("PriorityID")
    )
    if priority_id is None:
        raise RequiredFieldMissing(
            "TicketPrioritySet: Config must set 'Priority' or 'PriorityID' to a known priority"
        )
    await change_priority(
        session,
        ticket_id=ticket_id,
        new_priority_id=int(priority_id),
        user_id=user_id,
        sysconfig=sysconfig,
    )


async def _action_ticket_title_set(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    del sysconfig
    title = config.get("Title")
    if not title:
        raise RequiredFieldMissing("TicketTitleSet: Config must set 'Title'")
    await change_title(session, ticket_id=ticket_id, new_title=str(title), user_id=user_id)


async def _action_ticket_customer_set(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    del sysconfig
    customer_id = config.get("CustomerID", config.get("No"))
    customer_user_id = config.get("CustomerUserID", config.get("User"))
    await set_customer(
        session,
        ticket_id=ticket_id,
        customer_id=customer_id,
        customer_user_id=customer_user_id,
        user_id=user_id,
    )


async def _action_ticket_responsible_set(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    del sysconfig
    responsible_id = await _resolve_user_id(
        session, config.get("Responsible"), config.get("ResponsibleID")
    )
    if responsible_id is None:
        raise RequiredFieldMissing(
            "TicketResponsibleSet: Config must set 'Responsible' or 'ResponsibleID' to a known user"
        )
    await assign_responsible(
        session, ticket_id=ticket_id, new_responsible_id=int(responsible_id), user_id=user_id
    )


async def _action_ticket_lock_set(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    lock = config.get("Lock")
    if lock is None:
        lock_id = config.get("LockID")
        if lock_id is None:
            raise RequiredFieldMissing("TicketLockSet: Config must set 'Lock' or 'LockID'")
        # Znuny convention used throughout ticket_write_service: 1=unlock, 2=lock.
        lock = "lock" if int(lock_id) == 2 else "unlock"
    if str(lock).lower() == "lock":
        await lock_ticket(session, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig)
    else:
        await unlock_ticket(session, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig)


async def _action_dynamic_field_set(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Port of ``DynamicFieldSet.pm``: every ``Config`` key (other than the
    reserved ``UserID`` override key) is a dynamic field name => value pair.
    A field literally named ``UserID`` must be configured as
    ``DynamicField_UserID`` to disambiguate — same convention Znuny documents.
    """
    del sysconfig
    for key, value in config.items():
        if key == "UserID":
            continue
        field_name = key.removeprefix("DynamicField_")
        values = value if isinstance(value, list) else [value]
        str_values = [str(v) for v in values]
        if isinstance(value, str) and value:
            # DynamicFieldSet.pm:132-143 (bug#14900): a Multiselect value written as one
            # string ("a, b" -- also what a dynamic-field smart tag yields) is a comma list.
            field = await load_ticket_dynamic_field(session, field_name)
            if field is not None and field.field_type == "Multiselect":
                str_values = split_scalar_list(value)
        await update_dynamic_field(
            session, ticket_id=ticket_id, field_name=field_name, values=str_values, user_id=user_id
        )


async def _action_dynamic_field_remove(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Port of DynamicFieldRemove — clear field values (empty set)."""
    del sysconfig
    for key in config:
        if key == "UserID":
            continue
        field_name = key.removeprefix("DynamicField_")
        await update_dynamic_field(
            session, ticket_id=ticket_id, field_name=field_name, values=[], user_id=user_id
        )


async def _action_dynamic_field_increment(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Port of DynamicFieldIncrement — numeric field += Value (default 1)."""
    del sysconfig
    from sqlalchemy import text

    for key, value in config.items():
        if key == "UserID":
            continue
        field_name = key.removeprefix("DynamicField_")
        delta = int(value) if value not in (None, "") else 1
        field_id = (
            await session.execute(
                text(
                    "SELECT id FROM dynamic_field WHERE name = :n"
                    " AND object_type = 'Ticket' AND valid_id = 1 LIMIT 1"
                ),
                {"n": field_name},
            )
        ).scalar_one_or_none()
        if field_id is None:
            continue
        row = (
            await session.execute(
                text(
                    "SELECT value_text, value_int FROM dynamic_field_value"
                    " WHERE field_id = :fid AND object_id = :oid ORDER BY id LIMIT 1"
                ),
                {"fid": field_id, "oid": ticket_id},
            )
        ).first()
        current = 0
        if row is not None:
            if row[1] is not None:
                current = int(row[1])
            elif row[0] is not None:
                try:
                    current = int(str(row[0]).strip() or "0")
                except ValueError:
                    current = 0
        await update_dynamic_field(
            session,
            ticket_id=ticket_id,
            field_name=field_name,
            values=[str(current + delta)],
            user_id=user_id,
        )


async def _action_article_send(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Port of ArticleSend — agent email article via outbound reply path."""
    from tiqora.channels.email.outbound_reply import deliver_agent_email_reply

    subject = str(config.get("Subject") or "Notification")
    body = str(config.get("Body") or "")
    if not body and not config.get("Subject"):
        raise RequiredFieldMissing("ArticleSend: Config must set Body or Subject")
    t = await _ticket_must_exist(session, ticket_id)
    article = ArticleIn(
        sender_type="agent",
        is_visible_for_customer=bool(config.get("IsVisibleForCustomer", 1)),
        subject=subject,
        body=body,
        channel="email",
        to_address=str(config.get("To") or config.get("Customer") or "") or None,
    )
    await deliver_agent_email_reply(
        session,
        sysconfig,
        None,
        ticket_id=ticket_id,
        queue_id=int(t["queue_id"]),
        user_id=user_id,
        article=article,
    )


_ARTICLE_CHANNEL_MAP: dict[str, str] = {
    "internal": "note",
    "note": "note",
    "phone": "phone",
    "email": "email",
    "sms": "sms",
    "whatsapp": "whatsapp",
}


async def _action_ticket_article_create(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    sender_type = config.get("SenderType")
    if not sender_type:
        raise RequiredFieldMissing("TicketArticleCreate: Config must set 'SenderType'")
    channel_raw = str(config.get("CommunicationChannel") or "Internal").lower()
    channel = _ARTICLE_CHANNEL_MAP.get(channel_raw, "note")
    article = ArticleIn(
        sender_type=str(sender_type),
        is_visible_for_customer=bool(config.get("IsVisibleForCustomer", 0)),
        subject=str(config.get("Subject", "")),
        body=str(config.get("Body", "")),
        channel=channel,
    )
    await add_article(
        session, ticket_id=ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
    )


async def _action_ticket_type_set(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    type_id = await _resolve_type_id(session, config.get("Type"), config.get("TypeID"))
    if type_id is None:
        raise RequiredFieldMissing(
            "TicketTypeSet: Config must set 'Type' or 'TypeID' to a known type"
        )
    await change_type(
        session,
        ticket_id=ticket_id,
        new_type_id=int(type_id),
        user_id=user_id,
        sysconfig=sysconfig,
    )


async def _action_ticket_service_set(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    service_id = await _resolve_service_id(session, config.get("Service"), config.get("ServiceID"))
    if service_id is None and config.get("Service") is None and config.get("ServiceID") is None:
        raise RequiredFieldMissing("TicketServiceSet: Config must set 'Service' or 'ServiceID'")
    await change_service(
        session,
        ticket_id=ticket_id,
        new_service_id=int(service_id) if service_id is not None else None,
        user_id=user_id,
        sysconfig=sysconfig,
    )


async def _action_ticket_sla_set(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    sla_id = await _resolve_sla_id(session, config.get("SLA"), config.get("SLAID"))
    if sla_id is None and config.get("SLA") is None and config.get("SLAID") is None:
        raise RequiredFieldMissing("TicketSLASet: Config must set 'SLA' or 'SLAID'")
    await change_sla(
        session,
        ticket_id=ticket_id,
        new_sla_id=int(sla_id) if sla_id is not None else None,
        user_id=user_id,
        sysconfig=sysconfig,
    )


async def _action_ticket_watch_set(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    del sysconfig
    watcher_id = await _resolve_user_id(session, config.get("User"), config.get("UserID"))
    if watcher_id is None:
        watcher_id = user_id
    await watch_ticket(
        session, ticket_id=ticket_id, watcher_user_id=int(watcher_id), user_id=user_id
    )


async def _action_link_add(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    del sysconfig
    target = config.get("TargetTicketID") or config.get("TicketID")
    if target is None:
        raise RequiredFieldMissing("LinkAdd: Config must set 'TargetTicketID' or 'TicketID'")
    link_type = str(config.get("Type") or config.get("LinkType") or "Normal")
    await link_tickets(
        session,
        source_ticket_id=ticket_id,
        target_ticket_id=int(target),
        link_type=link_type,
        user_id=user_id,
    )


def _offset_to_seconds(offset: Any) -> int:
    """Port of DynamicFieldPendingTimeSet::_Offset2Seconds (``1d 5h 12m 500s``)."""
    if offset is None or offset == "":
        return 0
    raw = str(offset).strip()
    m = _OFFSET_RE.match(raw)
    if not m or not raw:
        return 0
    days, hours, minutes, seconds = (int(x or 0) for x in m.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


async def _set_ticket_pending_time(
    session: AsyncSession,
    *,
    ticket_id: int,
    pending_time: datetime,
    user_id: int,
) -> None:
    """Set ``ticket.until_time`` + ``SetPendingTime`` history (TicketPendingTimeSet)."""
    if pending_time.tzinfo is None:
        pending_time = pending_time.replace(tzinfo=UTC)
    until_time = int(pending_time.timestamp())
    await session.execute(
        text(
            "UPDATE ticket SET until_time = :ut,"
            " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
        ),
        {"ut": until_time, "uid": user_id, "tid": ticket_id},
    )
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
    await invalidate_ticket_cache(session, ticket_id)


async def _action_dynamic_field_pending_time_set(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Port of DynamicFieldPendingTimeSet — pending time from a DF (+ optional Offset).

    Config keys: ``DynamicField`` (required name without prefix), ``Offset``
    (optional ``1d 5h 12m 500s``), ``State``/``StateID`` (optional state change
    before setting pending time). Empty/missing DF value is a silent no-op
    (Znuny returns early).
    """
    field = config.get("DynamicField")
    if not field:
        raise RequiredFieldMissing("DynamicFieldPendingTimeSet: Config must set 'DynamicField'")
    attrs = await get_ticket_attrs(session, ticket_id)
    raw = attrs.get(f"DynamicField_{field}", "") or attrs.get(str(field), "")
    if not raw:
        return
    validated = value_validate(str(raw).strip())
    # After ValueValidate, datetimes are epoch ints; plain strings that are
    # already epoch also work via _as_integer.
    epoch = as_integer(validated)
    if epoch is None:
        # Try a final parse for values ValueValidate did not rewrite.
        try:
            epoch = int(datetime.fromisoformat(str(raw).replace(" ", "T")).timestamp())
        except (TypeError, ValueError):
            logger.warning("DynamicFieldPendingTimeSet: cannot parse DF %r value %r", field, raw)
            return
    pending = datetime.fromtimestamp(epoch + _offset_to_seconds(config.get("Offset")), tz=UTC)

    state_id = await _resolve_state_id(session, config.get("State"), config.get("StateID"))
    if state_id is not None:
        # Znuny: TicketStateSet then TicketPendingTimeSet separately.
        await change_state(
            session,
            ticket_id=ticket_id,
            new_state_id=int(state_id),
            user_id=user_id,
            sysconfig=sysconfig,
            pending_time=None,
        )
    await _set_ticket_pending_time(
        session, ticket_id=ticket_id, pending_time=pending, user_id=user_id
    )


def _session_factory_from(session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    """Build a short-lived session factory from an open session's bind.

    ``create_ticket`` needs a factory for ``ticket_create_number`` (separate
    short transactions on the counter table); process actions only receive the
    caller's session.
    """
    from sqlalchemy.ext.asyncio import AsyncEngine

    raw = session.get_bind()
    engine: AsyncEngine
    if isinstance(raw, AsyncEngine):
        engine = raw
    else:
        maybe = getattr(raw, "engine", None)
        if not isinstance(maybe, AsyncEngine):
            raise RuntimeError("TicketCreate requires an AsyncEngine-bound session")
        engine = maybe
    return async_sessionmaker(engine, expire_on_commit=False)


async def _resolve_link_as(session: AsyncSession, link_as: str) -> tuple[str, str] | None:
    """Map Znuny ``LinkAs`` (SourceName/TargetName) → (link_type_name, direction).

    Direction is ``Source`` when the *new* ticket is the link source (parent
    ticket is target), or ``Target`` when the original process ticket is the
    source. Tiqora's ``link_type`` table only stores the type name; Znuny's
    SourceName/TargetName come from SysConfig. Convention used here matches
    Znuny defaults: ``Normal``/``Normal``, ``Parent``/``Child`` for
    ``ParentChild``.
    """
    la = link_as.strip()
    if not la:
        return None
    # Direct type-name match (e.g. LinkAs=Normal).
    row = (
        await session.execute(
            text("SELECT name FROM link_type WHERE name = :n AND valid_id = 1 LIMIT 1"),
            {"n": la},
        )
    ).first()
    if row is not None:
        return str(row[0]), "Source"
    aliases: dict[str, tuple[str, str]] = {
        "parent": ("ParentChild", "Source"),
        "child": ("ParentChild", "Target"),
    }
    return aliases.get(la.lower())


async def _action_ticket_create(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Port of TransitionAction::TicketCreate — spawn a linked sibling ticket.

    Config keys (subset of Znuny): Title, Queue/QueueID, State/StateID,
    Priority/PriorityID, CustomerID, CustomerUser/CustomerUserID, Owner/OwnerID,
    Body, Subject, SenderType, IsVisibleForCustomer, CommunicationChannel,
    LinkAs (optional link to the process ticket), DynamicField_* values.
    Defaults for Queue/State/Lock/Priority come from Process::Default* sysconfig
    keys when neither name nor ID is set.
    """
    title = config.get("Title")
    if not title:
        # Znuny TicketCreate requires Title; fail closed rather than invent one.
        raise RequiredFieldMissing("TicketCreate: Config must set 'Title'")

    queue_id = await _resolve_queue_id(session, config.get("Queue"), config.get("QueueID"))
    if queue_id is None:
        default_q = await sysconfig.get_str("Process::DefaultQueue", "Raw")
        queue_id = await _resolve_queue_id(session, default_q, None)
    if queue_id is None:
        raise RequiredFieldMissing(
            "TicketCreate: Config must set 'Queue' or 'QueueID' to a known queue"
        )

    state_id = await _resolve_state_id(session, config.get("State"), config.get("StateID"))
    if state_id is None:
        default_s = await sysconfig.get_str("Process::DefaultState", "new")
        state_id = await _resolve_state_id(session, default_s, None)
    if state_id is None:
        raise RequiredFieldMissing(
            "TicketCreate: Config must set 'State' or 'StateID' to a known state"
        )

    priority_id = await _resolve_priority_id(
        session, config.get("Priority"), config.get("PriorityID")
    )
    if priority_id is None:
        default_p = await sysconfig.get_str("Process::DefaultPriority", "3 normal")
        priority_id = await _resolve_priority_id(session, default_p, None)
    if priority_id is None:
        raise RequiredFieldMissing(
            "TicketCreate: Config must set 'Priority' or 'PriorityID' to a known priority"
        )

    owner_id = await _resolve_user_id(session, config.get("Owner"), config.get("OwnerID"))
    if owner_id is None:
        owner_id = user_id

    responsible_id = await _resolve_user_id(
        session, config.get("Responsible"), config.get("ResponsibleID")
    )
    type_id = await _resolve_type_id(session, config.get("Type"), config.get("TypeID"))
    service_id = await _resolve_service_id(session, config.get("Service"), config.get("ServiceID"))
    sla_id = await _resolve_sla_id(session, config.get("SLA"), config.get("SLAID"))

    customer_id = config.get("CustomerID")
    customer_user_id = config.get("CustomerUserID", config.get("CustomerUser"))
    if customer_id is not None:
        customer_id = str(customer_id)
    if customer_user_id is not None:
        customer_user_id = str(customer_user_id)

    lock_raw = config.get("Lock")
    lock_id = 1  # unlock
    if lock_raw is not None and str(lock_raw).lower() == "lock":
        lock_id = 2
    elif config.get("LockID") is not None:
        lock_id = int(config["LockID"])

    archive_flag = 0
    if str(config.get("ArchiveFlag") or "").lower() in ("y", "1", "true"):
        archive_flag = 1

    dynamic_fields: dict[str, list[str]] = {}
    for key, value in config.items():
        if not str(key).startswith("DynamicField_"):
            continue
        fname = str(key).removeprefix("DynamicField_")
        values = value if isinstance(value, list) else [value]
        dynamic_fields[fname] = [str(v) for v in values]

    # Article only when SenderType + IsVisibleForCustomer are both present
    # (Znuny TicketCreate.pm gate).
    article: ArticleIn | None = None
    if config.get("SenderType") is not None and config.get("IsVisibleForCustomer") is not None:
        channel_raw = str(config.get("CommunicationChannel") or "Internal").lower()
        channel = _ARTICLE_CHANNEL_MAP.get(channel_raw, "note")
        article = ArticleIn(
            sender_type=str(config["SenderType"]),
            is_visible_for_customer=bool(config.get("IsVisibleForCustomer")),
            subject=str(config.get("Subject") or title),
            body=str(config.get("Body") or ""),
            channel=channel,
        )

    params = TicketIn(
        title=str(title)[:255],
        queue_id=int(queue_id),
        state_id=int(state_id),
        priority_id=int(priority_id),
        owner_id=int(owner_id),
        lock_id=lock_id,
        type_id=int(type_id) if type_id is not None else None,
        service_id=int(service_id) if service_id is not None else None,
        sla_id=int(sla_id) if sla_id is not None else None,
        responsible_id=int(responsible_id) if responsible_id is not None else None,
        customer_id=customer_id,
        customer_user_id=customer_user_id,
        archive_flag=archive_flag,
        dynamic_fields=dynamic_fields,
        article=article,
    )

    factory = _session_factory_from(session)
    new_ticket_id = await create_ticket(
        session,
        factory,
        sysconfig,
        params=params,
        user_id=user_id,
    )

    # Optional pending time on the *new* ticket (Znuny PendingTime / PendingTimeDiff).
    pending_time: datetime | None = None
    if config.get("PendingTime"):
        validated = value_validate(str(config["PendingTime"]).strip())
        epoch = as_integer(validated)
        if epoch is not None:
            pending_time = datetime.fromtimestamp(epoch, tz=UTC)
    elif config.get("PendingTimeDiff") is not None:
        pending_time = datetime.now(UTC) + timedelta(seconds=int(config["PendingTimeDiff"]))
    if pending_time is not None:
        await _set_ticket_pending_time(
            session, ticket_id=new_ticket_id, pending_time=pending_time, user_id=user_id
        )

    link_as = config.get("LinkAs")
    if link_as:
        resolved = await _resolve_link_as(session, str(link_as))
        if resolved is None:
            logger.warning("TicketCreate: LinkAs %r is invalid; skipping link", link_as)
        else:
            link_type, direction = resolved
            if direction == "Source":
                source_id, target_id = new_ticket_id, ticket_id
            else:
                source_id, target_id = ticket_id, new_ticket_id
            await link_tickets(
                session,
                source_ticket_id=source_id,
                target_ticket_id=target_id,
                link_type=link_type,
                user_id=user_id,
            )


_ActionHandler = Callable[[AsyncSession, dict[str, Any], int, int, SysConfig], Awaitable[None]]

_ACTION_HANDLERS: dict[str, _ActionHandler] = {
    "TicketStateSet": _action_ticket_state_set,
    "TicketQueueSet": _action_ticket_queue_set,
    "TicketOwnerSet": _action_ticket_owner_set,
    "TicketPrioritySet": _action_ticket_priority_set,
    "TicketTitleSet": _action_ticket_title_set,
    "TicketCustomerSet": _action_ticket_customer_set,
    "TicketResponsibleSet": _action_ticket_responsible_set,
    "TicketLockSet": _action_ticket_lock_set,
    "DynamicFieldSet": _action_dynamic_field_set,
    "DynamicFieldRemove": _action_dynamic_field_remove,
    "DynamicFieldIncrement": _action_dynamic_field_increment,
    "DynamicFieldPendingTimeSet": _action_dynamic_field_pending_time_set,
    "TicketArticleCreate": _action_ticket_article_create,
    "ArticleSend": _action_article_send,
    "TicketTypeSet": _action_ticket_type_set,
    "TicketServiceSet": _action_ticket_service_set,
    "TicketSLASet": _action_ticket_sla_set,
    "TicketWatchSet": _action_ticket_watch_set,
    "LinkAdd": _action_link_add,
    "TicketCreate": _action_ticket_create,
}
"""Implemented TransitionAction modules, keyed by the last ``::``-segment of
``TransitionActionConfig.module``. Remaining deferred modules include
``ExecuteInvoker``, ``Appointment*``, ``ConfigItemUpdate`` — see
:func:`execute_transition_action`."""


def _module_short_name(module: str) -> str:
    """Last segment of a Perl-style ``Kernel::System::...::Foo`` module path."""
    if "::" in module:
        return module.rsplit("::", 1)[-1]
    if "." in module:
        return module.rsplit(".", 1)[-1]
    return module


async def execute_transition_action(
    session: AsyncSession,
    *,
    action: TransitionActionConfig,
    ticket_id: int,
    process_entity_id: str,
    activity_entity_id: str,
    transition_entity_id: str,
    user_id: int,
    sysconfig: SysConfig,
) -> str | None:
    """Dispatch and run one TransitionAction.

    Returns the module's short name if it is unsupported/deferred (a
    warning is also logged) so callers (``submit_activity_dialog``) can
    collect skipped actions into ``ActivityDialogSubmitResult.unsupported_actions``.
    Returns ``None`` on successful dispatch.

    ``process_entity_id``/``activity_entity_id``/``transition_entity_id`` are
    accepted for parity with Znuny's ``TransitionAction::Run()`` signature
    (which passes them to every action module) but are not currently
    consulted by any of the implemented handlers.

    The handler receives a copy of ``action.config`` with the smart tags replaced
    (:func:`tiqora.process.smart_tags.replace_action_config`, Znuny's
    ``_ReplaceTicketAttributes`` + ``_ReplaceAdditionalAttributes``); ``action`` itself is never
    mutated.
    """
    del process_entity_id, activity_entity_id, transition_entity_id  # see docstring
    module_name = _module_short_name(action.module)
    handler = _ACTION_HANDLERS.get(module_name)
    if handler is None:
        logger.warning("unsupported ProcessManagement TransitionAction module: %s", action.module)
        return module_name
    config = await replace_action_config(
        action.config, session=session, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig
    )
    await handler(session, config, ticket_id, user_id, sysconfig)
    return None


# ---------------------------------------------------------------------------
# 5. submit_activity_dialog
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivityDialogSubmitResult:
    """Outcome of one :func:`submit_activity_dialog` call."""

    activity_changed: bool
    new_activity_entity_id: str | None
    unsupported_actions: list[str]
    transition_entity_id: str | None


async def _resolve_dialog_field_id(
    session: AsyncSession,
    *,
    field: str,
    kind: str,
    raw: Any,
    resolver: Callable[[AsyncSession, str | None, int | None], Awaitable[int | None]],
) -> int | None:
    """Resolve one submitted activity-dialog field value to an entity id.

    Returns ``None`` when *raw* is blank — the field was left empty and must
    be skipped, mirroring the old ``if <id> is not None`` guards so an
    optional field the agent never filled does not abort the whole submit.
    (The frontend seeds every declared field with ``""``, so blank optional
    fields reach here on essentially every submit.)

    A non-blank value that resolves neither by name nor as a numeric id
    raises :class:`UnresolvedFieldValue` (mapped to HTTP 400 by the caller).
    """
    text_value = str(raw).strip()
    if not text_value:
        return None
    resolved = await resolver(session, text_value, None)
    if resolved is None and text_value.isdigit():
        resolved = int(text_value)
    if resolved is None:
        raise UnresolvedFieldValue(
            f"activity dialog field {field!r}: could not resolve {raw!r} to a {kind}"
        )
    return resolved


async def _apply_dialog_field_changes(
    session: AsyncSession,
    *,
    dialog_config: ActivityDialogConfig,
    field_values: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
    sender_type: str = "agent",
) -> None:
    """Apply submitted field_values to the ticket, per the field names the
    dialog config declares. Only fields actually present (and non-blank) in
    ``field_values`` are applied — the required/optional check already ran in
    the caller (:func:`apply_activity_dialog`).
    """
    if "Queue" in field_values:
        queue_id = await _resolve_dialog_field_id(
            session,
            field="Queue",
            kind="queue id",
            raw=field_values["Queue"],
            resolver=_resolve_queue_id,
        )
        if queue_id is not None:
            await move_queue(
                session,
                ticket_id=ticket_id,
                new_queue_id=queue_id,
                user_id=user_id,
                sysconfig=sysconfig,
            )

    if "State" in field_values:
        state_id = await _resolve_dialog_field_id(
            session,
            field="State",
            kind="state id",
            raw=field_values["State"],
            resolver=_resolve_state_id,
        )
        if state_id is not None:
            await change_state(
                session,
                ticket_id=ticket_id,
                new_state_id=state_id,
                user_id=user_id,
                sysconfig=sysconfig,
            )

    if "Priority" in field_values:
        priority_id = await _resolve_dialog_field_id(
            session,
            field="Priority",
            kind="priority id",
            raw=field_values["Priority"],
            resolver=_resolve_priority_id,
        )
        if priority_id is not None:
            await change_priority(
                session,
                ticket_id=ticket_id,
                new_priority_id=priority_id,
                user_id=user_id,
                sysconfig=sysconfig,
            )

    if "Type" in field_values:
        type_id = await _resolve_dialog_field_id(
            session,
            field="Type",
            kind="type id",
            raw=field_values["Type"],
            resolver=_resolve_type_id,
        )
        if type_id is not None:
            await change_type(
                session,
                ticket_id=ticket_id,
                new_type_id=type_id,
                user_id=user_id,
                sysconfig=sysconfig,
            )

    if "Service" in field_values:
        service_id = await _resolve_dialog_field_id(
            session,
            field="Service",
            kind="service id",
            raw=field_values["Service"],
            resolver=_resolve_service_id,
        )
        if service_id is not None:
            await change_service(
                session,
                ticket_id=ticket_id,
                new_service_id=service_id,
                user_id=user_id,
                sysconfig=sysconfig,
            )

    if "SLA" in field_values:
        sla_id = await _resolve_dialog_field_id(
            session,
            field="SLA",
            kind="sla id",
            raw=field_values["SLA"],
            resolver=_resolve_sla_id,
        )
        if sla_id is not None:
            await change_sla(
                session,
                ticket_id=ticket_id,
                new_sla_id=sla_id,
                user_id=user_id,
                sysconfig=sysconfig,
            )

    if "Title" in field_values and field_values["Title"]:
        await change_title(
            session, ticket_id=ticket_id, new_title=str(field_values["Title"]), user_id=user_id
        )

    if "Responsible" in field_values:
        responsible_id = await _resolve_dialog_field_id(
            session,
            field="Responsible",
            kind="user id",
            raw=field_values["Responsible"],
            resolver=_resolve_user_id,
        )
        if responsible_id is not None:
            await assign_responsible(
                session, ticket_id=ticket_id, new_responsible_id=responsible_id, user_id=user_id
            )

    if "Lock" in field_values:
        lock_raw = str(field_values["Lock"]).strip().lower()
        if lock_raw in {"lock", "2"}:
            await lock_ticket(session, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig)
        elif lock_raw in {"unlock", "1"}:
            await unlock_ticket(session, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig)
        elif lock_raw:
            raise UnresolvedFieldValue(
                f"activity dialog field 'Lock': could not resolve {field_values['Lock']!r} "
                "to a lock (expected lock/unlock or lock id 1/2)"
            )

    if "CustomerID" in field_values or "CustomerUserID" in field_values:
        t = await _ticket_must_exist(session, ticket_id)
        customer_id = field_values.get("CustomerID", t.get("customer_id"))
        customer_user_id = field_values.get("CustomerUserID", t.get("customer_user_id"))
        await set_customer(
            session,
            ticket_id=ticket_id,
            customer_id=customer_id,
            customer_user_id=customer_user_id,
            user_id=user_id,
        )

    # Dynamic fields and the article are stored in FieldOrder (the Znuny save loop,
    # AgentTicketProcess.pm:5463-5700); a dynamic field the order does not list comes last.
    order = dialog_field_order(dialog_config)
    dynamic = [k for k in field_values if k.startswith("DynamicField_")]
    dynamic.sort(key=lambda k: order.index(k) if k in order else len(order))
    steps = [n for n in order if n == "Article" or n in dynamic]
    steps += [k for k in dynamic if k not in steps]
    for step in steps:
        if step == "Article":
            await _store_dialog_article(
                session,
                dialog_config=dialog_config,
                field_values=field_values,
                ticket_id=ticket_id,
                user_id=user_id,
                sysconfig=sysconfig,
                sender_type=sender_type,
            )
            continue
        value = field_values[step]
        values = value if isinstance(value, list) else [value]
        await update_dynamic_field(
            session,
            ticket_id=ticket_id,
            field_name=step.removeprefix("DynamicField_"),
            values=[str(v) for v in values],
            user_id=user_id,
        )


async def _store_dialog_article(
    session: AsyncSession,
    *,
    dialog_config: ActivityDialogConfig,
    field_values: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
    sender_type: str,
) -> None:
    """Article (AgentTicketProcess.pm:5576, 5686-5687): stored only when Subject AND Body are
    set; sender, visibility and channel come from the dialog config, never from the request."""
    article_cfg = next(
        (c for n, c in dialog_config.fields.items() if canonical_field_name(n) == "Article"), None
    )
    if (
        article_cfg is None
        or not is_present(field_values.get("Subject"))
        or not is_present(field_values.get("Body"))
    ):
        return
    cfg = article_cfg.config
    channel_raw = str(cfg.get("CommunicationChannel") or cfg.get("ArticleType") or "note").lower()
    article = ArticleIn(
        sender_type=sender_type,
        is_visible_for_customer=str(cfg.get("IsVisibleForCustomer", "0")) == "1",
        subject=str(field_values["Subject"]),
        body=str(field_values["Body"]),
        channel=_ARTICLE_CHANNEL_MAP.get(channel_raw, "note"),
    )
    await add_article(
        session, ticket_id=ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
    )


async def _ensure_required_lock(
    session: AsyncSession, *, ticket_id: int, user_id: int, sysconfig: SysConfig
) -> None:
    """Dialog ``RequiredLock`` (AgentTicketProcess.pm:182-224).

    Locked ticket: only its owner may continue (``OwnerCheck``), otherwise denied.
    Unlocked ticket: ``TicketLockSet lock``, then ``TicketOwnerSet`` only if the owner differs.
    """
    t = await _ticket_must_exist(session, ticket_id)
    if int(t["ticket_lock_id"]) != 1:  # 1 = unlock
        if int(t["user_id"]) != user_id:
            raise ProcessPermissionDenied("Das Ticket ist von einer anderen Person gesperrt.")
        return
    await lock_ticket(session, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig)
    if int(t["user_id"]) != user_id:
        await assign_owner(
            session,
            ticket_id=ticket_id,
            new_owner_id=user_id,
            user_id=user_id,
            sysconfig=sysconfig,
        )


async def _existing_state_id(session: AsyncSession, id_: int) -> int | None:
    return (
        await session.execute(select(TicketState.id).where(TicketState.id == id_))
    ).scalar_one_or_none()


def _dialog_default(dialog: ActivityDialogConfig, field_name: str) -> str:
    """DefaultValue of the field spelled exactly *field_name* (``State`` is not ``StateID``)."""
    cfg = dialog.fields.get(field_name)
    raw = cfg.default_value if cfg is not None else None
    return str(raw).strip() if raw is not None else ""


async def _dialog_target_state_id(
    session: AsyncSession,
    *,
    ticket_id: int,
    dialog: ActivityDialogConfig,
    values: dict[str, Any],
) -> int | None:
    """State the ticket ends in, for deciding about PendingTime (AgentTicketProcess.pm:1262-1297).

    Znuny's order: the submitted state (``StateGet ID``; a number counts only when that state
    exists), fallback 1 the ``State`` field's DefaultValue (by name), fallback 2 the ``StateID``
    field's DefaultValue (as an id, :1279-1284), fallback 3 the ticket's current state.
    """
    submitted = values.get("State")
    text_value = str(submitted).strip() if submitted is not None else ""
    if text_value:
        resolved = await _resolve_state_id(session, text_value, None)
        if resolved is None and text_value.isdigit():
            resolved = await _existing_state_id(session, int(text_value))
        if resolved is not None:
            return resolved
    default_name = _dialog_default(dialog, "State")
    if default_name:
        resolved = await _resolve_state_id(session, default_name, None)
        if resolved is not None:
            return resolved
    default_id = _dialog_default(dialog, "StateID")
    if default_id.isdigit():
        resolved = await _existing_state_id(session, int(default_id))
        if resolved is not None:
            return resolved
    ticket = await _ticket_must_exist(session, ticket_id)
    return int(ticket["ticket_state_id"])


async def _resolve_dialog_pending_time(
    session: AsyncSession,
    *,
    ticket_id: int,
    dialog: ActivityDialogConfig,
    values: dict[str, Any],
) -> tuple[bool, datetime | None]:
    """Dialog ``PendingTime`` (D8): ``(target_is_pending, UTC minute to store)``.

    Znuny reads the field only when the target state's type starts with ``pending``
    (AgentTicketProcess.pm:1299-1303, case-insensitive) and stores it with
    ``TicketPendingTimeSet`` from Year..Minute — seconds are dropped and ``:00`` is used
    (Ticket.pm:4033-4040). The value is an ISO 8601 string (naive = UTC), the same contract as
    ``PATCH /tickets/{id}``. A pending target without a value returns ``(True, None)``, so the
    caller can enforce ``Display: 2``; a non-pending target is ``(False, None)`` and the value
    is ignored.
    """
    state_id = await _dialog_target_state_id(
        session, ticket_id=ticket_id, dialog=dialog, values=values
    )
    if state_id is None or not (await _state_type_name(session, state_id)).lower().startswith(
        "pending"
    ):
        return False, None
    raw = values.get("PendingTime")
    if not is_present(raw):
        return True, None
    try:
        if not isinstance(raw, str):
            raise ValueError("not a string")
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise UnresolvedFieldValue(
            f"activity dialog field 'PendingTime': could not parse {raw!r} as an ISO 8601 datetime"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return True, parsed.astimezone(UTC).replace(second=0, microsecond=0)


async def apply_activity_dialog(
    session: AsyncSession,
    *,
    ticket_id: int,
    dialog: ActivityDialogConfig,
    field_values: dict[str, Any],
    actor: ProcessActor,
    sysconfig: SysConfig,
    skip_fields: frozenset[str] = frozenset(),
    apply_required_lock: bool = True,
) -> None:
    """Validate and store one activity dialog submission with Znuny's field semantics.

    ``skip_fields`` (canonical names) are validated but not written again — used when a field
    was already consumed by ``create_ticket``. ``apply_required_lock=False`` for new tickets
    (Znuny never runs RequiredLock on create).
    """
    values = effective_field_values(dialog, field_values, actor.interface)
    customer_filled = actor.interface == "CustomerInterface"
    if customer_filled:
        # The customer interface never asks for the customer, it is the acting customer
        # (CustomerTicketProcess.pm:3560-3569, 3675-3676); whatever was submitted is ignored.
        values["CustomerUserID"] = actor.customer_user_login
        values["CustomerID"] = actor.customer_id
    missing = missing_required_fields(dialog, values, actor.interface)
    # PendingTime is only required (and only read) when the target state is a pending one.
    pending_required = "PendingTime" in missing
    missing = [name for name in missing if name != "PendingTime"]
    pending_time: datetime | None = None
    if "PendingTime" in values or pending_required:
        target_is_pending, pending_time = await _resolve_dialog_pending_time(
            session, ticket_id=ticket_id, dialog=dialog, values=values
        )
        if target_is_pending and pending_time is None and pending_required:
            missing.append("PendingTime")
    if missing:
        raise RequiredFieldMissing(f"required activity dialog field missing: {', '.join(missing)}")
    if customer_filled:
        # The ticket already carries this customer; do not rewrite it.
        values.pop("CustomerUserID")
        values.pop("CustomerID")
    if apply_required_lock and dialog.required_lock == "1" and actor.interface == "AgentInterface":
        await _ensure_required_lock(
            session, ticket_id=ticket_id, user_id=actor.user_id, sysconfig=sysconfig
        )
    await _apply_dialog_field_changes(
        session,
        dialog_config=dialog,
        field_values={k: v for k, v in values.items() if k not in skip_fields},
        ticket_id=ticket_id,
        user_id=actor.user_id,
        sysconfig=sysconfig,
        sender_type="customer" if actor.interface == "CustomerInterface" else "agent",
    )
    if pending_time is not None:
        # AgentTicketProcess.pm:5554-5575: TicketPendingTimeSet after the other fields.
        await _set_ticket_pending_time(
            session, ticket_id=ticket_id, pending_time=pending_time, user_id=actor.user_id
        )
    # The owner is set last, after the whole save loop (AgentTicketProcess.pm:5967).
    owner_id = (
        await _resolve_dialog_field_id(
            session,
            field="Owner",
            kind="user id",
            raw=values["Owner"],
            resolver=_resolve_user_id,
        )
        if "Owner" in values and "Owner" not in skip_fields
        else None
    )
    if owner_id is not None:
        await assign_owner(
            session,
            ticket_id=ticket_id,
            new_owner_id=owner_id,
            user_id=actor.user_id,
            sysconfig=sysconfig,
        )


@dataclass(frozen=True)
class TransitionOutcome:
    """Result of one :func:`run_transitions` pass."""

    activity_changed: bool
    new_activity_entity_id: str | None
    transition_entity_id: str | None
    unsupported_actions: list[str]


_NO_TRANSITION = TransitionOutcome(False, None, None, [])


async def run_transitions(
    session: AsyncSession,
    *,
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
    repository: ProcessRepository | None = None,
) -> TransitionOutcome:
    """Run the first matching transition of the ticket's current activity.

    Znuny's ``TicketProcessTransitions`` event runs only for Active and FadeAway processes
    (``Process.pm:395-402``), so any other state is a no-op. Transitions are checked in
    ``cmp`` order of their entity ids (``Process.pm:442-445``); ``Data`` is a fresh
    ``TicketGet`` (:func:`get_condition_data`) and smart tags in Base-type conditions are
    rendered with the ticket's placeholder context.
    """
    state = await get_ticket_process_state(session, ticket_id)
    if state is None:
        return _NO_TRANSITION
    repository = repository or ProcessRepository(session)
    graph = await repository.get_process(state.process_entity_id)
    if graph is None or graph.state_entity_id not in RUNNING_STATES:
        return _NO_TRANSITION
    activity = graph.activities.get(state.activity_entity_id)
    if activity is None:
        return _NO_TRANSITION

    data: ConditionData = await get_condition_data(session, ticket_id)
    tag_ctx: list[PlaceholderContext] = []  # built on the first tag only

    async def _render(text_: str) -> str:
        if not tag_ctx:
            tag_ctx.append(
                await build_tag_context(
                    session,
                    ticket_id=ticket_id,
                    user_id=user_id,
                    sysconfig=sysconfig,
                    data=data,
                    for_condition=True,
                )
            )
        return await render_tags(text_, ctx=tag_ctx[0], sysconfig=sysconfig)

    tags = await resolve_transition_tags(
        TransitionConfig(
            conditions=[b for t in activity.outgoing_transitions for b in t.config.conditions]
        ),
        data,
        render=_render,
    )
    for transition in sorted(activity.outgoing_transitions, key=lambda t: t.entity_id):
        try:
            if not check_transition(transition.config, data, tags=tags):
                continue
        except ConditionConfigError as exc:
            # Znuny's bare ``return`` leaves TransitionCheck: nothing fires (Transition.pm:322).
            logger.error("transition %s: %s; no transition runs", transition.entity_id, exc)
            return _NO_TRANSITION
        # The new activity is stored BEFORE the actions run (Process.pm:501, actions from :558),
        # so the history shows the activity update first.
        await update_dynamic_field(
            session,
            ticket_id=ticket_id,
            field_name=ACTIVITY_ID_DF_NAME,
            values=[transition.target_activity_entity_id],
            user_id=user_id,
        )
        unsupported: list[str] = []
        for action_node in transition.actions:
            skipped = await execute_transition_action(
                session,
                action=action_node.config,
                ticket_id=ticket_id,
                process_entity_id=graph.entity_id,
                activity_entity_id=activity.entity_id,
                transition_entity_id=transition.entity_id,
                user_id=user_id,
                sysconfig=sysconfig,
            )
            if skipped is not None:
                unsupported.append(skipped)
        return TransitionOutcome(
            activity_changed=True,
            new_activity_entity_id=transition.target_activity_entity_id,
            transition_entity_id=transition.entity_id,
            unsupported_actions=unsupported,
        )
    return _NO_TRANSITION


async def submit_activity_dialog(
    session: AsyncSession,
    *,
    ticket_id: int,
    activity_dialog_entity_id: str,
    field_values: dict[str, Any],
    user_id: int,
    sysconfig: SysConfig,
    actor: ProcessActor | None = None,
    repository: ProcessRepository | None = None,
) -> ActivityDialogSubmitResult:
    """Submit an Activity Dialog for *ticket_id*: apply field changes, then
    evaluate the current activity's outgoing transitions in listed (YAML
    insertion) order and, for the first one that matches, run its
    TransitionActions and move the ticket to its target activity.

    Structurally mirrors Znuny's ``AgentTicketProcess.pm`` submit flow
    (validate dialog -> apply fields -> ``_TransitionDo`` -> advance
    activity) — a faithful-behaviour port, not a line-by-line one; the
    exact Perl call order need not be byte-identical (documented per
    subtask instructions).

    If no transition matches, the ticket stays on its current activity —
    valid Znuny behaviour (a dialog can be submitted without advancing).
    """
    state = await get_ticket_process_state(session, ticket_id)
    if state is None:
        raise TicketNotInProcess(ticket_id)

    repository = repository or ProcessRepository(session)
    graph = await repository.get_process(state.process_entity_id)
    if graph is None:
        raise ProcessNotFound(state.process_entity_id)

    activity = graph.activities.get(state.activity_entity_id)
    if activity is None:
        raise TicketNotInProcess(ticket_id)

    if all(d.entity_id != activity_dialog_entity_id for d in activity.activity_dialogs) and (
        await repository.get_activity_dialog(activity_dialog_entity_id) is None
    ):
        raise ActivityDialogNotFound(activity_dialog_entity_id)

    actor = actor or ProcessActor.agent(user_id)
    # Process state, dialog on the current activity, Interface, Permission (agents) and the
    # Action / ActivityDialog ACLs (availability.py; AgentTicketProcess.pm:140-177).
    dialog_node = await assert_can_submit(
        session,
        actor,
        ticket_id=ticket_id,
        candidates=TicketProcessCandidates(
            process=graph,
            activity_entity_id=activity.entity_id,
            activity_dialogs=activity.activity_dialogs,
            outgoing_transitions=activity.outgoing_transitions,
        ),
        activity_dialog_entity_id=activity_dialog_entity_id,
    )
    dialog_config = dialog_node.config

    await apply_activity_dialog(
        session,
        ticket_id=ticket_id,
        dialog=dialog_config,
        field_values=field_values,
        actor=actor,
        sysconfig=sysconfig,
    )

    outcome = await run_transitions(
        session,
        ticket_id=ticket_id,
        user_id=user_id,
        sysconfig=sysconfig,
        repository=repository,
    )
    return ActivityDialogSubmitResult(
        activity_changed=outcome.activity_changed,
        new_activity_entity_id=outcome.new_activity_entity_id,
        unsupported_actions=outcome.unsupported_actions,
        transition_entity_id=outcome.transition_entity_id,
    )
