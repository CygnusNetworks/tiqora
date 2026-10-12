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
- TransitionAction modules are dispatched through the registry
  ``tiqora.process.actions.TRANSITION_ACTIONS`` (:func:`resolve_action`); every
  Znuny module is listed there. Only ``ConfigItemUpdate`` has no Tiqora handler
  (``run is None``, it needs the CMDB). ``ExecuteInvoker`` runs the GenericInterface
  requester's handler (``tiqora.gi.process_action``), which always queues the call as
  a background job. Unsupported and
  unknown modules are logged and no-op'd, and collected into
  ``ActivityDialogSubmitResult.unsupported_actions``.
- Activity Dialog ``PendingTimeDiff`` is not a dialog field (a transition action's own
  ``PendingTimeDiff`` on ``TicketStateSet`` is honoured).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.channels.email.placeholder import PlaceholderContext
from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.queue import Queue, Service, Sla
from tiqora.db.legacy.ticket import TicketPriority, TicketState, TicketType
from tiqora.db.legacy.user import Users
from tiqora.domain.attachments_in import ArticleAttachmentIn, AttachmentInvalid, decode_attachments
from tiqora.domain.dynamic_field_values import load_ticket_dynamic_field
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    TicketIn,
    _state_type_name,  # noqa: PLC2701
    _ticket_must_exist,  # noqa: PLC2701
    add_article,
    add_time_accounting,
    assign_owner,
    assign_responsible,
    change_priority,
    change_service,
    change_sla,
    change_state,
    change_title,
    change_type,
    create_ticket,
    link_objects,
    link_tickets,
    lock_ticket,
    move_queue,
    set_customer,
    unlock_ticket,
    unwatch_ticket,
    update_dynamic_field,
    watch_ticket,
)
from tiqora.process.actions.registry import module_short_name, resolve_action
from tiqora.process.actor import ProcessActor
from tiqora.process.attachments_field import (
    ATTACHMENTS_FIELD,
    collect_action_attachments,
    encode_attachment_selection,
    valid_attachment_keys,
)
from tiqora.process.availability import RUNNING_STATES, assert_can_submit
from tiqora.process.conditions import (
    ConditionConfigError,
    ConditionData,
    as_integer,
    check_transition,
    condition_module_warning_scope,
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
    time_units_mode,
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
from tiqora.util.perl import perl_comma_list, perl_truthy
from tiqora.znuny.cache_invalidation import invalidate_ticket_cache
from tiqora.znuny.history import (
    TYPE_ADD_NOTE,
    TYPE_FOLLOW_UP,
    TYPE_PHONE_CALL_AGENT,
    TYPE_PHONE_CALL_CUSTOMER,
    TYPE_WEB_REQUEST_CUSTOMER,
    add_pending_time,
)
from tiqora.znuny.sysconfig import SysConfig

logger = logging.getLogger(__name__)

__all__ = [
    "ActivityDialogSubmitResult",
    "TransitionOutcome",
    "apply_activity_dialog",
    "dialog_values_and_missing",
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


async def _ticket_column_unchanged(
    session: AsyncSession, ticket_id: int, column: str, new_id: int
) -> bool:
    """Same queue/priority/responsible: Znuny's ``TicketQueueSet`` (Ticket.pm:2157),
    ``TicketPrioritySet`` (:5380) and ``TicketResponsibleSet`` (:5094) write nothing. The
    dialog now preselects the ticket's current values, so an untouched field is submitted."""
    t = await _ticket_must_exist(session, ticket_id)
    return int(t[column]) == new_id


async def _state_unchanged(session: AsyncSession, ticket_id: int, state_id: int) -> bool:
    """Same state: Znuny writes nothing (TicketStateSet.pm:163, Ticket.pm:4531-4539)."""
    t = await _ticket_must_exist(session, ticket_id)
    return int(t["ticket_state_id"]) == state_id


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
    elif await _state_unchanged(session, ticket_id, int(state_id)):
        return
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
    """Port of TransitionAction::TicketCustomerSet (TicketCustomerSet.pm:103-190): a falsy
    ``CustomerID`` falls back to ``No``, ``CustomerUserID`` to ``User``; each key that differs
    from the ticket is set by its own ``TicketCustomerSet`` call (own history row), an equal
    one is skipped."""
    del sysconfig
    keys = ("CustomerID", "No", "CustomerUserID", "User")
    if not any(perl_truthy(config.get(k)) for k in keys):
        raise RequiredFieldMissing(
            "TicketCustomerSet: Config must set CustomerID/No or CustomerUserID/User"
        )
    customer_id = config.get("CustomerID")
    if not perl_truthy(customer_id) and perl_truthy(config.get("No")):
        customer_id = config.get("No")
    customer_user_id = config.get("CustomerUserID")
    if not perl_truthy(customer_user_id) and perl_truthy(config.get("User")):
        customer_user_id = config.get("User")
    t = await _ticket_must_exist(session, ticket_id)
    if customer_id is not None and str(customer_id) != t.get("customer_id"):
        await set_customer(
            session,
            ticket_id=ticket_id,
            customer_id=str(customer_id),
            customer_user_id=None,
            user_id=user_id,
            only_given=True,
        )
    if customer_user_id is not None and str(customer_user_id) != t.get("customer_user_id"):
        await set_customer(
            session,
            ticket_id=ticket_id,
            customer_id=None,
            customer_user_id=str(customer_user_id),
            user_id=user_id,
            only_given=True,
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
    queue_id = int(t["queue_id"])
    article = ArticleIn(
        sender_type="agent",
        is_visible_for_customer=perl_truthy(config.get("IsVisibleForCustomer", 1)),
        subject=subject,
        body=body,
        channel="email",
        # ``To`` is Znuny's key; ``Customer`` is the older Tiqora spelling.
        from_address=await _article_send_from(session, config, queue_id),
        to_address=_address_list(config.get("To") or config.get("Customer")),
        cc=_address_list(config.get("Cc")),
        bcc=_address_list(config.get("Bcc")),
        attachments=await collect_action_attachments(
            session, config, ticket_id=ticket_id, sysconfig=sysconfig
        ),
    )
    await deliver_agent_email_reply(
        session,
        sysconfig,
        None,
        ticket_id=ticket_id,
        queue_id=queue_id,
        user_id=user_id,
        article=article,
    )


def _address_list(value: Any) -> str | None:
    """``To``/``Cc``/``Bcc`` as one comma separated string (a list value is joined)."""
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v).strip() for v in value if str(v).strip())
    text_value = str(value or "").strip()
    return text_value or None


async def _system_address(
    session: AsyncSession, *, address: str | None = None, queue_id: int | None = None
) -> tuple[str, str] | None:
    """``(address, realname)`` of a valid system address, by address or a queue's own."""
    if address is not None:
        row = (
            await session.execute(
                text(
                    "SELECT value0, value1 FROM system_address"
                    " WHERE LOWER(value0) = :a AND valid_id = 1 LIMIT 1"
                ),
                {"a": address.lower()},
            )
        ).first()
    else:
        row = (
            await session.execute(
                text(
                    "SELECT sa.value0, sa.value1 FROM queue q"
                    " JOIN system_address sa ON sa.id = q.system_address_id"
                    " WHERE q.id = :q LIMIT 1"
                ),
                {"q": queue_id},
            )
        ).first()
    if row is None or not str(row[0] or "").strip():
        return None
    return str(row[0]).strip(), str(row[1] or "").strip()


async def _article_send_from(
    session: AsyncSession, config: dict[str, Any], ticket_queue_id: int
) -> str | None:
    """Port of ``ArticleSend::FromGet`` (``TransitionAction/ArticleSend.pm:378-432``).

    ``From`` only counts when it is a local system address (``Realname <address>``);
    anything else is logged and replaced by the default. The default is the system address
    of ``Queue``/``QueueID`` when set, else of the ticket's queue. ``None`` lets the shared
    outbound path pick the ticket queue's address, which is the same default.
    """
    from email.utils import parseaddr

    wanted = str(config.get("From") or "").strip()
    if wanted:
        found = await _system_address(session, address=parseaddr(wanted)[1] or wanted)
        if found is not None:
            addr, name = found
            return f"{name} <{addr}>" if name else addr
        logger.warning(
            "ArticleSend: configured From address %r is not a system address, "
            "using the default queue address",
            wanted,
        )
    queue_id = await _resolve_queue_id(session, config.get("Queue"), config.get("QueueID"))
    if queue_id is not None and int(queue_id) != ticket_queue_id:
        found = await _system_address(session, queue_id=int(queue_id))
        if found is not None:
            addr, name = found
            return f"{name} <{addr}>" if name else addr
    return None


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
        is_visible_for_customer=perl_truthy(config.get("IsVisibleForCustomer", 0)),
        subject=str(config.get("Subject", "")),
        body=str(config.get("Body", "")),
        channel=channel,
        attachments=await collect_action_attachments(
            session, config, ticket_id=ticket_id, sysconfig=sysconfig
        ),
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


def _acting_user(config: dict[str, Any], user_id: int, module: str) -> int:
    """``_OverrideUserID``: ``DynamicField_UserID`` or ``UserID`` replaces the acting user."""
    raw = config.get("DynamicField_UserID") or config.get("UserID")
    if raw in (None, ""):
        return user_id
    try:
        return int(str(raw).strip())
    except ValueError:
        logger.warning("%s: UserID %r is not a number, using the acting user", module, raw)
        return user_id


async def _users_by_email(session: AsyncSession, pattern: str) -> list[int]:
    """``UserSearch(PostMasterSearch)``: valid users whose ``UserEmail`` preference matches.

    ``*`` is the wildcard, the match is case insensitive and the whole address has to match.
    Matching happens here, not in SQL: the preference is a BLOB on MariaDB and a bytea on
    PostgreSQL.
    """
    from fnmatch import fnmatchcase

    wanted = pattern.lower().replace("[", "[[]")
    rows = (
        await session.execute(
            text(
                "SELECT p.user_id, p.preferences_value FROM user_preferences p"
                " WHERE p.preferences_key = 'UserEmail'"
            )
        )
    ).all()
    ids = sorted(
        {
            int(uid)
            for uid, value in rows
            if value is not None
            and fnmatchcase(
                (bytes(value).decode("utf-8", "replace") if not isinstance(value, str) else value)
                .strip()
                .lower(),
                wanted,
            )
        }
    )
    if not ids:
        return []
    valid = await session.execute(select(Users.id).where(Users.id.in_(ids), Users.valid_id == 1))
    return sorted(int(r) for r in valid.scalars().all())


async def _action_ticket_watch_set(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Port of TicketWatchSet (``TicketWatchSet.pm:126-200``).

    Znuny keys: ``Action`` (``Subscribe`` default; ``Unsubscribe``; ``UnsubscribeAll``), the
    watchers as ``UserLogin`` / ``PostMasterSearch`` / ``UserIDs`` (comma lists, each present
    list is applied) and ``UserID`` (``DynamicField_UserID``), which overrides the *acting*
    user. Unknown logins / ids are skipped.

    Fallback for configs written for Tiqora before: with none of the three lists, ``User``
    (login) or ``UserID`` names the one watcher (default the acting user), and ``Action``
    still picks subscribe or unsubscribe.
    """
    del sysconfig
    action = str(config.get("Action") or "Subscribe")

    async def apply(watcher_id: int, actor: int) -> None:
        if action == "Subscribe":
            await watch_ticket(
                session, ticket_id=ticket_id, watcher_user_id=watcher_id, user_id=actor
            )
        else:  # _TicketWatch: everything but Subscribe unsubscribes
            await unwatch_ticket(
                session, ticket_id=ticket_id, watcher_user_id=watcher_id, user_id=actor
            )

    if action == "UnsubscribeAll":
        actor = _acting_user(config, user_id, "TicketWatchSet")
        watchers = (
            (
                await session.execute(
                    text("SELECT user_id FROM ticket_watcher WHERE ticket_id = :t"),
                    {"t": ticket_id},
                )
            )
            .scalars()
            .all()
        )
        for watcher_id in watchers:
            await unwatch_ticket(
                session, ticket_id=ticket_id, watcher_user_id=int(watcher_id), user_id=actor
            )
        return

    logins = perl_comma_list(config.get("UserLogin"))
    searches = perl_comma_list(config.get("PostMasterSearch"))
    user_ids = perl_comma_list(config.get("UserIDs"))
    if not (logins or searches or user_ids):
        # Tiqora's own keys: one watcher, ``User`` (login) or ``UserID``.
        watcher_id = await _resolve_user_id(
            session,
            config.get("User"),
            int(config["UserID"]) if str(config.get("UserID") or "").isdigit() else None,
        )
        await apply(int(watcher_id) if watcher_id is not None else user_id, user_id)
        return

    actor = _acting_user(config, user_id, "TicketWatchSet")
    for login in logins:
        found = await _resolve_user_id(session, login, None)
        if found is None:
            logger.warning("TicketWatchSet: cannot find user login %r", login)
            continue
        await apply(int(found), actor)
    for pattern in searches:
        for found in await _users_by_email(session, pattern):
            await apply(found, actor)
    for raw in user_ids:
        found_id = (
            (
                await session.execute(
                    select(Users.id).where(Users.id == int(raw), Users.valid_id == 1)
                )
            ).scalar_one_or_none()
            if raw.isdigit()
            else None
        )
        if found_id is None:
            logger.warning("TicketWatchSet: cannot find user with id %r", raw)
            continue
        await apply(int(found_id), actor)


async def _action_link_add(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Port of LinkAdd (``LinkAdd.pm``): ``LinkObject::LinkAdd`` with the config as is.

    Znuny keys: ``SourceObject``, ``SourceKey``, ``TargetObject``, ``TargetKey``, ``Type``,
    ``State`` (``Valid`` / ``Temporary``) and ``UserID`` (``DynamicField_UserID``), which
    overrides the acting user. The link is stored from source to target as given.

    Keys left out fall back to Tiqora's older form: source ``Ticket`` / the current ticket,
    target ``Ticket`` with ``TargetTicketID`` (or ``TicketID``), ``Type`` or ``LinkType``
    (default ``Normal``), ``State`` ``Valid``.
    """
    del sysconfig
    actor = _acting_user(config, user_id, "LinkAdd")
    source_object = str(config.get("SourceObject") or "Ticket").strip()
    target_object = str(config.get("TargetObject") or "Ticket").strip()
    source_key = str(config.get("SourceKey") or "").strip() or str(ticket_id)
    target_key = str(
        config.get("TargetKey") or config.get("TargetTicketID") or config.get("TicketID") or ""
    ).strip()
    if not target_key:
        raise RequiredFieldMissing("LinkAdd: Config must set 'TargetKey' (or 'TargetTicketID')")
    for object_name in (source_object, target_object):
        if not _LINK_OBJECT_RE.match(object_name):
            raise RequiredFieldMissing(f"LinkAdd: {object_name!r} is not a link object name")
    for obj, key in ((source_object, source_key), (target_object, target_key)):
        if obj == "Ticket" and not key.isdigit():
            raise RequiredFieldMissing(f"LinkAdd: {key!r} is not a ticket id")
    await link_objects(
        session,
        source_object=source_object,
        source_key=source_key,
        target_object=target_object,
        target_key=target_key,
        link_type=str(config.get("Type") or config.get("LinkType") or "Normal"),
        state=str(config.get("State") or "Valid"),
        user_id=actor,
    )


_LINK_OBJECT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,99}$")


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
            is_visible_for_customer=perl_truthy(config.get("IsVisibleForCustomer")),
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
    entry = resolve_action(action.module)
    if entry is None or entry.run is None:
        logger.warning("unsupported ProcessManagement TransitionAction module: %s", action.module)
        return module_short_name(action.module)
    config = await replace_action_config(
        action.config, session=session, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig
    )
    await entry.run(session, config, ticket_id, user_id, sysconfig)
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
    new_ticket: bool = False,
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
        if queue_id is not None and not await _ticket_column_unchanged(
            session, ticket_id, "queue_id", queue_id
        ):
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
        if state_id is not None and not await _state_unchanged(session, ticket_id, state_id):
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
        if priority_id is not None and not await _ticket_column_unchanged(
            session, ticket_id, "ticket_priority_id", priority_id
        ):
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
        # change_title is a no-op for an unchanged title (Ticket.pm:1883).
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
        if responsible_id is not None and not await _ticket_column_unchanged(
            session, ticket_id, "responsible_user_id", responsible_id
        ):
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
        if "CustomerID" in field_values and not is_present(customer_id):
            # An agent picked a customer user and left the company empty: Znuny's agent screens
            # fill it with the customer user's UserCustomerID through AJAX (D6).
            picked = str(customer_user_id or "").strip()
            if picked:
                customer_id = await customer_id_of_user(session, picked)
        # AgentTicketProcess.pm:5770-5783: no TicketCustomerSet when CustomerID and
        # CustomerUser both equal the ticket's (Perl ``eq``: undef compares as "").
        unchanged = str(customer_id or "") == str(t.get("customer_id") or "") and str(
            customer_user_id or ""
        ) == str(t.get("customer_user_id") or "")
        if not unchanged:
            await set_customer(
                session,
                ticket_id=ticket_id,
                customer_id=customer_id,
                customer_user_id=customer_user_id,
                user_id=user_id,
            )

    # Dynamic fields, the article and the owner are stored in FieldOrder (the Znuny save loop,
    # AgentTicketProcess.pm:5463ff; Owner via $TicketFieldSetSub, :5800-5860); a dynamic field
    # the order does not list comes last. A new ticket stores all dynamic fields first
    # (:5262-5290), the article and the owner follow.
    order = dialog_field_order(dialog_config)
    dynamic = [k for k in field_values if k.startswith("DynamicField_")]
    dynamic.sort(key=lambda k: order.index(k) if k in order else len(order))
    steps = [n for n in order if n in ("Article", "Owner", ATTACHMENTS_FIELD) or n in dynamic]
    steps += [k for k in dynamic if k not in steps]
    if new_ticket:
        steps = dynamic + [n for n in steps if n not in dynamic]
    for step in steps:
        if step == "Owner":
            owner_id = (
                await _resolve_dialog_field_id(
                    session,
                    field="Owner",
                    kind="user id",
                    raw=field_values["Owner"],
                    resolver=_resolve_user_id,
                )
                if "Owner" in field_values
                else None
            )
            if owner_id is not None:
                await assign_owner(
                    session,
                    ticket_id=ticket_id,
                    new_owner_id=owner_id,
                    user_id=user_id,
                    sysconfig=sysconfig,
                )
            continue
        if step == ATTACHMENTS_FIELD:
            # Agent and existing ticket only (AgentTicketProcess.pm:1878, 5513-5545). Znuny sets
            # the dynamic field only for a non-empty selection; unknown keys are dropped.
            if sender_type == "agent" and not new_ticket and ATTACHMENTS_FIELD in field_values:
                keys = await valid_attachment_keys(
                    session, ticket_id, sysconfig, field_values[ATTACHMENTS_FIELD]
                )
                if keys:
                    await update_dynamic_field(
                        session,
                        ticket_id=ticket_id,
                        field_name=await sysconfig.get_str(
                            "Process::DynamicFieldProcessManagementAttachment",
                            "ProcessManagementAttachment",
                        ),
                        values=[encode_attachment_selection(keys)],
                        user_id=user_id,
                    )
            continue
        if step == "Article":
            await _store_dialog_article(
                session,
                dialog_config=dialog_config,
                field_values=field_values,
                ticket_id=ticket_id,
                user_id=user_id,
                sysconfig=sysconfig,
                sender_type=sender_type,
                new_ticket=new_ticket,
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


async def customer_id_of_user(session: AsyncSession, login: str) -> str | None:
    """``UserCustomerID`` of the customer user *login* (``None`` for an unknown login)."""
    return (
        await session.execute(select(CustomerUser.customer_id).where(CustomerUser.login == login))
    ).scalar_one_or_none()


#: Files per dialog submit; the reply dialog (``useChatAttachments.MAX_ATTACHMENTS``) allows the
#: same number (rulings F33).
MAX_DIALOG_ATTACHMENTS = 10


def _decode_dialog_attachments(raw: Any) -> list[tuple[str, str, bytes]]:
    """``ArticleAttachments`` (list of ``{filename, content_type, content_base64}``) -> files.

    Raises :class:`AttachmentInvalid` (422) / ``AttachmentTooLarge`` (413) from the domain layer;
    the routes map them."""
    if raw is None or raw == []:
        return []
    if not isinstance(raw, list):
        raise AttachmentInvalid("ArticleAttachments must be a list")
    if len(raw) > MAX_DIALOG_ATTACHMENTS:
        raise AttachmentInvalid(f"at most {MAX_DIALOG_ATTACHMENTS} attachments per submit")
    try:
        items = [ArticleAttachmentIn.model_validate(item) for item in raw]
    except ValidationError as exc:
        raise AttachmentInvalid(f"invalid attachment: {exc.errors()[0]['msg']}") from None
    return decode_attachments(items)


async def _store_dialog_article(
    session: AsyncSession,
    *,
    dialog_config: ActivityDialogConfig,
    field_values: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
    sender_type: str,
    new_ticket: bool = False,
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
        if is_present(field_values.get("ArticleAttachments")):
            # Files belong to the article (AgentTicketProcess.pm:5701-5707): no article, no files.
            logger.warning(
                "process dialog uploads ignored: ticket %s gets no article (Subject and Body "
                "are both required)",
                ticket_id,
            )
        return
    cfg = article_cfg.config
    channel_raw = str(cfg.get("CommunicationChannel") or cfg.get("ArticleType") or "note").lower()
    # The history row is the dialog's, not add_article's channel-derived default:
    # agent AgentTicketProcess.pm:5650-5654 (AddNote "%%Note" for every channel but Phone,
    # PhoneCallAgent "%%"); customer CustomerTicketProcess.pm:3955-3962 (PhoneCallCustomer for
    # Phone, else WebRequestCustomer on a new ticket and FollowUp on an existing one, "%%").
    phone = channel_raw == "phone"
    if sender_type == "agent":
        history_type = TYPE_PHONE_CALL_AGENT if phone else TYPE_ADD_NOTE
        history_comment = "%%" if phone else "%%Note"
    else:
        if phone:
            history_type = TYPE_PHONE_CALL_CUSTOMER
        else:
            history_type = TYPE_WEB_REQUEST_CUSTOMER if new_ticket else TYPE_FOLLOW_UP
        history_comment = "%%"
    attachments = _decode_dialog_attachments(field_values.get("ArticleAttachments"))
    article = ArticleIn(
        sender_type=sender_type,
        is_visible_for_customer=perl_truthy(cfg.get("IsVisibleForCustomer", 0)),
        subject=str(field_values["Subject"]),
        body=str(field_values["Body"]),
        channel=_ARTICLE_CHANNEL_MAP.get(channel_raw, "note"),
        history_type_override=history_type,
        history_comment_override=history_comment,
        attachments=attachments,
    )
    article_id = await add_article(
        session, ticket_id=ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
    )
    # Time accounting after the article, with its id (AgentTicketProcess.pm:5716-5723). A
    # Perl-false value (empty, "0") books nothing; the field exists for agents only and only
    # while Ticket::Frontend::AccountTime is on.
    mode = time_units_mode(
        dialog_config,
        "AgentInterface" if sender_type == "agent" else "CustomerInterface",
        account_time=perl_truthy(await sysconfig.get("Ticket::Frontend::AccountTime")),
    )
    time_units = field_values.get("TimeUnits")
    if mode != "0" and perl_truthy(time_units):
        await add_time_accounting(
            session,
            ticket_id=ticket_id,
            article_id=article_id,
            time_unit=time_units if isinstance(time_units, int | float) else str(time_units),
            user_id=user_id,
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


def dialog_values_and_missing(
    dialog: ActivityDialogConfig,
    field_values: Mapping[str, Any],
    actor: ProcessActor,
    *,
    account_time: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    """Effective dialog values and the ``Display: 2`` fields still missing (PendingTime included).

    On the customer interface ``CustomerUserID``/``CustomerID`` are the acting customer
    (CustomerTicketProcess.pm:3560-3569, 3675-3676): whatever was submitted is ignored and a
    mandatory CustomerUserID counts as given, although the form never asks for it.
    """
    values = effective_field_values(dialog, field_values, actor.interface)
    if actor.interface == "CustomerInterface":
        values["CustomerUserID"] = actor.customer_user_login
        values["CustomerID"] = actor.customer_id
    return values, missing_required_fields(
        dialog, values, actor.interface, account_time=account_time
    )


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
    new_ticket: bool = False,
) -> None:
    """Validate and store one activity dialog submission with Znuny's field semantics.

    ``skip_fields`` (canonical names) are validated but not written again — used when a field
    was already consumed by ``create_ticket``. ``apply_required_lock=False`` and
    ``new_ticket=True`` for new tickets (Znuny never runs RequiredLock on create, stores the
    dynamic fields right after creating the ticket, and a customer's article is a web request
    instead of a follow-up).
    """
    account_time = perl_truthy(await sysconfig.get("Ticket::Frontend::AccountTime"))
    values, missing = dialog_values_and_missing(
        dialog, field_values, actor, account_time=account_time
    )
    customer_filled = actor.interface == "CustomerInterface"
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
    # ``if ($ActivityDialogHashRef->{RequiredLock})`` (AgentTicketProcess.pm:183): Perl truthiness.
    if (
        apply_required_lock
        and perl_truthy(dialog.required_lock)
        and actor.interface == "AgentInterface"
    ):
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
        new_ticket=new_ticket,
    )
    if pending_time is not None:
        # AgentTicketProcess.pm:5554-5575: TicketPendingTimeSet after the other fields.
        await _set_ticket_pending_time(
            session, ticket_id=ticket_id, pending_time=pending_time, user_id=actor.user_id
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
    chosen = None
    with condition_module_warning_scope():
        for candidate in sorted(activity.outgoing_transitions, key=lambda t: t.entity_id):
            try:
                if check_transition(candidate.config, data, tags=tags):
                    chosen = candidate
                    break
            except ConditionConfigError as exc:
                # Znuny's bare ``return`` leaves TransitionCheck: nothing fires (Transition.pm:322).
                logger.error("transition %s: %s; no transition runs", candidate.entity_id, exc)
                return _NO_TRANSITION
    if chosen is None:
        return _NO_TRANSITION
    transition = chosen
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
    evaluate the current activity's outgoing transitions in entity-id order
    (:func:`run_transitions`, ``Process.pm:442-445``) and, for the first one that
    matches, move the ticket to its target activity and then run its
    TransitionActions (``Process.pm:501``, ``:558ff``).

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
