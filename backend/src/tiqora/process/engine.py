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
  with a logged warning — see ``_evaluate_field``. Ordered comparisons
  (``GreaterThan`` / ``GreaterThanOrEqual`` / ``LessThan`` /
  ``LessThanOrEqual``) are implemented per Znuny
  ``TransitionValidation::Base``.
- TransitionAction modules other than the implemented handlers (see
  ``_ACTION_HANDLERS``) — remaining deferred: ``ExecuteInvoker``,
  ``Appointment*`` (Create/Update/Remove), ``ConfigItemUpdate``.
  Unsupported modules are logged and no-op'd, and collected into
  ``ActivityDialogSubmitResult.unsupported_actions``.
- ``%<OTRS_TICKET_...>%``/``<OTRS_...>`` smart-tag placeholder substitution
  inside TransitionAction ``Config`` values (Znuny's
  ``TemplateGenerator::_Replace``, used e.g. by
  ``TransitionValidation::Base::CheckValueGet``/``MatchValueGet``) is NOT
  implemented — Config values and condition Match values are used verbatim.
  Documented as a deferred feature, not a line-by-line port.
- Activity Dialog ``PendingTime``/``PendingTimeDiff`` submission fields are
  not applied from ``field_values`` (only a transition action's own
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

from tiqora.db.legacy.dynamic_field import DynamicField, DynamicFieldValue
from tiqora.db.legacy.queue import Queue, Service, Sla
from tiqora.db.legacy.ticket import TicketPriority, TicketState, TicketType
from tiqora.db.legacy.user import Users
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    TicketIn,
    _priority_name,  # noqa: PLC2701 -- deliberate reuse, see module docstring
    _queue_name,  # noqa: PLC2701
    _state_name,  # noqa: PLC2701
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
from tiqora.permissions.engine import PermissionEngine
from tiqora.process.config import ActivityDialogConfig, TransitionActionConfig, TransitionConfig
from tiqora.process.exceptions import (
    ActivityDialogNotAvailable,
    ActivityDialogNotFound,
    ProcessNotFound,
    ProcessPermissionDenied,
    RequiredFieldMissing,
    TicketAlreadyInProcess,
    TicketNotInProcess,
    UnresolvedFieldValue,
)
from tiqora.process.graph import ProcessRepository
from tiqora.process.ticket_state import (
    ACTIVITY_ID_DF_NAME,
    PROCESS_ID_DF_NAME,
    get_ticket_process_state,
)
from tiqora.znuny.cache_invalidation import invalidate_ticket_cache
from tiqora.znuny.history import add_pending_time
from tiqora.znuny.sysconfig import SysConfig

logger = logging.getLogger(__name__)

__all__ = [
    "ActivityDialogSubmitResult",
    "evaluate_transition",
    "execute_transition_action",
    "get_ticket_attrs",
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
    repository = ProcessRepository(session)
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
# 2 & 3. get_ticket_attrs + evaluate_transition
# ---------------------------------------------------------------------------


async def get_ticket_attrs(session: AsyncSession, ticket_id: int) -> dict[str, str]:
    """Build the flat ``{field_name: value}`` dict used for condition
    evaluation and (indirectly, by documentation) for TransitionAction
    Config placeholder resolution (not implemented, see module docstring).

    Reuses ``ticket_write_service``'s private ``_ticket_must_exist``/
    ``_queue_name``/``_state_name``/``_priority_name`` lookup helpers
    (favoring reuse per subtask instructions) plus the same
    ``DynamicField``/``DynamicFieldValue`` loading pattern used by
    ``TicketService._load_dynamic_fields``. Multi-value dynamic fields are
    joined with ``", "`` — matching the string this flat model can hold and
    the same join convention ``update_dynamic_field`` uses for its history
    "old value" string.
    """
    t = await _ticket_must_exist(session, ticket_id)
    attrs: dict[str, str] = {
        "Queue": await _queue_name(session, int(t["queue_id"])),
        "State": await _state_name(session, int(t["ticket_state_id"])),
        "Priority": await _priority_name(session, int(t["ticket_priority_id"])),
        "Title": str(t.get("title") or ""),
    }

    fields = (
        (
            await session.execute(
                select(DynamicField).where(
                    DynamicField.object_type == "Ticket", DynamicField.valid_id == 1
                )
            )
        )
        .scalars()
        .all()
    )
    if not fields:
        return attrs

    field_by_id = {f.id: f for f in fields}
    values = (
        (
            await session.execute(
                select(DynamicFieldValue).where(
                    DynamicFieldValue.object_id == ticket_id,
                    DynamicFieldValue.field_id.in_(field_by_id.keys()),
                )
            )
        )
        .scalars()
        .all()
    )
    grouped: dict[int, list[str]] = {}
    for v in values:
        val: str | None = None
        if v.value_text is not None:
            val = str(v.value_text)
        elif v.value_int is not None:
            val = str(v.value_int)
        elif v.value_date is not None:
            val = v.value_date.isoformat()
        if val is not None:
            grouped.setdefault(v.field_id, []).append(val)

    for fid, field in field_by_id.items():
        attrs[f"DynamicField_{field.name}"] = ", ".join(grouped.get(fid, []))

    return attrs


# DateTime / Date → epoch, matching TransitionValidation::Base::ValueValidate.
_DATETIME_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})\s(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
# Znuny VariableCheck::IsInteger — optional leading '-', no leading zeros.
_INTEGER_RE = re.compile(r"^(-)?(?:0|[1-9]\d*)$")
# DynamicFieldPendingTimeSet offset: ``1d 5h 12m 500s`` (parts optional).
_OFFSET_RE = re.compile(
    r"^(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s\s*)?$",
    re.IGNORECASE,
)


def _value_validate(raw: str) -> str | int:
    """Port of ``TransitionValidation::Base::ValueValidate``.

    DateTime (``YYYY-MM-DD HH:MM[:SS]``) and Date (``YYYY-MM-DD``) strings
    become epoch seconds (UTC); everything else is returned as-is.
    """
    m = _DATETIME_RE.match(raw)
    if m:
        y, mo, d, h, mi, sec = m.groups()
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(sec or 0), tzinfo=UTC)
        return int(dt.timestamp())
    m = _DATE_RE.match(raw)
    if m:
        y, mo, d = m.groups()
        dt = datetime(int(y), int(mo), int(d), 0, 0, 0, tzinfo=UTC)
        return int(dt.timestamp())
    return raw


