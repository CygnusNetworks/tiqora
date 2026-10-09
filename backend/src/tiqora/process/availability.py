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

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

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
from tiqora.process.ticket_state import TicketProcessCandidates

# Process.pm ProcessList: new starts need Active; AgentTicketZoom keeps FadeAway tickets going.
STARTABLE_STATES = frozenset({"S1"})
RUNNING_STATES = frozenset({"S1", "S3"})

__all__ = [
    "RUNNING_STATES",
    "STARTABLE_STATES",
    "assert_can_start",
    "assert_can_submit",
    "available_dialogs",
    "dialog_allows",
    "list_startable_processes",
    "process_action_allowed",
]


async def filter_process_options(session: AsyncSession, **kwargs: Any) -> list[str]:
    """:func:`tiqora.domain.ticket_acl.filter_process_options`, imported lazily:
    domain.ticket_acl -> deployed.store -> process.dump -> this package."""
    from tiqora.domain.ticket_acl import filter_process_options as _filter

    return await _filter(session, **kwargs)


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
    if not await process_action_allowed(session, actor, ticket_id=ticket_id):
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


async def _visible_dialogs(
    session: AsyncSession,
    actor: ProcessActor,
    *,
    ticket_id: int,
    candidates: TicketProcessCandidates,
) -> list[ActivityDialogNode]:
    """Interface, (agents) dialog Permission and ``ActivityDialog`` ACL over the current
    activity's dialogs (``AgentTicketZoom.pm:1620-1650``)."""
    dialogs = [d for d in candidates.activity_dialogs if dialog_allows(d.config, actor.interface)]
    if actor.interface == "AgentInterface":
        # Znuny keeps the dialog Permission for agents only (AgentTicketProcess.pm:140-153);
        # CustomerTicketProcess has none, the portal writes as the panel user.
        queue_id: int | None = None
        rights: dict[str, bool] = {}
        allowed: list[ActivityDialogNode] = []
        for dialog in dialogs:
            permission = dialog.config.permission
            if permission:
                if permission not in rights:
                    if queue_id is None:
                        queue_id = int((await _ticket_must_exist(session, ticket_id))["queue_id"])
                    rights[permission] = await PermissionEngine(session).check(
                        actor.user_id, queue_id, permission
                    )
                if not rights[permission]:
                    continue
            allowed.append(dialog)
        dialogs = allowed
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
    the current activity) or :class:`ProcessPermissionDenied` (403).
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
    if not await process_action_allowed(session, actor, ticket_id=ticket_id):
        raise ProcessPermissionDenied(f"{actor.frontend_action} is not allowed on this ticket")
    visible = await _visible_dialogs(session, actor, ticket_id=ticket_id, candidates=candidates)
    if node.entity_id not in {d.entity_id for d in visible}:
        raise ProcessPermissionDenied(
            f"activity dialog {activity_dialog_entity_id!r} is not permitted for this user"
        )
    return node
