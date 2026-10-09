"""New process tickets and enrolling an existing ticket through the start dialog (plan D10).

Znuny references: ``AgentTicketProcess.pm`` ``_StoreActivityDialog`` (:5104-5290 create,
:5300ff enroll, :5967 owner), ``CustomerTicketProcess.pm:3660-3800``.

Order of :func:`create_process_ticket`: gate (``assert_can_start``) -> start dialog -> required
fields -> ``Queue``/``State``/``Lock``/``Priority`` from the values, else the
``Process::Default*`` settings -> ``create`` permission (agents) -> title -> ``create_ticket``
(owner 1, so the owner change fires its events later) -> process + activity dynamic fields ->
the remaining dialog fields -> owner -> transitions.

History written for a start dialog ``Region`` + ``Article`` + ``Owner`` with a transition to
a second activity: ``NewTicket``, ``TicketDynamicFieldUpdate`` (process), (activity), (Region),
``AddNote``, ``OwnerUpdate``, then the transition's own rows (the new activity is stored before
its actions run, Process.pm:501/558).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.ticket import TicketLockType, TicketPriority, TicketState
from tiqora.domain.ticket_write_service import TicketIn, assign_owner, create_ticket
from tiqora.permissions.engine import PermissionEngine
from tiqora.process.actor import ProcessActor
from tiqora.process.availability import assert_can_start, customer_create_queue_ids
from tiqora.process.engine import (
    TransitionOutcome,
    _resolve_dialog_field_id,  # noqa: PLC2701 -- deliberate reuse
    _resolve_priority_id,  # noqa: PLC2701
    _resolve_queue_id,  # noqa: PLC2701
    _resolve_service_id,  # noqa: PLC2701
    _resolve_sla_id,  # noqa: PLC2701
    _resolve_state_id,  # noqa: PLC2701
    _resolve_type_id,  # noqa: PLC2701
    _resolve_user_id,  # noqa: PLC2701
    apply_activity_dialog,
    run_transitions,
    start_process,
)
from tiqora.process.exceptions import (
    ProcessNotFound,
    ProcessPermissionDenied,
    RequiredFieldMissing,
    TicketAlreadyInProcess,
)
from tiqora.process.fields import effective_field_values, is_present, missing_required_fields
from tiqora.process.graph import ActivityDialogNode, ProcessGraph, ProcessRepository
from tiqora.process.smart_tags import render_ticketless
from tiqora.process.ticket_state import get_ticket_process_state
from tiqora.znuny.sysconfig import SysConfig

logger = logging.getLogger(__name__)

__all__ = ["ProcessTicketResult", "create_process_ticket", "enroll_ticket"]

# Fields ``create_ticket`` consumes: ``apply_activity_dialog`` must not write them a second time
# (duplicate Move / StateUpdate / OwnerUpdate rows).
_CREATE_CONSUMED = frozenset(
    {
        "Title",
        "Queue",
        "State",
        "Priority",
        "Lock",
        "Type",
        "Service",
        "SLA",
        "CustomerUserID",
        "CustomerID",
        "Owner",
        "Responsible",
    }
)


@dataclass(frozen=True, slots=True)
class ProcessTicketResult:
    ticket_id: int
    ticket_number: str
    transition: TransitionOutcome


async def _start_dialog(
    graph: ProcessGraph, repository: ProcessRepository, process_entity_id: str
) -> ActivityDialogNode:
    dialog_id = graph.config.start_activity_dialog
    dialog = await repository.get_activity_dialog(dialog_id) if dialog_id else None
    if dialog is None:  # assert_can_start demanded it; a race with a redeploy
        raise ProcessNotFound(process_entity_id)
    return dialog


def _assert_required(
    dialog: ActivityDialogNode, field_values: Mapping[str, Any], actor: ProcessActor
) -> dict[str, Any]:
    """Effective values; raise for a missing ``Display: 2`` field (PendingTime depends on the
    target state and is checked by :func:`apply_activity_dialog`)."""
    values = effective_field_values(dialog.config, field_values, actor.interface)
    if actor.interface == "CustomerInterface":
        values["CustomerUserID"] = actor.customer_user_login
        values["CustomerID"] = actor.customer_id
    missing = [
        n
        for n in missing_required_fields(dialog.config, values, actor.interface)
        if n != "PendingTime"
    ]
    if missing:
        raise RequiredFieldMissing(f"required activity dialog field missing: {', '.join(missing)}")
    return values


async def _lock_id(session: AsyncSession, raw: str) -> int | None:
    if raw.isdigit():
        return (
            await session.execute(select(TicketLockType.id).where(TicketLockType.id == int(raw)))
        ).scalar_one_or_none()
    return (
        await session.execute(
            select(TicketLockType.id).where(
                TicketLockType.name == raw, TicketLockType.valid_id == 1
            )
        )
    ).scalar_one_or_none()


async def _resolve_required(
    session: AsyncSession,
    sysconfig: SysConfig,
    values: Mapping[str, Any],
    field: str,
    *,
    kind: str,
    resolver: Any,
    default_setting: str,
    default_name: str,
) -> int:
    """The id of Queue/State/Priority: the submitted value, else the ``Process::Default*``
    setting (AgentTicketProcess.pm:5135-5155 ``_CheckField``)."""
    if is_present(values.get(field)):
        resolved = await _resolve_dialog_field_id(
            session, field=field, kind=kind, raw=values[field], resolver=resolver
        )
        if resolved is not None:
            return int(resolved)
    default = await sysconfig.get_str(default_setting, default_name)
    resolved = await resolver(session, default, None)
    if resolved is None:
        raise RequiredFieldMissing(f"{default_setting} fehlt oder ist ungültig.")
    return int(resolved)


async def _resolve_lock(
    session: AsyncSession, sysconfig: SysConfig, values: Mapping[str, Any]
) -> int:
    raw = str(values.get("Lock") or "").strip().lower()
    if raw:
        lock_id = await _lock_id(session, raw)
        if lock_id is not None:
            return int(lock_id)
    default = (await sysconfig.get_str("Process::DefaultLock", "unlock")).strip().lower()
    lock_id = await _lock_id(session, default)
    if lock_id is None:
        raise RequiredFieldMissing("Process::DefaultLock fehlt oder ist ungültig.")
    return int(lock_id)


async def _optional_id(
    session: AsyncSession, values: Mapping[str, Any], field: str, kind: str, resolver: Any
) -> int | None:
    if field not in values:
        return None
    resolved = await _resolve_dialog_field_id(
        session, field=field, kind=kind, raw=values[field], resolver=resolver
    )
    return int(resolved) if resolved is not None else None


async def _name(session: AsyncSession, column: Any, id_column: Any, id_: int) -> str:
    return str((await session.execute(select(column).where(id_column == id_))).scalar_one())


def _now_in_zone(zone_name: str) -> tuple[str, str]:
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("OTRSTimeZone %r is unknown, using UTC for the default title", zone_name)
        zone, zone_name = ZoneInfo("UTC"), "UTC"
    return datetime.now(UTC).astimezone(zone).strftime("%Y-%m-%d %H:%M:%S"), zone_name


async def _default_title(
    sysconfig: SysConfig, process_name: str, actor: ProcessActor, values: Mapping[str, Any]
) -> str:
    """``Title``, else the article ``Subject``, else ``"<process> - <time> (<zone>)"``
    (the customer interface omits the zone, CustomerTicketProcess.pm:3690)."""
    for key in ("Title", "Subject"):
        if is_present(values.get(key)):
            return str(values[key])
    stamp, zone = _now_in_zone(await sysconfig.otrs_time_zone())
    if actor.interface == "CustomerInterface":
        return f"{process_name} - {stamp}"
    return f"{process_name} - {stamp} ({zone})"


async def _customer(
    session: AsyncSession, actor: ProcessActor, values: Mapping[str, Any]
) -> tuple[str | None, str | None]:
    """``(customer_id, customer_user_id)``: forced from the acting customer on the customer
    interface; agents choose, and a customer user without a customer id brings its own
    (``TicketCustomerSet``)."""
    if actor.interface == "CustomerInterface":
        return actor.customer_id, actor.customer_user_login
    customer_user = str(values.get("CustomerUserID") or "").strip() or None
    customer_id = str(values.get("CustomerID") or "").strip() or None
    if customer_user and not customer_id:
        customer_id = (
            await session.execute(
                select(CustomerUser.customer_id).where(CustomerUser.login == customer_user)
            )
        ).scalar_one_or_none()
    return customer_id, customer_user


async def create_process_ticket(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    *,
    actor: ProcessActor,
    process_entity_id: str,
    field_values: Mapping[str, Any],
    repository: ProcessRepository | None = None,
) -> ProcessTicketResult:
    """Create a ticket from the start dialog of *process_entity_id* and run its first
    transitions. The caller owns the transaction: any error leaves nothing behind once it
    rolls back."""
    repository = repository or ProcessRepository(session)
    graph = await assert_can_start(
        session, actor, process_entity_id=process_entity_id, ticket_id=None, repository=repository
    )
    dialog = await _start_dialog(graph, repository, process_entity_id)
    values = _assert_required(dialog, field_values, actor)

    queue_id = await _resolve_required(
        session,
        sysconfig,
        values,
        "Queue",
        kind="queue id",
        resolver=_resolve_queue_id,
        default_setting="Process::DefaultQueue",
        default_name="Raw",
    )
    state_id = await _resolve_required(
        session,
        sysconfig,
        values,
        "State",
        kind="state id",
        resolver=_resolve_state_id,
        default_setting="Process::DefaultState",
        default_name="new",
    )
    priority_id = await _resolve_required(
        session,
        sysconfig,
        values,
        "Priority",
        kind="priority id",
        resolver=_resolve_priority_id,
        default_setting="Process::DefaultPriority",
        default_name="3 normal",
    )
    lock_id = await _resolve_lock(session, sysconfig, values)

    if actor.interface == "AgentInterface" and not await PermissionEngine(session).check(
        actor.user_id, queue_id, "create"
    ):
        raise ProcessPermissionDenied(
            f"user {actor.user_id} lacks 'create' on queue {queue_id} of process "
            f"{process_entity_id!r}"
        )

    if (
        actor.interface == "CustomerInterface"
        and (values.get("Queue") or values.get("QueueID"))
        and queue_id not in await customer_create_queue_ids(session, actor)
    ):
        # CustomerTicketProcess.pm:4626-4690 _GetQueues: only queues of the customer's create
        # groups are offered; a queue chosen explicitly must come from that list.
        raise ProcessPermissionDenied(
            f"customer {actor.customer_user_login!r} may not create tickets in queue {queue_id}"
        )

    type_id = await _optional_id(session, values, "Type", "type id", _resolve_type_id)
    service_id = await _optional_id(session, values, "Service", "service id", _resolve_service_id)
    sla_id = await _optional_id(session, values, "SLA", "sla id", _resolve_sla_id)
    responsible_id = await _optional_id(session, values, "Responsible", "user id", _resolve_user_id)
    owner_id = await _optional_id(session, values, "Owner", "user id", _resolve_user_id)
    customer_id, customer_user_id = await _customer(session, actor, values)

    # Znuny renders the title with the submitted values plus the resolved Queue/State/Priority/
    # Lock names (AgentTicketProcess.pm:5183-5195).
    tag_values = {k: str(v) for k, v in values.items() if isinstance(v, str | int | float)}
    tag_values.update(
        Queue=await _name(session, Queue.name, Queue.id, queue_id),
        State=await _name(session, TicketState.name, TicketState.id, state_id),
        Priority=await _name(session, TicketPriority.name, TicketPriority.id, priority_id),
        Lock=await _name(session, TicketLockType.name, TicketLockType.id, lock_id),
    )
    title = await render_ticketless(
        await _default_title(sysconfig, graph.name, actor, values),
        session=session,
        user_id=actor.user_id,
        sysconfig=sysconfig,
        values=tag_values,
    )

    # Agents create as owner 1 and change the owner afterwards, so the owner events fire;
    # the customer interface creates as its panel user (CustomerTicketProcess.pm:3677-3685).
    ticket_id = await create_ticket(
        session,
        session_factory,
        sysconfig,
        params=TicketIn(
            title=title,
            queue_id=queue_id,
            state_id=state_id,
            priority_id=priority_id,
            owner_id=actor.user_id if actor.interface == "CustomerInterface" else 1,
            lock_id=lock_id,
            type_id=type_id,
            service_id=service_id,
            sla_id=sla_id,
            responsible_id=responsible_id,
            customer_id=customer_id,
            customer_user_id=customer_user_id,
        ),
        user_id=actor.user_id,
    )
    await start_process(
        session,
        ticket_id=ticket_id,
        process_entity_id=process_entity_id,
        user_id=actor.user_id,
        sysconfig=sysconfig,
        repository=repository,
    )
    await apply_activity_dialog(
        session,
        ticket_id=ticket_id,
        dialog=dialog.config,
        field_values=dict(field_values),
        actor=actor,
        sysconfig=sysconfig,
        skip_fields=_CREATE_CONSUMED,
        apply_required_lock=False,
    )
    if actor.interface == "AgentInterface" and owner_id is not None and owner_id != 1:
        await assign_owner(
            session,
            ticket_id=ticket_id,
            new_owner_id=owner_id,
            user_id=actor.user_id,
            sysconfig=sysconfig,
        )
    transition = await run_transitions(
        session,
        ticket_id=ticket_id,
        user_id=actor.user_id,
        sysconfig=sysconfig,
        repository=repository,
    )
    number = (
        await session.execute(text("SELECT tn FROM ticket WHERE id = :id"), {"id": ticket_id})
    ).scalar_one()
    return ProcessTicketResult(
        ticket_id=ticket_id, ticket_number=str(number), transition=transition
    )


async def enroll_ticket(
    session: AsyncSession,
    sysconfig: SysConfig,
    *,
    actor: ProcessActor,
    ticket_id: int,
    process_entity_id: str,
    field_values: Mapping[str, Any],
    repository: ProcessRepository | None = None,
) -> TransitionOutcome:
    """Start *process_entity_id* on an existing ticket through its start dialog
    (``TicketAlreadyInProcess`` when it already runs one), then run the transitions."""
    repository = repository or ProcessRepository(session)
    graph = await assert_can_start(
        session,
        actor,
        process_entity_id=process_entity_id,
        ticket_id=ticket_id,
        repository=repository,
    )
    dialog = await _start_dialog(graph, repository, process_entity_id)
    if await get_ticket_process_state(session, ticket_id) is not None:
        raise TicketAlreadyInProcess(ticket_id)
    _assert_required(dialog, field_values, actor)
    await start_process(
        session,
        ticket_id=ticket_id,
        process_entity_id=process_entity_id,
        user_id=actor.user_id,
        sysconfig=sysconfig,
        repository=repository,
    )
    await apply_activity_dialog(
        session,
        ticket_id=ticket_id,
        dialog=dialog.config,
        field_values=dict(field_values),
        actor=actor,
        sysconfig=sysconfig,
    )
    return await run_transitions(
        session,
        ticket_id=ticket_id,
        user_id=actor.user_id,
        sysconfig=sysconfig,
        repository=repository,
    )
