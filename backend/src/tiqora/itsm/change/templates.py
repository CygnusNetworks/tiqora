"""Change templates (design I8): what a template stores, its admin CRUD, and how it prefills a
change.

A template holds ``defaults`` (:class:`TemplateDefaults`: queue, services, CAB, texts, window
length, risk answers) and a list of :class:`TemplateTask`. :func:`expand_tasks` and
:func:`apply_template_defaults` are pure; the CRUD and :func:`resolve_defaults` read the database.
Template text (task bodies, plans, reason) is plain text, never HTML (ruling F25).

``is_standard`` belongs to ``change_type == "standard"`` templates only; a standard change can be
created from nothing else (``create_change`` checks it). References in a template (queue, services,
CAB, priority, risk answers) are checked when it is saved; when a change is created from it they
may have gone stale: stale ones are dropped and named as warnings, so a renamed or retired
service never blocks a change.

Import order: ``service`` imports this module lazily inside ``create_change`` (this module needs
``service`` and ``tasks``, and ``tasks`` imports ``service``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlalchemy import bindparam, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.utf8mb3 import replace_non_bmp
from tiqora.domain.ticket_access import CHUNK
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.models import (
    TiqoraItsmCab,
    TiqoraItsmChangeRiskQuestion,
    TiqoraItsmChangeTemplate,
)
from tiqora.itsm.change.phases import ChangeType
from tiqora.itsm.change.service import MAX_SERVICES, MAX_TEXT, ChangeCreateIn
from tiqora.itsm.change.settings import load_change_settings
from tiqora.itsm.change.tasks import TaskIn
from tiqora.jobs.service import utc_now

logger = logging.getLogger(__name__)

MAX_RISK_ANSWERS: Final = 100


class TemplateTask(BaseModel):
    key: str = Field(..., pattern=r"^[a-z0-9_]{1,50}$")
    title: str = Field(..., min_length=1, max_length=255)
    task_kind: Literal["workorder", "backout", "decision", "pir"] = "workorder"
    queue_id: int | None = None
    owner_id: int | None = None
    #: relative to the change's planned start
    offset_minutes: int = Field(0, ge=-43200, le=525600)
    duration_minutes: int | None = Field(default=None, ge=1, le=525600)
    body: str = Field("", max_length=20000)
    position: int = 0


def ordered_tasks(tasks: Sequence[TemplateTask]) -> list[TemplateTask]:
    """Template order: by ``position``, ties in list order."""
    return [t for _, t in sorted(enumerate(tasks), key=lambda item: (item[1].position, item[0]))]


def expand_tasks(tasks: Sequence[TemplateTask], planned_start: datetime | None) -> list[TaskIn]:
    """One :class:`TaskIn` per template task, in template order, ``position`` = its index.

    Times are only set when the change has a planned start (naive UTC): start = change start +
    offset, end = start + duration.
    """
    out: list[TaskIn] = []
    for index, task in enumerate(ordered_tasks(tasks)):
        start = (
            None
            if planned_start is None
            else planned_start + timedelta(minutes=task.offset_minutes)
        )
        end = None
        if start is not None and task.duration_minutes is not None:
            end = start + timedelta(minutes=task.duration_minutes)
        out.append(
            TaskIn(
                title=task.title,
                task_kind=task.task_kind,
                owner_id=task.owner_id,
                queue_id=task.queue_id,
                planned_start=start,
                planned_end=end,
                planned_effort_minutes=task.duration_minutes,
                body=task.body,
                position=index,
            )
        )
    return out


# --- defaults --------------------------------------------------------------------------------


class TemplateDefaults(BaseModel):
    """What a change created from the template starts with (all optional, plain text)."""

    model_config = ConfigDict(extra="forbid")

    #: offered to the create dialog as the title proposal; never forced on a title the agent typed
    title: str | None = Field(default=None, max_length=255)
    queue_id: int | None = None
    priority_id: int | None = None
    service_ids: list[int] = Field(default_factory=list, max_length=MAX_SERVICES)
    cab_id: int | None = None
    reason: str | None = Field(default=None, max_length=MAX_TEXT)
    implementation_plan: str | None = Field(default=None, max_length=MAX_TEXT)
    backout_plan: str | None = Field(default=None, max_length=MAX_TEXT)
    test_plan: str | None = Field(default=None, max_length=MAX_TEXT)
    #: question key -> answer key
    risk_answers: dict[str, str] = Field(default_factory=dict, max_length=MAX_RISK_ANSWERS)
    #: planned_end = planned_start + duration when only a start is given
    duration_minutes: int | None = Field(default=None, ge=1, le=525600)
    portal_visible: bool = False


class TemplateIn(BaseModel):
    """Body of the admin create/update."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    description: str | None = Field(default=None, max_length=5000)
    change_type: ChangeType
    is_standard: bool = False
    valid: bool = True
    defaults: TemplateDefaults = Field(default_factory=lambda: TemplateDefaults())
    tasks: list[TemplateTask] = Field(default_factory=list, max_length=200)


