"""The path a ticket has taken through its process so far (plan Task 7, D8).

Znuny has no dedicated process-history table. Every activity change is a
``TicketDynamicFieldUpdate`` history row for the dynamic field
``ProcessManagementActivityID`` (``Ticket.pm`` / ``DynamicField/ObjectType/Ticket.pm``,
name ``%%FieldName%%<Field>%%Value%%<new>%%OldValue%%<old>``), so the path is read back from
``ticket_history``. Only rows after the newest ``ProcessManagementProcessID`` row that set the
ticket's current process belong to the current enrolment.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.ticket import TicketHistory
from tiqora.db.legacy.user import Users
from tiqora.domain.schemas import UtcDateTime
from tiqora.process.source import active_process_source
from tiqora.process.ticket_state import (
    ACTIVITY_ID_DF_NAME,
    PROCESS_ID_DF_NAME,
    get_ticket_process_state,
)
from tiqora.znuny.history import TYPE_DYNAMIC_FIELD_UPDATE, resolve_history_type_id

MAX_PATH_STEPS = 200

_PREFIX = "%%FieldName%%{field}%%Value%%"
_ACTIVITY_PREFIX = _PREFIX.format(field=ACTIVITY_ID_DF_NAME)
_PROCESS_PREFIX = _PREFIX.format(field=PROCESS_ID_DF_NAME)
_OLD_VALUE_MARK = "%%OldValue%%"


class ProcessPathStepOut(BaseModel):
    activity_entity_id: str
    activity_name: str  # "" when the activity no longer exists in the active source
    entered_at: UtcDateTime  # ticket_history.create_time (naive UTC)
    entered_by: int  # ticket_history.create_by
    entered_by_name: str  # users full name, "" when unknown
    current: bool


class ProcessPathOut(BaseModel):
    process_entity_id: str | None
    steps: list[ProcessPathStepOut]  # oldest first; max MAX_PATH_STEPS


def _value_after(name: str, prefix: str) -> str | None:
    """The new value of a DF history name, or ``None`` when *name* is another field's row.

    The prefix is tested here and not with ``LIKE`` because ``%`` is a ``LIKE`` wildcard.
    """
    if not name.startswith(prefix):
        return None
    value = name[len(prefix) :]
    cut = value.find(_OLD_VALUE_MARK)
    return value if cut < 0 else value[:cut]


async def load_process_path(session: AsyncSession, ticket_id: int) -> ProcessPathOut:
    """Oldest-first path of *ticket_id*; empty (``process_entity_id`` None) outside a process."""
    state = await get_ticket_process_state(session, ticket_id)
    if state is None:
        return ProcessPathOut(process_entity_id=None, steps=[])

    type_id = await resolve_history_type_id(session, TYPE_DYNAMIC_FIELD_UPDATE)
    rows = (
        await session.execute(
            select(
                TicketHistory.id,
                TicketHistory.name,
                TicketHistory.create_time,
                TicketHistory.create_by,
            )
            .where(
                TicketHistory.ticket_id == ticket_id,
                TicketHistory.history_type_id == type_id,
            )
            .order_by(TicketHistory.id)
        )
    ).all()

    # Start of the current enrolment: the newest row that set the current process.
    start_id = 0
    for row_id, name, _, _ in rows:
        if _value_after(name, _PROCESS_PREFIX) == state.process_entity_id:
            start_id = int(row_id)

    entered: list[tuple[str, datetime, int]] = []
    for row_id, name, created, created_by in rows:
        if int(row_id) < start_id:
            continue
        activity = _value_after(name, _ACTIVITY_PREFIX)
        if activity:
            entered.append((activity, created, int(created_by)))
    entered = entered[-MAX_PATH_STEPS:]
    if not entered:
        return ProcessPathOut(process_entity_id=state.process_entity_id, steps=[])

    activity_ids = {a for a, _, _ in entered}
    source = await active_process_source(session)
    names = {eid: rec.name for eid, rec in (await source.load("Activity", activity_ids)).items()}
    user_ids = {u for _, _, u in entered}
    user_rows = (
        await session.execute(
            select(Users.id, Users.first_name, Users.last_name).where(Users.id.in_(user_ids))
        )
    ).all()
    user_names = {int(r[0]): f"{r[1]} {r[2]}".strip() for r in user_rows}

    last = len(entered) - 1
    steps = [
        ProcessPathStepOut(
            activity_entity_id=activity,
            activity_name=names.get(activity, ""),
            entered_at=created,
            entered_by=created_by,
            entered_by_name=user_names.get(created_by, ""),
            current=i == last and activity == state.activity_entity_id,
        )
        for i, (activity, created, created_by) in enumerate(entered)
    ]
    return ProcessPathOut(process_entity_id=state.process_entity_id, steps=steps)