def _as_integer(value: Any) -> int | None:
    """Port of Znuny ``IsInteger`` after ValueValidate (ints or integer strings)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if _INTEGER_RE.match(s):
        return int(s)
    return None


def _compare_ordered(value: str, match: Any, op: str) -> bool:
    """Znuny GreaterThan/LessThan/… — integer compare after ValueValidate.

    Both sides are ValueValidate'd (dates → epoch), then must both be
    integers (``IsInteger``); otherwise the condition does not match.
    """
    check_i = _as_integer(_value_validate(str(value)))
    match_i = _as_integer(_value_validate(str(match)))
    if check_i is None or match_i is None:
        return False
    if op == "gt":
        return check_i > match_i
    if op == "gte":
        return check_i >= match_i
    if op == "lt":
        return check_i < match_i
    if op == "lte":
        return check_i <= match_i
    return False


def _evaluate_field(match: Any, type_: str, value: str) -> bool:
    """Evaluate one ``Fields`` entry of a transition condition block.

    ``value`` is looked up from ``ticket_attrs`` by the caller, already
    defaulted to ``""`` if the field is absent (per subtask instructions:
    an absent field is treated as empty string, not a missing/error state).

    Semantics verified against
    ``znuny-6.5.22/Kernel/System/ProcessManagement/TransitionValidation/*.pm``
    (String.pm / Regexp.pm / Base.pm's ``Contains``/``NotContains``/
    ``Equal``/``NotEqual`` / ordered compares):

    - ``String``: exact match via Perl ``eq`` — case-SENSITIVE (the task
      brief's "case-insensitive" guess does not hold; ``String.pm`` compares
      with plain ``eq``, no ``lc()``. Verified directly in the .pm source).
    - ``Regexp``: the raw ``Match`` string is used as a regex pattern,
      matched against the value with ``re.search`` (Perl ``=~``).
      Python's ``re`` syntax is a close but not 100%-identical superset/
      subset of Perl's; documented divergence, not fixed here.
    - ``Contains``/``NotContains``: Base.pm's ``Contains()``/``NotContains()``
      lower-case both sides and match ``$CheckValue =~ m{$MatchValue}`` — the
      match value is itself interpolated as a *regex*, not a literal
      substring, and the comparison is case-insensitive. Ported faithfully:
      ``re.search(match, value, re.IGNORECASE)``.
    - ``Equal``/``NotEqual``: Base.pm's ``Equal()``/``NotEqual()`` lower-case
      both sides and compare with ``eq``/``ne`` (for the plain-string case
      this engine operates on — Znuny's array-ref handling is not relevant
      to a flat string attribute dict).
    - ``GreaterThan`` / ``GreaterThanOrEqual`` / ``LessThan`` /
      ``LessThanOrEqual`` (and ``…Equals`` aliases): ValueValidate both
      sides (DateTime/Date → epoch), then integer compare only — non-integer
      values do not match (Znuny ``IsInteger`` gate).
    - ``Module``-based (custom Perl module) conditions remain UNSUPPORTED:
      logged and treated as non-matching (``False``), never raised.
    """
    type_lower = type_.lower()

    if type_lower == "string":
        if not isinstance(match, str):
            return False
        return value == match

    if type_lower == "regexp":
        pattern = match if isinstance(match, str) else str(match)
        try:
            return re.search(pattern, value) is not None
        except re.error:
            logger.warning("invalid Regexp condition pattern %r", pattern)
            return False

    if type_lower == "contains":
        pattern = str(match)
        try:
            return re.search(pattern, value, re.IGNORECASE) is not None
        except re.error:
            logger.warning("invalid Contains condition pattern %r", pattern)
            return False

    if type_lower == "notcontains":
        pattern = str(match)
        try:
            return re.search(pattern, value, re.IGNORECASE) is None
        except re.error:
            logger.warning("invalid NotContains condition pattern %r", pattern)
            return True

    if type_lower == "equal":
        return value.lower() == str(match).lower()

    if type_lower == "notequal":
        return value.lower() != str(match).lower()

    if type_lower == "greaterthan":
        return _compare_ordered(value, match, "gt")

    if type_lower in ("greaterthanorequal", "greaterthanequals", "greaterthanequal"):
        return _compare_ordered(value, match, "gte")

    if type_lower == "lessthan":
        return _compare_ordered(value, match, "lt")

    if type_lower in ("lessthanorequal", "lessthanequals", "lessthanequal"):
        return _compare_ordered(value, match, "lte")

    logger.warning("unsupported/deferred transition condition Type: %s", type_)
    return False


def evaluate_transition(transition_config: TransitionConfig, ticket_attrs: dict[str, str]) -> bool:
    """Evaluate whether *transition_config* matches *ticket_attrs*.

    No ``Condition``/empty ``conditions`` => unconditional match (``True``),
    per Znuny semantics (see ``TransitionConfig`` docstring). Otherwise:
    ``Fields`` within one condition block combine per that block's
    ``type_`` (``and``/``or``), and blocks combine per
    ``transition_config.condition_linking`` (``and``/``or``). An empty
    ``Fields`` map within a block is vacuously ``True`` for that block.
    """
    if not transition_config.conditions:
        return True

    block_results: list[bool] = []
    for block in transition_config.conditions:
        field_results = [
            _evaluate_field(cond.match, cond.type_, ticket_attrs.get(name, ""))
            for name, cond in block.fields.items()
        ]
        if not field_results:
            block_results.append(True)
        elif block.type_.lower() == "or":
            block_results.append(any(field_results))
        else:
            block_results.append(all(field_results))

    if transition_config.condition_linking.lower() == "or":
        return any(block_results)
    return all(block_results)


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
    validated = _value_validate(str(raw).strip())
    # After ValueValidate, datetimes are epoch ints; plain strings that are
    # already epoch also work via _as_integer.
    epoch = _as_integer(validated)
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
        validated = _value_validate(str(config["PendingTime"]).strip())
        epoch = _as_integer(validated)
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
    consulted by any of the implemented handlers — kept for forward
    compatibility (logging, future placeholder substitution) and because
    the subtask spec mandates this exact signature.
    """
    del process_entity_id, activity_entity_id, transition_entity_id  # see docstring
    module_name = _module_short_name(action.module)
    handler = _ACTION_HANDLERS.get(module_name)
    if handler is None:
        logger.warning("unsupported ProcessManagement TransitionAction module: %s", action.module)
        return module_name
    await handler(session, action.config, ticket_id, user_id, sysconfig)
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


def _is_present(field_name: str, field_values: dict[str, Any]) -> bool:
    """Required-field presence check for one dialog ``Fields`` entry.

    The pseudo-field ``Article`` is special-cased: Znuny's Article dialog
    field is really a bundle of ``Subject``/``Body``/... sub-fields, so
    "present" means either of the two content-bearing ones was submitted.
    """
    if field_name == "Article":
        return bool(field_values.get("Subject")) or bool(field_values.get("Body"))
    value = field_values.get(field_name)
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, list):
        return len(value) > 0
    return True


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
) -> None:
    """Apply submitted field_values to the ticket, per the field names the
    dialog config declares. Only fields actually present (and non-blank) in
    ``field_values`` are applied — the required/optional check already ran in
    the caller.
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

    if "Title" in field_values and field_values["Title"]:
        await change_title(
            session, ticket_id=ticket_id, new_title=str(field_values["Title"]), user_id=user_id
        )

    if "Owner" in field_values:
        owner_id = await _resolve_dialog_field_id(
            session,
            field="Owner",
            kind="user id",
            raw=field_values["Owner"],
            resolver=_resolve_user_id,
        )
        if owner_id is not None:
            await assign_owner(
                session,
                ticket_id=ticket_id,
                new_owner_id=owner_id,
                user_id=user_id,
                sysconfig=sysconfig,
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

    if "Article" in dialog_config.fields and _is_present("Article", field_values):
        article_field_cfg = dialog_config.fields["Article"].config
        channel_raw = str(
            article_field_cfg.get("ArticleType")
            or article_field_cfg.get("CommunicationChannel")
            or "note"
        ).lower()
        channel = _ARTICLE_CHANNEL_MAP.get(channel_raw, "note")
        article = ArticleIn(
            sender_type=str(field_values.get("SenderType") or "agent"),
            is_visible_for_customer=bool(field_values.get("IsVisibleForCustomer", False)),
            subject=str(field_values.get("Subject", "")),
            body=str(field_values.get("Body", "")),
            channel=channel,
        )
        await add_article(
            session, ticket_id=ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
        )

    for key, value in field_values.items():
        if not key.startswith("DynamicField_"):
            continue
        field_name = key.removeprefix("DynamicField_")
        values = value if isinstance(value, list) else [value]
        str_values = [str(v) for v in values]
        await update_dynamic_field(
            session, ticket_id=ticket_id, field_name=field_name, values=str_values, user_id=user_id
        )


async def submit_activity_dialog(
    session: AsyncSession,
    *,
    ticket_id: int,
    activity_dialog_entity_id: str,
    field_values: dict[str, Any],
    user_id: int,
    sysconfig: SysConfig,
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

    repository = ProcessRepository(session)
    graph = await repository.get_process(state.process_entity_id)
    if graph is None:
        raise ProcessNotFound(state.process_entity_id)

    activity = graph.activities.get(state.activity_entity_id)
    if activity is None:
        raise TicketNotInProcess(ticket_id)

    dialog_node = next(
        (d for d in activity.activity_dialogs if d.entity_id == activity_dialog_entity_id), None
    )
    if dialog_node is None:
        if await repository.get_activity_dialog(activity_dialog_entity_id) is None:
            raise ActivityDialogNotFound(activity_dialog_entity_id)
        raise ActivityDialogNotAvailable(activity_dialog_entity_id)

    dialog_config = dialog_node.config

    if dialog_config.permission:
        t = await _ticket_must_exist(session, ticket_id)
        perms = PermissionEngine(session)
        if not await perms.check(user_id, int(t["queue_id"]), dialog_config.permission):
            raise ProcessPermissionDenied(
                f"user {user_id} lacks {dialog_config.permission!r} for activity dialog "
                f"{activity_dialog_entity_id!r}"
            )

    for field_name, field_cfg in dialog_config.fields.items():
        if field_cfg.required and not _is_present(field_name, field_values):
            raise RequiredFieldMissing(f"required activity dialog field missing: {field_name}")

    await _apply_dialog_field_changes(
        session,
        dialog_config=dialog_config,
        field_values=field_values,
        ticket_id=ticket_id,
        user_id=user_id,
        sysconfig=sysconfig,
    )

    unsupported: list[str] = []
    activity_changed = False
    new_activity_entity_id: str | None = None
    matched_transition_entity_id: str | None = None

    # Transitions are evaluated in Path's YAML insertion order (Python dicts
    # preserve it) — Znuny does not guarantee explicit ordering either.
    for transition in activity.outgoing_transitions:
        attrs = await get_ticket_attrs(session, ticket_id)
        if not evaluate_transition(transition.config, attrs):
            continue

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

        await update_dynamic_field(
            session,
            ticket_id=ticket_id,
            field_name=ACTIVITY_ID_DF_NAME,
            values=[transition.target_activity_entity_id],
            user_id=user_id,
        )
        activity_changed = True
        new_activity_entity_id = transition.target_activity_entity_id
        matched_transition_entity_id = transition.entity_id
        break

    return ActivityDialogSubmitResult(
        activity_changed=activity_changed,
        new_activity_entity_id=new_activity_entity_id,
        unsupported_actions=unsupported,
        transition_entity_id=matched_transition_entity_id,
    )
