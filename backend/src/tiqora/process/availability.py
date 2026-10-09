"""Which processes may be started and which dialogs may be used, per surface.

One place for the three gates every process surface (agent, portal, engine) must agree on:

* the process state (``ProcessList`` ``ProcessState``: only Active processes can be started, but
  FadeAway ones keep running, ``AgentTicketProcess.pm`` / ``Process.pm`` ``ProcessList``),
* the dialog ``Interface`` (``ActivityDialog.pm`` ``ActivityDialogGet`` returns nothing for an
  interface the dialog does not list),
* the ACLs (``Process`` / ``ActivityDialog`` / ``Action`` return types,
  ``AgentTicketProcess.pm:156-177``, ``AgentTicketZoom.pm:1620-1650``) and, for agents, the
  dialog ``Permission``.

The ``Action`` ACL needs a ticket (``AgentTicketProcess.pm`` evaluates it only inside
``if ($TicketID)``): the new-ticket list and start ignore it, enroll/dialogs/can_start honour it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy import GroupCustomerUser, PermissionGroups, Queue
from tiqora.domain.ticket_write_service import (
    _ticket_must_exist,  # noqa: PLC2701 -- deliberate reuse
)
from tiqora.permissions.engine import PermissionEngine
from tiqora.process.actor import Interface, ProcessActor
from tiqora.process.config import ActivityDialogConfig
from tiqora.process.exceptions import (
    ActivityDialogNotAvailable,
    ProcessNotFound,
    ProcessNotRunning,
    ProcessNotStartable,
    ProcessPermissionDenied,
)
from tiqora.process.graph import ActivityDialogNode, ProcessGraph, ProcessRepository, ProcessSummary
from tiqora.process.ticket_state import TicketProcessCandidates, get_ticket_process_state
from tiqora.znuny.sysconfig import SysConfig

if TYPE_CHECKING:
    from tiqora.domain.ticket_acl import AclPrincipal, ProcessAclKind

# Process.pm ProcessList: new starts need Active; AgentTicketZoom keeps FadeAway tickets going.
STARTABLE_STATES = frozenset({"S1"})
RUNNING_STATES = frozenset({"S1", "S3"})

__all__ = [
    "RUNNING_STATES",
    "STARTABLE_STATES",
    "assert_can_start",
    "assert_can_submit",
    "available_dialogs",
    "can_start",
    "customer_create_queue_ids",
    "customer_group_ids",
    "dialog_allows",
    "list_startable_processes",
    "process_action_allowed",
]


async def filter_process_options(
    session: AsyncSession,
    *,
    principal: AclPrincipal,
    kind: ProcessAclKind,
    items: Sequence[str],
    action: str,
    ticket_id: int | None = None,
    process_entity_id: str | None = None,
    activity_entity_id: str | None = None,
    activity_dialog_entity_id: str | None = None,
) -> list[str]:
    """:func:`tiqora.domain.ticket_acl.filter_process_options`, imported lazily:
    domain.ticket_acl -> deployed.store -> process.dump -> this package."""
    from tiqora.domain.ticket_acl import filter_process_options as _filter

    return await _filter(
        session,
        principal=principal,
        kind=kind,
        items=items,
        action=action,
        ticket_id=ticket_id,
        process_entity_id=process_entity_id,
        activity_entity_id=activity_entity_id,
        activity_dialog_entity_id=activity_dialog_entity_id,
    )


def dialog_allows(dialog: ActivityDialogConfig, interface: Interface) -> bool:
    """True when the dialog lists *interface*; an empty list matches no interface
    (``ActivityDialog.pm`` ``ActivityDialogGet`` logs "No Interface ... found", returns nothing)."""
    return interface in {str(i) for i in (dialog.interface or [])}


async def process_action_allowed(
    session: AsyncSession, actor: ProcessActor, *, ticket_id: int | None
) -> bool:
    """The ``Action`` ACL for the process frontend module; only evaluated with a ticket."""
    if ticket_id is None:
        return True
    kept = await filter_process_options(
        session,
        principal=actor.acl_principal,
        kind="Action",
        items=[actor.frontend_action],
        action=actor.frontend_action,
        ticket_id=ticket_id,
    )
    return actor.frontend_action in kept


async def customer_group_ids(
    session: AsyncSession, actor: ProcessActor, permission: str
) -> set[int]:
    """``CustomerGroup::GroupMemberList(Type => permission, Result => 'ID')``
    (``CustomerGroup.pm:266-281``): with ``CustomerGroupSupport`` off every valid group, with it
    on the valid groups of the customer user's ``group_customer_user`` rows (``permission_key``
    *permission* or ``rw``) plus ``CustomerGroupAlwaysGroups``. Company groups stay the
    documented limitation of ``load_customer_user_checks``."""
    sysconfig = SysConfig(session)
    valid = {
        int(gid): name
        for gid, name in (
            await session.execute(
                select(PermissionGroups.id, PermissionGroups.name).where(
                    PermissionGroups.valid_id == 1
                )
            )
        ).all()
    }
    if str(await sysconfig.get("CustomerGroupSupport", 0)) not in ("1", "True", "true"):
        return set(valid)
    if not actor.customer_user_login:
        return set()
    rows = (
        await session.execute(
            select(GroupCustomerUser.group_id).where(
                GroupCustomerUser.user_id == actor.customer_user_login,
                GroupCustomerUser.permission_value == 1,
                GroupCustomerUser.permission_key.in_([permission, "rw"]),
            )
        )
    ).scalars()
    ids = {int(g) for g in rows if int(g) in valid}
    always = await sysconfig.get("CustomerGroupAlwaysGroups", ["users"])
    always_names = {str(n) for n in always} if isinstance(always, list) else set()
    return ids | {gid for gid, name in valid.items() if name in always_names}


async def customer_create_queue_ids(session: AsyncSession, actor: ProcessActor) -> dict[int, str]:
    """Valid queues (id -> name) whose group the customer holds ``create`` in
    (``CustomerTicketProcess.pm:4626-4690`` ``_GetQueues``: ``MoveList Type => create`` plus the
    ``GroupMemberList Type => create`` check on ``QueueData{GroupID}``)."""
    groups = await customer_group_ids(session, actor, "create")
    if not groups:
        return {}
    rows = (
        await session.execute(
            select(Queue.id, Queue.name)
            .where(Queue.valid_id == 1, Queue.group_id.in_(sorted(groups)))
            .order_by(Queue.name)
        )
    ).all()
    return {int(r[0]): r[1] for r in rows}


async def _customer_has_permission(
    session: AsyncSession, actor: ProcessActor, *, queue_id: int, permission: str
) -> bool:
    """``TicketCustomerPermission`` GroupCheck (``CustomerPermission/GroupCheck.pm``).

    The customer needs *permission* in the queue's group via ``CustomerGroup::GroupMemberList``:
    with ``CustomerGroupSupport`` off that is every group (no extra restriction), with it on the
    ``Group_<perm>`` lists of :func:`load_customer_user_checks`. Company groups stay the
    documented limitation of that function.
    """
    from tiqora.domain.ticket_acl import load_customer_user_checks  # noqa: PLC0415 -- cycle

    sysconfig = SysConfig(session)
    if str(await sysconfig.get("CustomerGroupSupport", 0)) not in ("1", "True", "true"):
        return True
    group_name = (
        await session.execute(
            select(PermissionGroups.name)
            .join(Queue, Queue.group_id == PermissionGroups.id)
            .where(Queue.id == queue_id)
        )
    ).scalar_one_or_none()
    if group_name is None or not actor.customer_user_login:
        return False
    checks = await load_customer_user_checks(
        session, actor.customer_user_login, sysconfig=sysconfig
    )
    return group_name in checks.get(f"Group_{permission}", [])


async def _has_ticket_permission(
    session: AsyncSession, actor: ProcessActor, *, ticket_id: int, permission: str
) -> bool:
    """The dialog / start permission on the ticket's queue: ``TicketPermission`` for agents
    (AgentTicketProcess.pm:140-153), ``TicketCustomerPermission`` for customers
    (CustomerTicketProcess.pm:84-121)."""
    queue_id = int((await _ticket_must_exist(session, ticket_id))["queue_id"])
    if actor.interface == "AgentInterface":
        return await PermissionEngine(session).check(actor.user_id, queue_id, permission)
    return await _customer_has_permission(session, actor, queue_id=queue_id, permission=permission)


async def _start_dialog_offered(
    graph: ProcessGraph, actor: ProcessActor, repository: ProcessRepository
) -> bool:
    """``ProcessList`` Interface filter: the start dialog must exist for the interface."""
    if not graph.config.start_activity or not graph.config.start_activity_dialog:
        return False
    dialog = await repository.get_activity_dialog(graph.config.start_activity_dialog)
    return dialog is not None and dialog_allows(dialog.config, actor.interface)


async def list_startable_processes(
    session: AsyncSession,
    actor: ProcessActor,
    *,
    ticket_id: int | None = None,
    repository: ProcessRepository | None = None,
) -> list[ProcessSummary]:
    """Processes the actor may start: Active, start dialog on the interface, Process ACL."""
    repository = repository or ProcessRepository(session)
    if ticket_id is not None and not (
        await _has_ticket_permission(session, actor, ticket_id=ticket_id, permission="rw")
        and await process_action_allowed(session, actor, ticket_id=ticket_id)
    ):
        return []
    candidates: list[ProcessSummary] = []
    for summary in await repository.list_processes():
        if summary.state_entity_id not in STARTABLE_STATES:
            continue
        graph = await repository.get_process(summary.entity_id)
        if graph is None or not await _start_dialog_offered(graph, actor, repository):
            continue
        candidates.append(summary)
    if not candidates:
        return []
    kept = set(
        await filter_process_options(
            session,
            principal=actor.acl_principal,
            kind="Process",
            items=[c.entity_id for c in candidates],
            action=actor.frontend_action,
            ticket_id=ticket_id,
        )
    )
    return [c for c in candidates if c.entity_id in kept]


async def assert_can_start(
    session: AsyncSession,
    actor: ProcessActor,
    *,
    process_entity_id: str,
    ticket_id: int | None,
    repository: ProcessRepository,
) -> ProcessGraph:
    """Raise :class:`ProcessNotFound` / :class:`ProcessNotStartable`; return the process graph."""
    graph = await repository.get_process(process_entity_id)
    if graph is None or graph.config.start_activity is None:
        raise ProcessNotFound(process_entity_id)
    if graph.state_entity_id not in STARTABLE_STATES:
        raise ProcessNotStartable(f"process {process_entity_id!r} is not active")
    if not await _start_dialog_offered(graph, actor, repository):
        raise ProcessNotStartable(
            f"process {process_entity_id!r} has no start dialog for {actor.interface}"
        )
    if ticket_id is not None and not await _has_ticket_permission(
        session, actor, ticket_id=ticket_id, permission="rw"
    ):
        raise ProcessPermissionDenied(f"{actor.interface} lacks 'rw' on ticket {ticket_id}")
    if not await process_action_allowed(session, actor, ticket_id=ticket_id):
        raise ProcessNotStartable(f"{actor.frontend_action} is not allowed on this ticket")
    kept = await filter_process_options(
        session,
        principal=actor.acl_principal,
        kind="Process",
        items=[process_entity_id],
        action=actor.frontend_action,
        ticket_id=ticket_id,
    )
    if process_entity_id not in kept:
        raise ProcessNotStartable(f"process {process_entity_id!r} is not available")
    return graph


async def _dialog_permitted(
    session: AsyncSession, actor: ProcessActor, *, ticket_id: int, dialog: ActivityDialogNode
) -> bool:
    """The dialog ``Permission`` on the ticket's queue.

    Agents: only when the dialog sets one (the REST layer already demands ``rw``; Znuny's
    default is ``rw`` too, AgentTicketProcess.pm:140-153). Customers: ``Permission`` or ``rw``
    through GroupCheck (CustomerTicketProcess.pm:84-121). Known limitation: for agents ``rw``
    is required on top of a weaker dialog Permission (stricter than Znuny).
    """
    permission = dialog.config.permission
    if actor.interface == "AgentInterface" and not permission:
        return True
    return await _has_ticket_permission(
        session, actor, ticket_id=ticket_id, permission=permission or "rw"
    )


async def _visible_dialogs(
    session: AsyncSession,
    actor: ProcessActor,
    *,
    ticket_id: int,
    candidates: TicketProcessCandidates,
) -> list[ActivityDialogNode]:
    """Interface, dialog Permission and one ``ActivityDialog`` ACL call over the current
    activity's dialogs (``AgentTicketZoom.pm:1620-1650``)."""
    dialogs = [
        d
        for d in candidates.activity_dialogs
        if dialog_allows(d.config, actor.interface)
        and await _dialog_permitted(session, actor, ticket_id=ticket_id, dialog=d)
    ]
    if not dialogs:
        return []
    kept = set(
        await filter_process_options(
            session,
            principal=actor.acl_principal,
            kind="ActivityDialog",
            items=[d.entity_id for d in dialogs],
            action=actor.frontend_action,
            ticket_id=ticket_id,
            process_entity_id=candidates.process.entity_id,
            activity_entity_id=candidates.activity_entity_id,
        )
    )
    return [d for d in dialogs if d.entity_id in kept]