class TemplateView(BaseModel):
    id: int
    name: str
    description: str | None
    change_type: ChangeType
    is_standard: bool
    valid: bool
    #: the stored JSON no longer parses: defaults and tasks read as empty and no change can be
    #: created from it until an admin saves it again
    broken: bool
    defaults: TemplateDefaults
    tasks: list[TemplateTask]
    created_at: datetime
    created_by: int
    updated_at: datetime
    updated_by: int


def validate_template(
    *,
    change_type: str,
    is_standard: bool,
    tasks: Sequence[TemplateTask],
    max_tasks: int,
) -> None:
    """The rules that need no database (pure): :class:`ChangeError` 422, code ``template_*``."""
    if is_standard and change_type != "standard":
        raise ChangeError("template_standard_type", 422)
    if len(tasks) > max_tasks:
        raise ChangeError("template_too_many_tasks", 422, max_tasks=max_tasks)
    seen: set[str] = set()
    for task in tasks:
        if task.key in seen:
            raise ChangeError("template_duplicate_task_key", 422, key=task.key)
        seen.add(task.key)


def template_view(row: TiqoraItsmChangeTemplate) -> TemplateView:
    broken = False
    try:
        defaults = TemplateDefaults.model_validate(json.loads(row.defaults or "{}"))
        tasks = [TemplateTask.model_validate(t) for t in json.loads(row.tasks or "[]")]
    except (ValueError, TypeError, ValidationError):
        logger.warning("change_template_broken", extra={"template_id": row.id})
        broken, defaults, tasks = True, TemplateDefaults(), []
    return TemplateView(
        id=row.id,
        name=row.name,
        description=row.description,
        change_type=row.change_type,  # type: ignore[arg-type]
        is_standard=row.is_standard,
        valid=row.valid,
        broken=broken,
        defaults=defaults,
        tasks=tasks,
        created_at=row.created_at,
        created_by=row.created_by,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


def apply_template_defaults(data: ChangeCreateIn, template: TemplateView) -> ChangeCreateIn:
    """``data`` with the template's defaults where the caller said nothing (explicit input wins).

    Pure. The title is never replaced; the window end follows the start by the template's
    duration only when the caller gave a start and no end. The first template service becomes
    the ticket's own service, the rest the extra ones, unless the caller named services (an
    explicit ``service_ids: []`` counts as a choice).
    """
    d = template.defaults
    update: dict[str, object] = {}
    if data.queue_id is None and d.queue_id is not None:
        update["queue_id"] = d.queue_id
    if data.cab_id is None and d.cab_id is not None:
        update["cab_id"] = d.cab_id
    if data.reason is None and d.reason:
        update["reason"] = d.reason
    named_services = data.service_id is not None or "service_ids" in data.model_fields_set
    if not named_services and d.service_ids:
        update["service_id"] = d.service_ids[0]
        update["service_ids"] = list(d.service_ids[1:])
    if data.planned_start is not None and data.planned_end is None and d.duration_minutes:
        update["planned_end"] = data.planned_start + timedelta(minutes=d.duration_minutes)
    return data.model_copy(update=update) if update else data


# --- references ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StaleRef:
    #: ``queue`` | ``priority`` | ``service`` | ``cab`` | ``risk_answer`` | ``owner``
    kind: str
    #: the id, or the question key for ``risk_answer``
    ref: str

    @property
    def warning(self) -> str:
        if self.kind == "risk_answer":
            return f"risk_answer_stale:{self.ref}"
        return f"{self.kind}_not_found:{self.ref}"


#: table -> kind; constants, never input
_ID_TABLES: Final = {"queue": "queue", "priority": "ticket_priority", "service": "service"}


async def _existing(session: AsyncSession, table: str, ids: Sequence[int]) -> set[int]:
    wanted = sorted(set(ids))
    found: set[int] = set()
    stmt = text(f"SELECT id FROM {table} WHERE id IN :ids AND valid_id = 1").bindparams(  # noqa: S608
        bindparam("ids", expanding=True)
    )
    for start in range(0, len(wanted), CHUNK):
        rows = await session.execute(stmt, {"ids": wanted[start : start + CHUNK]})
        found.update(int(r[0]) for r in rows)
    return found


async def risk_answer_points(session: AsyncSession) -> dict[str, dict[str, int]]:
    """Valid questions: question key -> answer key -> points."""
    rows = await session.execute(
        select(
            TiqoraItsmChangeRiskQuestion.question_key, TiqoraItsmChangeRiskQuestion.answers
        ).where(TiqoraItsmChangeRiskQuestion.valid.is_(True))
    )
    out: dict[str, dict[str, int]] = {}
    for key, raw in rows:
        try:
            out[key] = {str(a["key"]): int(a["points"]) for a in json.loads(raw)}
        except (ValueError, TypeError, KeyError):
            continue
    return out


async def find_stale_refs(
    session: AsyncSession,
    defaults: TemplateDefaults,
    tasks: Sequence[TemplateTask] = (),
    *,
    points: dict[str, dict[str, int]] | None = None,
) -> list[StaleRef]:
    """References of the template that do not exist (any more), in a fixed order."""
    stale: list[StaleRef] = []
    queues = [q for q in (defaults.queue_id, *(t.queue_id for t in tasks)) if q is not None]
    if queues:
        ok = await _existing(session, _ID_TABLES["queue"], queues)
        stale += [StaleRef("queue", str(q)) for q in dict.fromkeys(queues) if q not in ok]
    if defaults.priority_id is not None and not await _existing(
        session, _ID_TABLES["priority"], [defaults.priority_id]
    ):
        stale.append(StaleRef("priority", str(defaults.priority_id)))
    if defaults.service_ids:
        ok = await _existing(session, _ID_TABLES["service"], defaults.service_ids)
        stale += [
            StaleRef("service", str(s)) for s in dict.fromkeys(defaults.service_ids) if s not in ok
        ]
    if defaults.cab_id is not None:
        cab = await session.get(TiqoraItsmCab, defaults.cab_id)
        if cab is None or not cab.valid:
            stale.append(StaleRef("cab", str(defaults.cab_id)))
    owners = [t.owner_id for t in tasks if t.owner_id is not None]
    if owners:
        ok = await _existing(session, "users", owners)
        stale += [StaleRef("owner", str(o)) for o in dict.fromkeys(owners) if o not in ok]
    if defaults.risk_answers:
        known = await risk_answer_points(session) if points is None else points
        for question, answer in defaults.risk_answers.items():
            if answer not in known.get(question, {}):
                stale.append(StaleRef("risk_answer", question))
    return stale


@dataclass(frozen=True, slots=True)
class ResolvedDefaults:
    """The template's defaults without stale references, the risk answers with their points and
    one warning per dropped reference."""

    defaults: TemplateDefaults
    #: ``(question_key, answer_key, points)``
    risk_answers: tuple[tuple[str, str, int], ...]
    warnings: tuple[str, ...]


async def resolve_defaults(session: AsyncSession, template: TemplateView) -> ResolvedDefaults:
    """Use at change creation: stale references are dropped and logged, never fatal."""
    d = template.defaults
    points = await risk_answer_points(session)
    stale = await find_stale_refs(session, d, template.tasks, points=points)
    # a stale task queue/owner fails that task in the job (recorded there), not the change
    dropped = {(s.kind, s.ref) for s in stale if s.kind != "owner"}
    kept = d.model_copy(
        update={
            "queue_id": None if ("queue", str(d.queue_id)) in dropped else d.queue_id,
            "priority_id": None if ("priority", str(d.priority_id)) in dropped else d.priority_id,
            "cab_id": None if ("cab", str(d.cab_id)) in dropped else d.cab_id,
            "service_ids": [s for s in d.service_ids if ("service", str(s)) not in dropped],
            "risk_answers": {
                q: a for q, a in d.risk_answers.items() if ("risk_answer", q) not in dropped
            },
        }
    )
    answers = tuple((q, a, points[q][a]) for q, a in kept.risk_answers.items())
    warnings = tuple(
        s.warning for s in stale if s.kind in {"queue", "priority", "service", "cab", "risk_answer"}
    )
    if warnings:
        logger.warning(
            "change_template_stale_defaults",
            extra={"template_id": template.id, "warnings": list(warnings)},
        )
    return ResolvedDefaults(kept, answers, warnings)


_STALE_CODES: Final = {
    "queue": "template_queue_not_found",
    "priority": "template_priority_not_found",
    "service": "template_service_not_found",
    "cab": "template_cab_not_found",
    "owner": "template_owner_not_found",
    "risk_answer": "template_risk_answer_invalid",
}


# --- CRUD (admin) ----------------------------------------------------------------------------


async def get_template(session: AsyncSession, template_id: int) -> TemplateView:
    row = await session.get(TiqoraItsmChangeTemplate, template_id)
    if row is None:
        raise ChangeError("template_not_found", 404)
    return template_view(row)


async def list_templates(session: AsyncSession, *, include_invalid: bool) -> list[TemplateView]:
    stmt = select(TiqoraItsmChangeTemplate).order_by(
        TiqoraItsmChangeTemplate.name, TiqoraItsmChangeTemplate.id
    )
    if not include_invalid:
        stmt = stmt.where(TiqoraItsmChangeTemplate.valid.is_(True))
    return [template_view(r) for r in (await session.execute(stmt)).scalars()]


async def _validated(
    session: AsyncSession, settings: Settings, data: TemplateIn, *, own_id: int | None
) -> None:
    cfg = await load_change_settings(session, settings)
    validate_template(
        change_type=data.change_type,
        is_standard=data.is_standard,
        tasks=data.tasks,
        max_tasks=cfg.max_tasks,
    )
    stale = await find_stale_refs(session, data.defaults, data.tasks)
    if stale:
        first = stale[0]
        raise ChangeError(_STALE_CODES[first.kind], 422, ref=first.ref)
    clash = (
        await session.execute(
            select(TiqoraItsmChangeTemplate.id).where(
                TiqoraItsmChangeTemplate.name == replace_non_bmp(data.name),
                TiqoraItsmChangeTemplate.id != (own_id or 0),
            )
        )
    ).first()
    if clash is not None:
        raise ChangeError("template_name_exists", 409)


def _dump_defaults(data: TemplateIn) -> str:
    dumped = json.dumps(data.defaults.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    return replace_non_bmp(dumped) or ""


def _dump_tasks(data: TemplateIn) -> str:
    return (
        replace_non_bmp(
            json.dumps([t.model_dump(mode="json") for t in data.tasks], ensure_ascii=False)
        )
        or ""
    )


async def create_template(
    session: AsyncSession, settings: Settings, *, data: TemplateIn, user_id: int
) -> TemplateView:
    """Admin. Services never commit; the route does."""
    await _validated(session, settings, data, own_id=None)
    now = utc_now()
    row = TiqoraItsmChangeTemplate(
        name=replace_non_bmp(data.name) or "",
        description=replace_non_bmp(data.description),
        change_type=data.change_type,
        is_standard=data.is_standard,
        valid=data.valid,
        defaults=_dump_defaults(data),
        tasks=_dump_tasks(data),
        created_at=now,
        created_by=user_id,
        updated_at=now,
        updated_by=user_id,
    )
    try:
        async with session.begin_nested():  # a concurrent create of the same name loses here
            session.add(row)
    except IntegrityError as exc:
        raise ChangeError("template_name_exists", 409) from exc
    return template_view(row)


async def update_template(
    session: AsyncSession,
    settings: Settings,
    *,
    template_id: int,
    data: TemplateIn,
    user_id: int,
) -> TemplateView:
    """Admin. Replaces the whole template; changes already created from it stay as they are."""
    row = await session.get(TiqoraItsmChangeTemplate, template_id)
    if row is None:
        raise ChangeError("template_not_found", 404)
    await _validated(session, settings, data, own_id=template_id)
    row.name = replace_non_bmp(data.name) or ""
    row.description = replace_non_bmp(data.description)
    row.change_type = data.change_type
    row.is_standard = data.is_standard
    row.valid = data.valid
    row.defaults = _dump_defaults(data)
    row.tasks = _dump_tasks(data)
    row.updated_at = utc_now()
    row.updated_by = user_id
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise ChangeError("template_name_exists", 409) from exc
    return template_view(row)


async def delete_template(session: AsyncSession, *, template_id: int) -> None:
    """Admin. Changes created from it keep their data: ``template_id`` becomes NULL (the foreign
    key's ``ON DELETE SET NULL``)."""
    row = await session.get(TiqoraItsmChangeTemplate, template_id)
    if row is None:
        raise ChangeError("template_not_found", 404)
    await session.delete(row)
    await session.flush()
