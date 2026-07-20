"""Resolve a ticket's current ProcessManagement (BPM) process/activity state.

Ticket-process linkage is not a foreign key — it is stored the same way any
other dynamic field value is stored: two well-known Dynamic Fields,
``ProcessManagementProcessID`` and ``ProcessManagementActivityID``, whose
*values* (in ``dynamic_field_value.value_text``) hold the process/activity
``EntityID`` strings (e.g. ``Process-f8194...``, ``Activity-50235...``), not
the numeric ``pm_process.id``/``pm_activity.id``. This mirrors the
``_load_dynamic_fields`` pattern in ``tiqora.domain.ticket_service``
(``DynamicField`` by ``name`` + ``object_type == "Ticket"``, then
``DynamicFieldValue`` by ``object_id == ticket_id`` and ``field_id``).

Read-only. Condition evaluation for candidate transitions is subtask 2's
job — this module only exposes the raw candidate lists.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.dynamic_field import DynamicField, DynamicFieldValue
from tiqora.process.graph import ActivityDialogNode, ProcessGraph, ProcessRepository, TransitionNode

PROCESS_ID_DF_NAME = "ProcessManagementProcessID"
ACTIVITY_ID_DF_NAME = "ProcessManagementActivityID"


@dataclass(frozen=True)
class TicketProcessState:
    """A ticket's current position in a BPM process."""

    process_entity_id: str
    activity_entity_id: str


async def get_ticket_process_state(
    session: AsyncSession, ticket_id: int
) -> TicketProcessState | None:
    """Resolve ``(process_entity_id, activity_entity_id)`` for *ticket_id*.

    Returns ``None`` if either Dynamic Field is not defined, or if the
    ticket has no value set for one/both of them (i.e. the ticket is not
    part of a process).
    """
    fields = (
        (
            await session.execute(
                select(DynamicField).where(
                    DynamicField.name.in_([PROCESS_ID_DF_NAME, ACTIVITY_ID_DF_NAME]),
                    DynamicField.object_type == "Ticket",
                )
            )
        )
        .scalars()
        .all()
    )
    field_id_by_name = {f.name: f.id for f in fields}
    process_field_id = field_id_by_name.get(PROCESS_ID_DF_NAME)
    activity_field_id = field_id_by_name.get(ACTIVITY_ID_DF_NAME)
    if process_field_id is None or activity_field_id is None:
        return None

    values = (
        (
            await session.execute(
                select(DynamicFieldValue).where(
                    DynamicFieldValue.object_id == ticket_id,
                    DynamicFieldValue.field_id.in_([process_field_id, activity_field_id]),
                )
            )
        )
        .scalars()
        .all()
    )
    value_by_field_id = {v.field_id: v.value_text for v in values}
    process_entity_id = value_by_field_id.get(process_field_id)
    activity_entity_id = value_by_field_id.get(activity_field_id)
    if not process_entity_id or not activity_entity_id:
        return None

    return TicketProcessState(
        process_entity_id=process_entity_id, activity_entity_id=activity_entity_id
    )


@dataclass(frozen=True)
class TicketProcessCandidates:
    """Raw candidate activity dialogs / outgoing transitions for a ticket's
    current activity — WITHOUT any condition evaluation (subtask 2's job).
    """

    process: ProcessGraph
    activity_entity_id: str
    activity_dialogs: list[ActivityDialogNode]
    outgoing_transitions: list[TransitionNode]


async def get_ticket_process_candidates(
    session: AsyncSession, ticket_id: int
) -> TicketProcessCandidates | None:
    """Resolve the ticket's process state and load the candidate dialogs/
    transitions available from its current activity.

    Returns ``None`` if the ticket has no process state, the referenced
    process no longer exists, or the referenced activity is not part of the
    loaded process graph.
    """
    state = await get_ticket_process_state(session, ticket_id)
    if state is None:
        return None

    repository = ProcessRepository(session)
    process = await repository.get_process(state.process_entity_id)
    if process is None:
        return None

    activity = process.activities.get(state.activity_entity_id)
    if activity is None:
        return None

    return TicketProcessCandidates(
        process=process,
        activity_entity_id=activity.entity_id,
        activity_dialogs=activity.activity_dialogs,
        outgoing_transitions=activity.outgoing_transitions,
    )