async def available_dialogs(
    session: AsyncSession,
    actor: ProcessActor,
    *,
    ticket_id: int,
    candidates: TicketProcessCandidates,
) -> list[ActivityDialogNode]:
    """Dialogs the actor may open now; none unless the process is running and the Action ACL
    allows the process module on this ticket."""
    if candidates.process.state_entity_id not in RUNNING_STATES:
        return []
    if not await process_action_allowed(session, actor, ticket_id=ticket_id):
        return []
    return await _visible_dialogs(session, actor, ticket_id=ticket_id, candidates=candidates)


async def can_start(
    session: AsyncSession,
    actor: ProcessActor,
    *,
    ticket_id: int,
    repository: ProcessRepository,
) -> bool:
    """The ticket is not in a process and the actor may start at least one (the start
    permission, the Action ACL and the Process ACL are all evaluated by the list)."""
    if await get_ticket_process_state(session, ticket_id) is not None:
        return False
    return bool(
        await list_startable_processes(session, actor, ticket_id=ticket_id, repository=repository)
    )


async def assert_can_submit(
    session: AsyncSession,
    actor: ProcessActor,
    *,
    ticket_id: int,
    candidates: TicketProcessCandidates,
    activity_dialog_entity_id: str,
) -> ActivityDialogNode:
    """The dialog node to submit.

    Raises :class:`ProcessNotRunning` (409), :class:`ActivityDialogNotAvailable` (409, not on
    the current activity) or :class:`ProcessPermissionDenied` (403). Like Znuny's submit
    (AgentTicketProcess.pm:248-266, CustomerTicketProcess.pm:146-170) the ``ActivityDialog``
    ACL is evaluated for this one dialog with its ``ActivityDialogEntityID`` known, so an ACL
    keyed on that id applies even when it did not hide the dialog in the list.
    """
    if candidates.process.state_entity_id not in RUNNING_STATES:
        raise ProcessNotRunning(f"process {candidates.process.entity_id!r} is not running")
    node = next(
        (d for d in candidates.activity_dialogs if d.entity_id == activity_dialog_entity_id), None
    )
    if node is None:
        raise ActivityDialogNotAvailable(activity_dialog_entity_id)
    if not dialog_allows(node.config, actor.interface):
        raise ProcessPermissionDenied(
            f"activity dialog {activity_dialog_entity_id!r} is not available on {actor.interface}"
        )
    if not await _dialog_permitted(session, actor, ticket_id=ticket_id, dialog=node):
        raise ProcessPermissionDenied(
            f"missing permission for activity dialog {activity_dialog_entity_id!r}"
        )
    if not await process_action_allowed(session, actor, ticket_id=ticket_id):
        raise ProcessPermissionDenied(f"{actor.frontend_action} is not allowed on this ticket")
    kept = await filter_process_options(
        session,
        principal=actor.acl_principal,
        kind="ActivityDialog",
        items=[node.entity_id],
        action=actor.frontend_action,
        ticket_id=ticket_id,
        process_entity_id=candidates.process.entity_id,
        activity_entity_id=candidates.activity_entity_id,
        activity_dialog_entity_id=node.entity_id,
    )
    if node.entity_id not in kept:
        raise ProcessPermissionDenied(
            f"activity dialog {activity_dialog_entity_id!r} is not permitted for this user"
        )
    return node
