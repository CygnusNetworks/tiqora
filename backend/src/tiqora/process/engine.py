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
- Condition types ``GreaterThan(OrEqual)``/``LessThan(OrEqual)`` and any
  ``Module``-based (custom Perl module) condition type: treated as
  non-matching with a logged warning — see ``_evaluate_field``.
- TransitionAction modules other than the ten implemented below
  (``TicketSLASet``, ``TicketServiceSet``, ``TicketTypeSet``,
  ``DynamicFieldRemove``, ``DynamicFieldIncrement``,
  ``DynamicFieldPendingTimeSet``, ``LinkAdd``, ``TicketWatchSet``,
  ``ArticleSend``, ``TicketCreate``, ``ExecuteInvoker``,
  ``Appointment*``, ``ConfigItemUpdate``): logged as unsupported and
  no-op'd — collected into ``ActivityDialogSubmitResult.unsupported_actions``
  so callers/tests can assert on skip behaviour.
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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.dynamic_field import DynamicField, DynamicFieldValue
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.ticket import TicketPriority, TicketState
from tiqora.db.legacy.user import Users
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    _priority_name,  # noqa: PLC2701 -- deliberate reuse, see module docstring
    _queue_name,  # noqa: PLC2701
    _state_name,  # noqa: PLC2701
    _ticket_must_exist,  # noqa: PLC2701
    add_article,
    assign_owner,
    assign_responsible,
    change_priority,
    change_state,
    change_title,
    lock_ticket,
    move_queue,
    set_customer,
    unlock_ticket,
    update_dynamic_field,
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
)
from tiqora.process.graph import ProcessRepository
from tiqora.process.ticket_state import (
    ACTIVITY_ID_DF_NAME,
    PROCESS_ID_DF_NAME,
    get_ticket_process_state,
)
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


def _evaluate_field(match: Any, type_: str, value: str) -> bool:
    """Evaluate one ``Fields`` entry of a transition condition block.

    ``value`` is looked up from ``ticket_attrs`` by the caller, already
    defaulted to ``""`` if the field is absent (per subtask instructions:
    an absent field is treated as empty string, not a missing/error state).

    Semantics verified against
    ``znuny-6.5.22/Kernel/System/ProcessManagement/TransitionValidation/*.pm``
    (String.pm / Regexp.pm / Base.pm's ``Contains``/``NotContains``/
    ``Equal``/``NotEqual``):

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

    GreaterThan(OrEqual)/LessThan(OrEqual) and any ``Module``-based
    (custom Perl module) condition type are UNSUPPORTED/deferred: logged and
    treated as non-matching (``False``), never raised.
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
    "TicketArticleCreate": _action_ticket_article_create,
}
"""Implemented TransitionAction modules, keyed by the last ``::``-segment of
``TransitionActionConfig.module``. Any module not in this dict (e.g.
``TicketSLASet``, ``TicketServiceSet``, ``TicketTypeSet``,
``DynamicFieldRemove``, ``DynamicFieldIncrement``,
``DynamicFieldPendingTimeSet``, ``LinkAdd``, ``TicketWatchSet``,
``ArticleSend``, ``TicketCreate``, ``ExecuteInvoker``, ``Appointment*``,
``ConfigItemUpdate``) is unsupported/deferred — see
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
    dialog config declares. Only fields actually present in ``field_values``
    are applied — the required/optional check already ran in the caller.
    """
    if "Queue" in field_values:
        queue_id = await _resolve_queue_id(session, str(field_values["Queue"]), None)
        if queue_id is None and str(field_values["Queue"]).isdigit():
            queue_id = int(field_values["Queue"])
        if queue_id is not None:
            await move_queue(
                session,
                ticket_id=ticket_id,
                new_queue_id=queue_id,
                user_id=user_id,
                sysconfig=sysconfig,
            )

    if "State" in field_values:
        state_id = await _resolve_state_id(session, str(field_values["State"]), None)
        if state_id is None and str(field_values["State"]).isdigit():
            state_id = int(field_values["State"])
        if state_id is not None:
            await change_state(
                session,
                ticket_id=ticket_id,
                new_state_id=state_id,
                user_id=user_id,
                sysconfig=sysconfig,
            )

    if "Priority" in field_values:
        priority_id = await _resolve_priority_id(session, str(field_values["Priority"]), None)
        if priority_id is None and str(field_values["Priority"]).isdigit():
            priority_id = int(field_values["Priority"])
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
        owner_id = await _resolve_user_id(session, str(field_values["Owner"]), None)
        if owner_id is None and str(field_values["Owner"]).isdigit():
            owner_id = int(field_values["Owner"])
        if owner_id is not None:
            await assign_owner(
                session,
                ticket_id=ticket_id,
                new_owner_id=owner_id,
                user_id=user_id,
                sysconfig=sysconfig,
            )

    if "Responsible" in field_values:
        responsible_id = await _resolve_user_id(session, str(field_values["Responsible"]), None)
        if responsible_id is None and str(field_values["Responsible"]).isdigit():
            responsible_id = int(field_values["Responsible"])
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
