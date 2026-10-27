"""Change tasks: child tickets of a change with order, kind and planned window (design I7).

A task is a Znuny ticket of ``ItsmSettings.change_task_type`` linked to the change as its child
(``ParentChild``) plus a ``tiqora_itsm_change_task`` row for what a ticket has no field for
(position, kind, planned/actual window, effort, template key). Assignment is the ticket owner,
progress the ticket state: a task is finished when its state type is closed, removed or merged.

Task queue (open question 13): the task's own queue, else ``itsm_change.task_queue_id``, else the
queue of the change; the creator needs ``create`` there. Owner: the given agent (valid, ``rw`` on
the task queue) or the creating agent. Customer, service and priority are copied from the change.

Services never commit; the route and the job do. Unreadable tasks are left out of every list and
answer 404 like missing ones.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.db.engine import get_session_factory
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.domain.ticket_access import CHUNK, TicketAccessDenied, readable_tickets, require_ticket
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    InvalidInput,
    LinkConflict,
    TicketIn,
    TicketWriteService,
    link_tickets,
)
from tiqora.domain.ticket_write_service import TicketAccessDenied as WriteAccessDenied
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.gate import require_change_mode
from tiqora.itsm.change.models import (
    TiqoraItsmChange,
    TiqoraItsmChangeTask,
    TiqoraItsmChangeTemplate,
)
from tiqora.itsm.change.phases import FINAL_PHASES
from tiqora.itsm.change.service import (
    CLOSED_STATE_TYPES,
    INITIAL_STATE,
    _check_window,
    _clean,
    _escape_html,
    _lookup_id,
    _naive,
    record_event,
)
from tiqora.itsm.change.settings import load_change_settings
from tiqora.itsm.settings import load_itsm_settings
from tiqora.jobs.context import JobCancelled, JobContext, JobFailed
from tiqora.jobs.registry import JobHandler
from tiqora.jobs.service import enqueue_job, utc_now
from tiqora.permissions.engine import PermissionEngine
from tiqora.plugins.sync_hooks import OperationVetoed
from tiqora.znuny.sysconfig import SysConfig

logger = logging.getLogger(__name__)

TaskKind = Literal["workorder", "backout", "decision", "pir"]
ITSM_CHANGE_TASKS_KIND: Final = "core.itsm_change_tasks"
MAX_TEXT: Final = 100000


# --- models ----------------------------------------------------------------------------------


class TaskIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    task_kind: TaskKind = "workorder"
    owner_id: int | None = None
    queue_id: int | None = None
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    planned_effort_minutes: int | None = Field(None, ge=0, le=100000)
    #: plain text; becomes one internal note, escaped (ruling F25)
    body: str = Field("", max_length=MAX_TEXT)
    #: ``None`` = last; a number inserts before the task now at that place
    position: int | None = Field(None, ge=0)

    @model_validator(mode="after")
    def _window(self) -> TaskIn:
        _check_window(self.planned_start, self.planned_end)
        return self


class TaskUpdateIn(BaseModel):
    """Only the fields that are sent change; ``null`` clears a time or the effort."""

    task_kind: TaskKind | None = None
    owner_id: int | None = None
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    planned_effort_minutes: int | None = Field(None, ge=0, le=100000)

    @model_validator(mode="after")
    def _fields(self) -> TaskUpdateIn:
        for name in ("task_kind", "owner_id"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} darf nicht leer sein.")
        _check_window(self.planned_start, self.planned_end)
        _check_window(self.actual_start, self.actual_end)
        return self


class TaskView(BaseModel):
    """A task for display (times are naive UTC)."""

    ticket_id: int
    tn: str
    title: str
    queue_id: int
    owner_id: int
    state: str
    state_type: str
    finished: bool
    task_kind: TaskKind
    position: int
    planned_start: datetime | None
    planned_end: datetime | None
    actual_start: datetime | None
    actual_end: datetime | None
    planned_effort_minutes: int | None
    template_task_key: str | None


class _Detail(BaseModel):
    tn: str
    title: str
    queue_id: int
    owner_id: int
    state: str
    state_type: str


# --- helpers ---------------------------------------------------------------------------------


async def _change_ticket(
    session: AsyncSession, ticket_id: int, *, user_id: int, write: bool
) -> tuple[int, TiqoraItsmChange]:
    """Queue of the change ticket and its record, locked when ``write`` (serialises adds)."""
    try:
        facts = await require_ticket(session, ticket_id, user_id=user_id, write=write)
    except TicketAccessDenied as exc:
        raise ChangeError(exc.code, exc.http_status) from exc
    stmt = select(TiqoraItsmChange).where(TiqoraItsmChange.ticket_id == ticket_id)
    if write:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ChangeError("change_not_found", 404)
    return facts.queue_id, row


async def _task_rows(session: AsyncSession, change_ticket_id: int) -> list[TiqoraItsmChangeTask]:
    rows = await session.execute(
        select(TiqoraItsmChangeTask)
        .where(TiqoraItsmChangeTask.change_ticket_id == change_ticket_id)
        .order_by(TiqoraItsmChangeTask.position, TiqoraItsmChangeTask.ticket_id)
    )
    return list(rows.scalars())


async def _details(session: AsyncSession, ids: Sequence[int]) -> dict[int, _Detail]:
    """Display facts of tickets, whatever the caller may read (callers filter first)."""
    found: dict[int, _Detail] = {}
    wanted = sorted(set(ids))
    stmt = text(
        "SELECT t.id, t.tn, t.title, t.queue_id, t.user_id, ts.name, tst.name FROM ticket t"
        " LEFT JOIN ticket_state ts ON ts.id = t.ticket_state_id"
        " LEFT JOIN ticket_state_type tst ON tst.id = ts.type_id WHERE t.id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    for start in range(0, len(wanted), CHUNK):
        for r in await session.execute(stmt, {"ids": wanted[start : start + CHUNK]}):
            found[int(r[0])] = _Detail(
                tn=str(r[1]),
                title=str(r[2] or ""),
                queue_id=int(r[3]),
                owner_id=int(r[4]),
                state=str(r[5] or ""),
                state_type=str(r[6] or ""),
            )
    return found


def _view(row: TiqoraItsmChangeTask, detail: _Detail) -> TaskView:
    return TaskView(
        ticket_id=row.ticket_id,
        tn=detail.tn,
        title=detail.title,
        queue_id=detail.queue_id,
        owner_id=detail.owner_id,
        state=detail.state,
        state_type=detail.state_type,
        finished=detail.state_type in CLOSED_STATE_TYPES,
        task_kind=row.task_kind,  # type: ignore[arg-type]
        position=row.position,
        planned_start=row.planned_start,
        planned_end=row.planned_end,
        actual_start=row.actual_start,
        actual_end=row.actual_end,
        planned_effort_minutes=row.planned_effort_minutes,
        template_task_key=row.template_task_key,
    )


async def _views(session: AsyncSession, rows: Sequence[TiqoraItsmChangeTask]) -> list[TaskView]:
    details = await _details(session, [r.ticket_id for r in rows])
    return [_view(r, details[r.ticket_id]) for r in rows if r.ticket_id in details]


async def _check_owner(session: AsyncSession, owner_id: int, queue_id: int) -> None:
    """A valid agent who may work on the task queue (``rw``)."""
    valid = (
        await session.execute(
            text("SELECT 1 FROM users WHERE id = :u AND valid_id = 1"), {"u": owner_id}
        )
    ).first()
    if valid is None or not await PermissionEngine(session).check(owner_id, queue_id, "rw"):
        raise ChangeError("owner_invalid", 422)


async def _check_queue(session: AsyncSession, queue_id: int) -> None:
    found = (
        await session.execute(
            text("SELECT 1 FROM queue WHERE id = :q AND valid_id = 1"), {"q": queue_id}
        )
    ).first()
    if found is None:
        raise ChangeError("queue_not_found", 422)


def _renumber(rows: Sequence[TiqoraItsmChangeTask]) -> None:
    for index, row in enumerate(rows):
        if row.position != index:
            row.position = index


# --- add -------------------------------------------------------------------------------------


async def _add_task(
    session: AsyncSession,
    settings: Settings,
    sysconfig: SysConfig,
    *,
    change_ticket_id: int,
    data: TaskIn,
    user_id: int,
    template_task_key: str | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[TaskView, bool]:
    """:func:`add_task` that also says whether a ticket was created (``False`` = key existed)."""
    await require_change_mode(session, settings)
    cfg = await load_change_settings(session, settings)
    itsm_cfg = await load_itsm_settings(session, settings)
    change_queue_id, change = await _change_ticket(
        session, change_ticket_id, user_id=user_id, write=True
    )
    existing = await _task_rows(session, change_ticket_id)
    if template_task_key is not None:
        for row in existing:
            if row.template_task_key == template_task_key:
                return (await _views(session, [row]))[0], False
    if change.phase in FINAL_PHASES:
        raise ChangeError("change_final", 409)
    if len(existing) >= cfg.max_tasks:
        raise ChangeError("max_tasks_reached", 409, max_tasks=cfg.max_tasks)

    queue_id = data.queue_id or cfg.task_queue_id or change_queue_id
    await _check_queue(session, queue_id)
    owner_id = data.owner_id if data.owner_id is not None else user_id
    if data.owner_id is not None:
        await _check_owner(session, data.owner_id, queue_id)
    type_id = await _lookup_id(session, "ticket_type", itsm_cfg.change_task_type)
    state_id = await _lookup_id(session, "ticket_state", INITIAL_STATE)
    if type_id is None or state_id is None:
        raise ChangeError("change_setup_missing", 409)
    parent = (
        await session.execute(
            text(
                "SELECT customer_id, customer_user_id, service_id, ticket_priority_id"
                " FROM ticket WHERE id = :t"
            ),
            {"t": change_ticket_id},
        )
    ).one()

    note = _clean(data.body) or data.title
    writer = TicketWriteService(session, session_factory or get_session_factory(), sysconfig)
    try:
        task_id = await writer.create_ticket(
            user_id,
            TicketIn(
                title=data.title,
                queue_id=queue_id,
                state_id=state_id,
                priority_id=int(parent[3]),
                owner_id=owner_id,
                type_id=type_id,
                service_id=None if parent[2] is None else int(parent[2]),
                customer_id=parent[0],
                customer_user_id=parent[1],
                article=ArticleIn(
                    sender_type="agent",
                    is_visible_for_customer=False,
                    subject=data.title,
                    body=_escape_html(note),
                    content_type="text/html; charset=utf-8",
                    channel="note",
                ),
            ),
        )
        await link_tickets(
            session,
            source_ticket_id=change_ticket_id,
            target_ticket_id=task_id,
            link_type=cfg.task_link_type,
            user_id=user_id,
        )
    except WriteAccessDenied as exc:
        raise ChangeError("forbidden", 403) from exc
    except OperationVetoed as exc:
        raise ChangeError("vetoed", 409, message=str(exc)) from exc
    except LinkConflict as exc:
        raise ChangeError("task_link_failed", 409, reason=exc.reason) from exc
    except InvalidInput as exc:
        raise ChangeError("invalid_input", 422, message=str(exc)) from exc

    index = len(existing) if data.position is None else min(data.position, len(existing))
    row = TiqoraItsmChangeTask(
        ticket_id=task_id,
        change_ticket_id=change_ticket_id,
        position=index,
        task_kind=data.task_kind,
        planned_start=_naive(data.planned_start),
        planned_end=_naive(data.planned_end),
        planned_effort_minutes=data.planned_effort_minutes,
        template_task_key=template_task_key,
        created_at=utc_now(),
        created_by=user_id,
    )
    session.add(row)
    _renumber([*existing[:index], row, *existing[index:]])
    await session.flush()
    await record_event(
        session,
        ticket_id=change_ticket_id,
        event="task_added",
        user_id=user_id,
        detail={"task_ticket_id": task_id, "task_kind": data.task_kind},
    )
    return (await _views(session, [row]))[0], True


async def add_task(
    session: AsyncSession,
    settings: Settings,
    sysconfig: SysConfig,
    *,
    change_ticket_id: int,
    data: TaskIn,
    user_id: int,
    template_task_key: str | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> TaskView:
    """A new task ticket under the change (needs ``rw`` on the change and ``create`` on the task
    queue). With ``template_task_key`` the call is idempotent: the task that already carries the
    key is returned, whatever ``data`` says."""
    view, _ = await _add_task(
        session,
        settings,
        sysconfig,
        change_ticket_id=change_ticket_id,
        data=data,
        user_id=user_id,
        template_task_key=template_task_key,
        session_factory=session_factory,
    )
    return view


# --- read / change ---------------------------------------------------------------------------


async def list_tasks(
    session: AsyncSession, *, change_ticket_id: int, user_id: int
) -> list[TaskView]:
    """The change's tasks in order; task tickets the caller cannot read are left out."""
    await _change_ticket(session, change_ticket_id, user_id=user_id, write=False)
    rows = await _task_rows(session, change_ticket_id)
    readable = await readable_tickets(session, [r.ticket_id for r in rows], user_id=user_id)
    return await _views(session, [r for r in rows if r.ticket_id in readable])


async def update_task(
    session: AsyncSession,
    settings: Settings,
    sysconfig: SysConfig,
    *,
    change_ticket_id: int,
    task_ticket_id: int,
    data: TaskUpdateIn,
    user_id: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> TaskView:
    """Kind, window, effort and owner of one task (needs ``rw`` on the task ticket)."""
    await require_change_mode(session, settings)
    await _change_ticket(session, change_ticket_id, user_id=user_id, write=False)
    rows = [r for r in await _task_rows(session, change_ticket_id) if r.ticket_id == task_ticket_id]
    if not rows:
        raise ChangeError("task_not_found", 404)
    row = rows[0]
    try:
        facts = await require_ticket(session, task_ticket_id, user_id=user_id, write=True)
    except TicketAccessDenied as exc:
        if exc.http_status == 404:
            raise ChangeError("task_not_found", 404) from exc
        raise ChangeError(exc.code, exc.http_status) from exc

    values = {name: getattr(data, name) for name in data.model_fields_set}
    window = {
        "planned_start": row.planned_start,
        "planned_end": row.planned_end,
        "actual_start": row.actual_start,
        "actual_end": row.actual_end,
    }
    for name in window:
        if name in values:
            window[name] = _naive(values[name])
    for start, end in (("planned_start", "planned_end"), ("actual_start", "actual_end")):
        left, right = window[start], window[end]
        if left is not None and right is not None and right <= left:
            raise ChangeError("invalid_window", 422)

    owner_id = values.get("owner_id")
    if owner_id is not None:
        await _check_owner(session, owner_id, facts.queue_id)
        writer = TicketWriteService(session, session_factory or get_session_factory(), sysconfig)
        try:
            await writer.assign_owner(user_id, task_ticket_id, owner_id)
        except WriteAccessDenied as exc:
            raise ChangeError("forbidden", 403) from exc
        except OperationVetoed as exc:
            raise ChangeError("vetoed", 409, message=str(exc)) from exc
    for name, value in window.items():
        setattr(row, name, value)
    if "task_kind" in values:
        row.task_kind = str(values["task_kind"])
    if "planned_effort_minutes" in values:
        row.planned_effort_minutes = values["planned_effort_minutes"]
    await session.flush()
    return (await _views(session, [row]))[0]


async def reorder_tasks(
    session: AsyncSession,
    settings: Settings,
    *,
    change_ticket_id: int,
    order: Sequence[int],
    user_id: int,
) -> list[TaskView]:
    """Put the caller's tasks in ``order`` (every readable task, once).

    A task the caller cannot read keeps its place; the readable ones are redistributed over the
    places they held, so the answer reveals nothing about hidden tasks (``order_invalid`` 422).
    """
    await require_change_mode(session, settings)
    await _change_ticket(session, change_ticket_id, user_id=user_id, write=True)
    rows = await _task_rows(session, change_ticket_id)
    readable = await readable_tickets(session, [r.ticket_id for r in rows], user_id=user_id)
    mine = [r for r in rows if r.ticket_id in readable]
    if len(set(order)) != len(order) or set(order) != {r.ticket_id for r in mine}:
        raise ChangeError("order_invalid", 422)
    by_id = {r.ticket_id: r for r in mine}
    for ticket_id, position in zip(order, sorted(r.position for r in mine), strict=True):
        by_id[ticket_id].position = position
    await session.flush()
    return await list_tasks(session, change_ticket_id=change_ticket_id, user_id=user_id)


async def task_state_counts(session: AsyncSession, change_ticket_id: int) -> tuple[int, int]:
    """``(open, total)`` over every task of the change, whoever may read them: the phase guard
    counts them all. Tasks whose ticket is gone are not counted; closed, removed and merged
    state types are finished."""
    rows = await _task_rows(session, change_ticket_id)
    details = await _details(session, [r.ticket_id for r in rows])
    total = len(details)
    open_count = sum(1 for d in details.values() if d.state_type not in CLOSED_STATE_TYPES)
    return open_count, total


# --- job -------------------------------------------------------------------------------------


class ChangeTasksPayload(BaseModel):
    change_ticket_id: int
    template_id: int
    user_id: int


async def enqueue_change_tasks(
    session: AsyncSession, *, change_ticket_id: int, template_id: int, user_id: int
) -> TiqoraJob:
    """Queue the template-task job (flushes, never commits); one active job per change."""
    return await enqueue_job(
        session,
        kind=ITSM_CHANGE_TASKS_KIND,
        payload=ChangeTasksPayload(
            change_ticket_id=change_ticket_id, template_id=template_id, user_id=user_id
        ),
        created_by=user_id,
        dedupe_key=f"itsm_change_tasks:{change_ticket_id}",
    )


async def run_change_tasks_job(ctx: JobContext, payload: ChangeTasksPayload) -> dict[str, Any]:
    """One task per template task, a commit per task.

    Each task carries its template key, so a requeued claim (worker restart) finds what exists
    and creates the rest; for the same reason the job passes no ``ctx.checkpoint`` (it would
    forbid exactly that requeue). The creator's ``rw`` is checked again for every task.
    Result ``{"created", "existing", "failed": [{key, code}]}``.
    """
    from tiqora.itsm.change.templates import TemplateTask, expand_tasks, ordered_tasks

    async with ctx.session_factory() as session:
        template = await session.get(TiqoraItsmChangeTemplate, payload.template_id)
        change = await session.get(TiqoraItsmChange, payload.change_ticket_id)
        if template is None:
            raise JobFailed("Die Change-Vorlage existiert nicht mehr.")
        if change is None:
            raise JobFailed("Der Change existiert nicht mehr.")
        try:
            tasks = ordered_tasks(
                [TemplateTask.model_validate(t) for t in json.loads(template.tasks)]
            )
        except ValueError as exc:
            raise JobFailed("Die Aufgaben der Change-Vorlage sind ungültig.") from exc
        inputs = expand_tasks(tasks, change.planned_start)

    created = existing = 0
    failed: list[dict[str, str]] = []
    cancelled = False
    for index, (task, data) in enumerate(zip(tasks, inputs, strict=True), start=1):
        async with ctx.session_factory() as session:
            try:
                async with session.begin_nested():
                    _, was_created = await _add_task(
                        session,
                        ctx.settings,
                        SysConfig(session),
                        change_ticket_id=payload.change_ticket_id,
                        data=data,
                        user_id=payload.user_id,
                        template_task_key=task.key,
                        session_factory=ctx.session_factory,
                    )
                if was_created:
                    created += 1
                else:
                    existing += 1
            except ChangeError as exc:
                failed.append({"key": task.key, "code": exc.error_code})
            except Exception as exc:
                logger.warning(
                    "itsm_change_task_failed",
                    extra={
                        "job_id": ctx.job_id,
                        "change_ticket_id": payload.change_ticket_id,
                        "key": task.key,
                        "error": type(exc).__name__,
                    },
                )
                failed.append({"key": task.key, "code": "error"})
            await session.commit()
        try:
            await ctx.progress(index, len(tasks))
        except JobCancelled:
            cancelled = True
            break
    result: dict[str, Any] = {"created": created, "existing": existing, "failed": failed}
    if cancelled:
        result["cancelled"] = True
    return result


ITSM_CHANGE_TASKS_JOB = JobHandler(
    kind=ITSM_CHANGE_TASKS_KIND,
    payload_model=ChangeTasksPayload,
    run=run_change_tasks_job,
    title_key="jobs.kinds.core.itsm_change_tasks",
    timeout_seconds=1800,
    max_attempts=2,
    requeue_if_interrupted=True,
    result_private=True,
)
