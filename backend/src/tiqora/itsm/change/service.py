"""Change records on change tickets: create, adopt, read, update, the dynamic-field mirror, the
event timeline and the listener point (design I6).

A change is a Znuny ticket of ``ItsmSettings.change_type`` plus a ``tiqora_itsm_change`` record
(Mode B only). The phase lives in the record; ``ITSMChangeType`` / ``ITSMChangePhase`` mirror it
into Znuny-visible dynamic fields through ``update_dynamic_field`` (history
``TicketDynamicFieldUpdate``, a no-op when nothing changed). Nothing here sets a phase except the
initial ``draft`` of :func:`create_change` / :func:`adopt_ticket`; every later phase change is the
phase machine's (``transitions.transition``), and ``ChangeUpdateIn`` has no phase field.

Every phase change on any path writes exactly one ``event="phase"`` row through
:func:`record_event`; the creating paths write it next to ``created`` / ``adopted``.

Services never commit: the route handler does. Unreadable and missing tickets collapse to one
404; a write without ``rw`` is 403 (``tiqora.domain.ticket_access``).
"""

from __future__ import annotations

import html
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.calendar.recurrence import to_utc_naive
from tiqora.config import Settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.dynamic_field_values import get_df_values, load_dynamic_field
from tiqora.domain.ticket_access import (
    CHUNK,
    TicketAccessDenied,
    TicketFacts,
    require_ticket,
)
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    InvalidInput,
    TicketIn,
    TicketWriteService,
    update_dynamic_field,
)
from tiqora.domain.ticket_write_service import TicketAccessDenied as WriteAccessDenied
from tiqora.extensions import ExtensionPoint, get_registry
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.gate import is_change_manager, require_change_mode
from tiqora.itsm.change.models import (
    TiqoraItsmCab,
    TiqoraItsmChange,
    TiqoraItsmChangeEvent,
    TiqoraItsmChangeRiskAnswer,
    TiqoraItsmChangeService,
    TiqoraItsmChangeTemplate,
)
from tiqora.itsm.change.phases import (
    FINAL_PHASES,
    REOPENABLE_PHASES,
    ChangeType,
    Phase,
    allowed_targets,
)
from tiqora.itsm.change.settings import ItsmChangeSettings, load_change_settings
from tiqora.itsm.ci_provider import CiError, CiRef
from tiqora.itsm.providers import get_ci_provider
from tiqora.itsm.settings import load_itsm_settings
from tiqora.jobs.service import utc_now
from tiqora.permissions.engine import PermissionEngine
from tiqora.plugins.sync_hooks import OperationVetoed
from tiqora.process.ticket_state import get_ticket_process_state
from tiqora.znuny.sysconfig import SysConfig

logger = logging.getLogger(__name__)

#: fixed names, created by the setup group ``change`` (``TIQORA_CHANGE_DYNAMIC_FIELDS``)
TYPE_FIELD: Final = "ITSMChangeType"
PHASE_FIELD: Final = "ITSMChangePhase"

#: Znuny ticket state / priority a new change starts with
INITIAL_STATE: Final = "new"
DEFAULT_PRIORITY: Final = "3 normal"
MAX_TEXT: Final = 100000
#: Znuny state types of a finished ticket
CLOSED_STATE_TYPES: Final = frozenset({"closed", "removed", "merged"})
MAX_SERVICES: Final = 50
#: plans the CAB decides on (frozen while the change is in ``approval``)
_APPROVAL_LOCKED_TEXTS: Final = frozenset({"implementation_plan", "backout_plan", "test_plan"})


# --- models ----------------------------------------------------------------------------------


class ChangeRecord(BaseModel):
    """Frozen copy of a ``tiqora_itsm_change`` row (the ORM instance stays in the session)."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    ticket_id: int
    change_type: ChangeType
    phase: Phase
    outcome: str | None
    template_id: int | None
    cab_id: int | None
    planned_start: datetime | None
    planned_end: datetime | None
    actual_start: datetime | None
    actual_end: datetime | None
    reason: str | None
    implementation_plan: str | None
    backout_plan: str | None
    test_plan: str | None
    pir_notes: str | None
    risk_score: int | None
    risk_level: str | None
    risk_evaluated_at: datetime | None
    risk_override_level: str | None
    risk_override_reason: str | None
    risk_override_by: int | None
    approval_via: str | None
    approval_round: int
    approval_id: int | None
    approved_at: datetime | None
    impact_requested_at: datetime | None
    impact_computed_at: datetime | None
    impact_truncated: bool
    portal_visible: bool
    calendar_appointment_id: int | None
    created_at: datetime
    created_by: int
    updated_at: datetime
    updated_by: int

    @property
    def effective_risk_level(self) -> str | None:
        """The manager override wins over the computed level."""
        return self.risk_override_level or self.risk_level


@dataclass(slots=True)
class ChangeView:
    """Everything the detail page and ``ChangeOut`` need."""

    record: ChangeRecord
    ticket: TicketFacts
    #: ``ticket.service_id`` first, then ``tiqora_itsm_change_service``
    service_ids: tuple[int, ...]
    #: targets this caller may offer (empty without ``rw``); SYSTEM-only targets left out
    allowed_targets: tuple[Phase, ...]
    #: ``type_field`` / ``phase_field`` when the dynamic field differs from the record,
    #: ``ticket_closed`` when the ticket was closed behind Tiqora's back
    drift: tuple[str, ...]
    can_write: bool
    can_manage: bool
    #: the ticket later became a process ticket: only ``cancelled`` (manager) is possible
    process_ticket: bool


class ChangeCreateIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    change_type: ChangeType
    #: required unless ``change_queue_id`` is set
    queue_id: int | None = None
    template_id: int | None = None
    service_id: int | None = None
    service_ids: list[int] = Field(default_factory=list, max_length=MAX_SERVICES)
    #: ``"tiqora:<id>"``
    ci_refs: list[str] = Field(default_factory=list, max_length=50)
    customer_user_id: str | None = None
    #: first article (internal note); plain text, stored as escaped HTML
    body: str = Field("", max_length=MAX_TEXT)
    #: plain text
    reason: str | None = Field(None, max_length=MAX_TEXT)
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    cab_id: int | None = None

    @model_validator(mode="after")
    def _check(self) -> ChangeCreateIn:
        if self.change_type == "standard" and self.template_id is None:
            raise ValueError("Ein Standard-Change braucht eine Change-Vorlage.")
        _check_window(self.planned_start, self.planned_end)
        return self


class ChangeUpdateIn(BaseModel):
    """PATCH body. There is no ``phase``: unknown fields are refused (422), so a phase can only
    change through the phase machine."""

    model_config = ConfigDict(extra="forbid")

    #: only in ``draft``
    change_type: ChangeType | None = None
    reason: str | None = Field(None, max_length=MAX_TEXT)
    implementation_plan: str | None = Field(None, max_length=MAX_TEXT)
    backout_plan: str | None = Field(None, max_length=MAX_TEXT)
    test_plan: str | None = Field(None, max_length=MAX_TEXT)
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    clear_window: bool = False
    #: replaces the extra services; the ticket's own service stays
    service_ids: list[int] | None = Field(None, max_length=MAX_SERVICES)
    #: omitted = unchanged, ``null`` = no CAB chosen
    cab_id: int | None = None
    portal_visible: bool | None = None

    @model_validator(mode="after")
    def _check(self) -> ChangeUpdateIn:
        if self.clear_window and (self.planned_start or self.planned_end):
            raise ValueError("clear_window und ein Zeitfenster schließen sich aus.")
        _check_window(self.planned_start, self.planned_end)
        return self


def _check_window(start: datetime | None, end: datetime | None) -> None:
    if start is not None and end is not None and to_utc_naive(end) <= to_utc_naive(start):
        raise ValueError("Das Ende des Zeitfensters muss nach dem Beginn liegen.")


# --- listeners -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    ticket_id: int
    #: ``created`` | ``phase`` | ``updated`` | ``impact``
    event: str
    from_phase: str | None
    to_phase: str | None
    user_id: int
    at: datetime


@dataclass(frozen=True, slots=True)
class ChangeListener:
    key: str  # unique, e.g. "itsm_beyond.ai_risk"
    events: frozenset[str]  # created|phase|updated|impact
    #: runs in the caller's transaction inside a savepoint; must not commit. Errors are
    #: logged, never raised.
    on_event: Callable[[AsyncSession, ChangeEvent], Awaitable[None]]


CHANGE_LISTENERS: Final[ExtensionPoint[ChangeListener]] = ExtensionPoint(
    key="itsm.change_listeners",
    item_key=lambda item: item.key,
    description=(
        "Called after a change is created, changes phase, is updated or gets a new impact set."
    ),
    sdk_stable=False,
)


async def notify_listeners(session: AsyncSession, event: ChangeEvent) -> None:
    """Call the listeners registered for ``event.event``, each inside its own savepoint.

    The savepoint keeps a listener that fails in SQL from poisoning the surrounding transaction
    (PostgreSQL aborts it otherwise).
    """
    for listener in get_registry().items(CHANGE_LISTENERS):
        if event.event not in listener.events:
            continue
        try:
            async with session.begin_nested():
                await listener.on_event(session, event)
        except Exception:
            logger.exception(
                "change_listener_failed",
                extra={
                    "listener": listener.key,
                    "ticket_id": event.ticket_id,
                    "event": event.event,
                },
            )


async def record_event(
    session: AsyncSession,
    *,
    ticket_id: int,
    event: str,
    user_id: int,
    from_phase: str | None = None,
    to_phase: str | None = None,
    detail: dict[str, Any] | None = None,
) -> TiqoraItsmChangeEvent:
    """Append one timeline row (flushed, not committed)."""
    row = TiqoraItsmChangeEvent(
        ticket_id=ticket_id,
        created_at=utc_now(),
        created_by=user_id,
        event=event,
        from_phase=from_phase,
        to_phase=to_phase,
        detail=None if detail is None else json.dumps(detail, ensure_ascii=False, sort_keys=True),
    )
    session.add(row)
    await session.flush()
    return row


# --- dynamic-field mirror --------------------------------------------------------------------


async def mirror_dynamic_fields(
    session: AsyncSession,
    *,
    ticket_id: int,
    change_type: str,
    phase: str,
    user_id: int,
) -> bool:
    """Write the two mirror fields (``update_dynamic_field``: history only when a value changes).

    A value the field no longer allows (an admin edited ``PossibleValues`` in Znuny) writes
    nothing: it is logged as ``change_df_mirror_failed``, the page shows the drift and the
    caller's transition carries on. Returns whether both fields were written.
    """
    ok = True
    for name, value in ((TYPE_FIELD, change_type), (PHASE_FIELD, phase)):
        try:
            await update_dynamic_field(
                session, ticket_id=ticket_id, field_name=name, values=[value], user_id=user_id
            )
        except InvalidInput:
            ok = False
            logger.warning(
                "change_df_mirror_failed",
                extra={"ticket_id": ticket_id, "field": name, "value": value},
            )
    return ok


async def _drift(
    session: AsyncSession, record: TiqoraItsmChange, facts: TicketFacts
) -> tuple[str, ...]:
    out: list[str] = []
    for name, expected, label in (
        (TYPE_FIELD, record.change_type, "type_field"),
        (PHASE_FIELD, record.phase, "phase_field"),
    ):
        field = await load_dynamic_field(session, name)
        current = [] if field is None else await get_df_values(session, field, record.ticket_id)
        if current != [expected]:
            out.append(label)
    if facts.state_type in CLOSED_STATE_TYPES and record.phase not in FINAL_PHASES:
        out.append("ticket_closed")
    return tuple(out)


# --- helpers ---------------------------------------------------------------------------------


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _escape_html(text_value: str) -> str:
    """Plain text as article HTML (ruling F25): escaped, line breaks as ``<br>``."""
    return html.escape(text_value).replace("\r\n", "\n").replace("\n", "<br>")


def _naive(value: datetime | None) -> datetime | None:
    return None if value is None else to_utc_naive(value)


async def _ticket(
    session: AsyncSession, ticket_id: int, *, user_id: int, write: bool = False
) -> TicketFacts:
    try:
        return await require_ticket(session, ticket_id, user_id=user_id, write=write)
    except TicketAccessDenied as exc:
        raise ChangeError(exc.code, exc.http_status) from exc


async def _lookup_id(session: AsyncSession, table: str, name: str) -> int | None:
    # ``table`` is one of three constants below, never input
    row = (
        await session.execute(
            text(f"SELECT id FROM {table} WHERE name = :n AND valid_id = 1 ORDER BY id LIMIT 1"),
            {"n": name},
        )
    ).first()
    return None if row is None else int(row[0])


async def _priority_id(session: AsyncSession) -> int:
    found = await _lookup_id(session, "ticket_priority", DEFAULT_PRIORITY)
    if found is not None:
        return found
    row = (
        await session.execute(text("SELECT MIN(id) FROM ticket_priority WHERE valid_id = 1"))
    ).first()
    if row is None or row[0] is None:
        raise ChangeError("change_priority_not_found", 409)
    return int(row[0])


async def _existing_services(session: AsyncSession, ids: Sequence[int]) -> set[int]:
    found: set[int] = set()
    wanted = sorted(set(ids))
    stmt = text("SELECT id FROM service WHERE id IN :ids AND valid_id = 1").bindparams(
        bindparam("ids", expanding=True)
    )
    for start in range(0, len(wanted), CHUNK):
        rows = await session.execute(stmt, {"ids": wanted[start : start + CHUNK]})
        found.update(int(r[0]) for r in rows)
    return found


async def _check_services(session: AsyncSession, ids: Sequence[int]) -> None:
    if ids and not set(ids) <= await _existing_services(session, ids):
        raise ChangeError("service_not_found", 422)


async def _template(
    session: AsyncSession, template_id: int | None
) -> TiqoraItsmChangeTemplate | None:
    if template_id is None:
        return None
    tpl = await session.get(TiqoraItsmChangeTemplate, template_id)
    if tpl is None or not tpl.valid:
        raise ChangeError("template_not_found", 422)
    return tpl


async def _check_cab(session: AsyncSession, cab_id: int | None) -> None:
    if cab_id is None:
        return
    cab = await session.get(TiqoraItsmCab, cab_id)
    if cab is None or not cab.valid:
        raise ChangeError("cab_not_found", 422)


async def _extra_services(session: AsyncSession, ticket_id: int) -> list[int]:
    rows = await session.execute(
        select(TiqoraItsmChangeService.service_id)
        .where(TiqoraItsmChangeService.ticket_id == ticket_id)
        .order_by(TiqoraItsmChangeService.service_id)
    )
    return [int(r[0]) for r in rows]


async def _row(session: AsyncSession, ticket_id: int, *, lock: bool = False) -> TiqoraItsmChange:
    stmt = select(TiqoraItsmChange).where(TiqoraItsmChange.ticket_id == ticket_id)
    if lock:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        # the caller already holds ``ro`` on the ticket, so this tells nothing it cannot see
        raise ChangeError("change_not_found", 404)
    return row


def _auto_eligible(row: TiqoraItsmChange) -> bool:
    """The stored risk would take the low-risk route: offer it only then (the transition
    evaluates again before taking it)."""
    from tiqora.itsm.change.risk import stored_evaluation  # risk imports this module

    evaluation = stored_evaluation(row)
    return (
        evaluation.effective_level == "low" and not evaluation.blocked and not evaluation.incomplete
    )


async def _view(
    session: AsyncSession,
    cfg: ItsmChangeSettings,
    facts: TicketFacts,
    row: TiqoraItsmChange,
    *,
    user_id: int,
) -> ChangeView:
    can_write = await PermissionEngine(session).check(user_id, facts.queue_id, "rw")
    can_manage = await is_change_manager(session, user_id, cfg)
    process = await get_ticket_process_state(session, facts.ticket_id) is not None
    phase: Phase = row.phase  # type: ignore[assignment]
    change_type: ChangeType = row.change_type  # type: ignore[assignment]
    targets: tuple[Phase, ...] = ()
    if process:
        # F17: a later process ticket may only be cancelled, by a manager
        if can_manage and phase in REOPENABLE_PHASES:
            targets = ("cancelled",)
    elif can_write:
        targets = allowed_targets(
            phase,
            change_type,
            emergency_post_approval=cfg.emergency_post_approval,
            standard_requires_review=cfg.standard_requires_review,
            auto_approve_low_risk=cfg.auto_approve_low_risk and _auto_eligible(row),
        )
    extra = await _extra_services(session, facts.ticket_id)
    services = tuple(
        dict.fromkeys([*([] if facts.service_id is None else [facts.service_id]), *extra])
    )
    return ChangeView(
        record=ChangeRecord.model_validate(row),
        ticket=facts,
        service_ids=services,
        allowed_targets=targets,
        drift=await _drift(session, row, facts),
        can_write=can_write,
        can_manage=can_manage,
        process_ticket=process,
    )


# --- create / adopt --------------------------------------------------------------------------


async def create_change(
    session: AsyncSession,
    settings: Settings,
    sysconfig: SysConfig,
    *,
    data: ChangeCreateIn,
    user_id: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[ChangeView, int | None]:
    """A new change ticket with its record in phase ``draft``.

    Returns the view and the id of the template-task job (``None`` when the template has no
    tasks or none was used; the impact job is wired by the impact module). Needs ``create`` on the
    queue (403). A template prefills what the caller left out (see ``apply_template_defaults``);
    references that went stale since the template was saved are dropped and named in the
    ``created`` event's ``template_warnings``.
    """
    # lazy: ``templates`` imports this module and ``tasks``, which imports this module too
    from tiqora.itsm.change.impact import enqueue_impact
    from tiqora.itsm.change.tasks import enqueue_change_tasks
    from tiqora.itsm.change.templates import (
        ResolvedDefaults,
        apply_template_defaults,
        resolve_defaults,
        template_view,
    )

    await require_change_mode(session, settings)
    cfg = await load_change_settings(session, settings)
    itsm_cfg = await load_itsm_settings(session, settings)

    template = await _template(session, data.template_id)
    if data.change_type == "standard" and (template is None or not template.is_standard):
        raise ChangeError("standard_needs_template", 422)
    if template is not None and template.change_type != data.change_type:
        raise ChangeError("template_type_mismatch", 422)
    tview = None if template is None else template_view(template)
    resolved: ResolvedDefaults | None = None
    if tview is not None:
        if tview.broken:
            raise ChangeError("template_invalid", 409)
        resolved = await resolve_defaults(session, tview)
        tview = tview.model_copy(update={"defaults": resolved.defaults})
        data = apply_template_defaults(data, tview)
    defaults = None if tview is None else tview.defaults
    queue_id = data.queue_id or cfg.change_queue_id
    if not queue_id:
        raise ChangeError("queue_required", 422)
    # the creator reads the new change right away: ``create`` alone is not enough
    if not await PermissionEngine(session).check(user_id, queue_id, "ro"):
        raise ChangeError("forbidden", 403)
    extra_services = [s for s in dict.fromkeys(data.service_ids) if s != data.service_id]
    await _check_services(
        session, [*([] if data.service_id is None else [data.service_id]), *extra_services]
    )
    await _check_cab(session, data.cab_id)
    refs = _parse_ci_refs(data.ci_refs)
    provider = get_ci_provider("tiqora", sysconfig) if refs else None
    if refs and provider is None:
        raise ChangeError("cmdb_unavailable", 409)

    type_id = await _lookup_id(session, "ticket_type", itsm_cfg.change_type)
    state_id = await _lookup_id(session, "ticket_state", INITIAL_STATE)
    if type_id is None or state_id is None:
        raise ChangeError("change_setup_missing", 409)
    customer_id: str | None = None
    if data.customer_user_id:
        row = (
            await session.execute(
                text("SELECT customer_id FROM customer_user WHERE login = :l"),
                {"l": data.customer_user_id},
            )
        ).first()
        customer_id = None if row is None else str(row[0])

    note = _clean(data.body) or _clean(data.reason) or data.title
    writer = TicketWriteService(session, session_factory or get_session_factory(), sysconfig)
    try:
        ticket_id = await writer.create_ticket(
            user_id,
            TicketIn(
                title=data.title,
                queue_id=queue_id,
                state_id=state_id,
                priority_id=(
                    defaults.priority_id
                    if defaults is not None and defaults.priority_id is not None
                    else await _priority_id(session)
                ),
                owner_id=user_id,
                type_id=type_id,
                service_id=data.service_id,
                customer_id=customer_id,
                customer_user_id=data.customer_user_id,
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
    except WriteAccessDenied as exc:
        raise ChangeError("forbidden", 403) from exc
    except OperationVetoed as exc:
        raise ChangeError("vetoed", 409, message=str(exc)) from exc
    except InvalidInput as exc:
        raise ChangeError("invalid_input", 422, message=str(exc)) from exc

    now = utc_now()
    session.add(
        TiqoraItsmChange(
            ticket_id=ticket_id,
            change_type=data.change_type,
            phase="draft",
            template_id=None if template is None else template.id,
            cab_id=data.cab_id,
            planned_start=_naive(data.planned_start),
            planned_end=_naive(data.planned_end),
            reason=_clean(data.reason),
            implementation_plan=None if defaults is None else _clean(defaults.implementation_plan),
            backout_plan=None if defaults is None else _clean(defaults.backout_plan),
            test_plan=None if defaults is None else _clean(defaults.test_plan),
            portal_visible=False if defaults is None else defaults.portal_visible,
            approval_round=0,
            created_at=now,
            created_by=user_id,
            updated_at=now,
            updated_by=user_id,
        )
    )
    await session.flush()
    for service_id in extra_services:
        session.add(TiqoraItsmChangeService(ticket_id=ticket_id, service_id=service_id))
    if resolved is not None:
        for question_key, answer_key, points in resolved.risk_answers:
            session.add(
                TiqoraItsmChangeRiskAnswer(
                    ticket_id=ticket_id,
                    question_key=question_key,
                    answer_key=answer_key,
                    points=points,
                    answered_at=now,
                    answered_by=user_id,
                )
            )
    await session.flush()

    if provider is not None:
        for ref in refs:
            try:
                await provider.link_ticket(
                    session,
                    ticket_id=ticket_id,
                    ref=ref,
                    link_type=itsm_cfg.ci_ticket_link_type,
                    user_id=user_id,
                )
            except CiError as exc:
                raise ChangeError("ci_not_found", 422, ref=str(ref)) from exc

    await mirror_dynamic_fields(
        session, ticket_id=ticket_id, change_type=data.change_type, phase="draft", user_id=user_id
    )
    await record_event(
        session,
        ticket_id=ticket_id,
        event="created",
        user_id=user_id,
        to_phase="draft",
        detail={
            "change_type": data.change_type,
            **({} if template is None else {"template_id": template.id}),
            **(
                {"template_warnings": list(resolved.warnings)}
                if resolved is not None and resolved.warnings
                else {}
            ),
        },
    )
    await record_event(
        session, ticket_id=ticket_id, event="phase", user_id=user_id, to_phase="draft"
    )
    await notify_listeners(
        session,
        ChangeEvent(ticket_id, "created", None, "draft", user_id, now),
    )
    await enqueue_impact(session, ticket_id=ticket_id, user_id=user_id)
    tasks_job_id: int | None = None
    if tview is not None and tview.tasks:
        job = await enqueue_change_tasks(
            session, change_ticket_id=ticket_id, template_id=tview.id, user_id=user_id
        )
        tasks_job_id = job.id
    return await _loaded_view(session, cfg, ticket_id, user_id), tasks_job_id


def _parse_ci_refs(raw: Sequence[str]) -> list[CiRef]:
    refs: list[CiRef] = []
    for item in dict.fromkeys(raw):
        try:
            ref = CiRef.parse(item)
        except ValueError as exc:
            raise ChangeError("ci_ref_invalid", 422, ref=item) from exc
        if ref.source != "tiqora":
            raise ChangeError("ci_ref_invalid", 422, ref=item)
        refs.append(ref)
    return refs


async def _loaded_view(
    session: AsyncSession, cfg: ItsmChangeSettings, ticket_id: int, user_id: int
) -> ChangeView:
    facts = await _ticket(session, ticket_id, user_id=user_id)
    return await _view(session, cfg, facts, await _row(session, ticket_id), user_id=user_id)


async def adopt_ticket(
    session: AsyncSession,
    settings: Settings,
    sysconfig: SysConfig,
    *,
    ticket_id: int,
    change_type: ChangeType,
    user_id: int,
) -> ChangeView:
    """Give an existing ticket of the change type a record in phase ``draft``.

    Needs ``rw``. Refuses a ticket of another type (``not_a_change_ticket``), a ticket that
    already has a record (``change_exists``), a process ticket (``process_ticket``) and a closed
    ticket (``ticket_closed``).
    """
    _ = sysconfig
    from tiqora.itsm.change.impact import enqueue_impact

    await require_change_mode(session, settings)
    cfg = await load_change_settings(session, settings)
    itsm_cfg = await load_itsm_settings(session, settings)
    facts = await _ticket(session, ticket_id, user_id=user_id, write=True)
    if facts.type_name != itsm_cfg.change_type:
        raise ChangeError("not_a_change_ticket", 409)
    if await session.get(TiqoraItsmChange, ticket_id) is not None:
        raise ChangeError("change_exists", 409)
    if await get_ticket_process_state(session, ticket_id) is not None:
        raise ChangeError("process_ticket", 409)
    if facts.state_type in CLOSED_STATE_TYPES:
        raise ChangeError("ticket_closed", 409)
    now = utc_now()
    try:
        async with session.begin_nested():  # a concurrent adopt loses on the primary key
            session.add(
                TiqoraItsmChange(
                    ticket_id=ticket_id,
                    change_type=change_type,
                    phase="draft",
                    approval_round=0,
                    created_at=now,
                    created_by=user_id,
                    updated_at=now,
                    updated_by=user_id,
                )
            )
    except IntegrityError as exc:
        raise ChangeError("change_exists", 409) from exc
    await mirror_dynamic_fields(
        session, ticket_id=ticket_id, change_type=change_type, phase="draft", user_id=user_id
    )
    await record_event(
        session,
        ticket_id=ticket_id,
        event="adopted",
        user_id=user_id,
        to_phase="draft",
        detail={"change_type": change_type},
    )
    await record_event(
        session, ticket_id=ticket_id, event="phase", user_id=user_id, to_phase="draft"
    )
    await notify_listeners(session, ChangeEvent(ticket_id, "created", None, "draft", user_id, now))
    # the ticket may carry CI links and a service already
    await enqueue_impact(session, ticket_id=ticket_id, user_id=user_id)
    return await _loaded_view(session, cfg, ticket_id, user_id)


# --- read / update ---------------------------------------------------------------------------


async def get_change(
    session: AsyncSession, settings: Settings, *, ticket_id: int, user_id: int
) -> ChangeView:
    """The change of a ticket the caller may read (404 otherwise)."""
    await require_change_mode(session, settings)
    cfg = await load_change_settings(session, settings)
    return await _loaded_view(session, cfg, ticket_id, user_id)


async def list_events(
    session: AsyncSession,
    settings: Settings,
    *,
    ticket_id: int,
    user_id: int,
    limit: int = 50,
    before_id: int | None = None,
) -> list[TiqoraItsmChangeEvent]:
    """Timeline of a readable change, newest first."""
    await require_change_mode(session, settings)
    await _ticket(session, ticket_id, user_id=user_id)
    await _row(session, ticket_id)
    stmt = select(TiqoraItsmChangeEvent).where(TiqoraItsmChangeEvent.ticket_id == ticket_id)
    if before_id is not None:
        stmt = stmt.where(TiqoraItsmChangeEvent.id < before_id)
    stmt = stmt.order_by(TiqoraItsmChangeEvent.id.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars())


async def update_change(
    session: AsyncSession,
    settings: Settings,
    sysconfig: SysConfig,
    *,
    ticket_id: int,
    data: ChangeUpdateIn,
    user_id: int,
) -> ChangeView:
    """Change fields of the record (never the phase). Needs ``rw``; final changes are read-only.

    The change type moves only in ``draft``. While the CAB decides (``approval``) what it decides
    on is frozen: change type, window, the three plans and the CAB answer 409
    ``change_locked_in_approval``; later phases stay editable. Writes one ``updated`` event
    naming the changed fields (none when nothing changed).
    """
    _ = sysconfig
    from tiqora.itsm.change.impact import enqueue_impact

    await require_change_mode(session, settings)
    cfg = await load_change_settings(session, settings)
    facts = await _ticket(session, ticket_id, user_id=user_id, write=True)
    row = await _row(session, ticket_id, lock=True)
    if row.phase in FINAL_PHASES:
        raise ChangeError("change_final", 409)

    changed: list[str] = []
    fields_set = data.model_fields_set

    def locked(name: str) -> None:
        if row.phase == "approval":
            raise ChangeError("change_locked_in_approval", 409, field=name)

    if data.change_type is not None and data.change_type != row.change_type:
        locked("change_type")
        if row.phase != "draft":
            raise ChangeError("change_type_locked", 409)
        if row.template_id is not None:
            # a change created from a template keeps the template's type
            used = await session.get(TiqoraItsmChangeTemplate, row.template_id)
            if used is not None and used.change_type != data.change_type:
                raise ChangeError("template_type_mismatch", 422)
        if data.change_type == "standard":
            template = await _template(session, row.template_id)
            if template is None or not template.is_standard:
                raise ChangeError("standard_needs_template", 422)
        row.change_type = data.change_type
        changed.append("change_type")

    for name in ("reason", "implementation_plan", "backout_plan", "test_plan"):
        if name in fields_set and getattr(data, name) is not None:
            value = _clean(getattr(data, name))
            if value != getattr(row, name):
                if name in _APPROVAL_LOCKED_TEXTS:
                    locked(name)
                setattr(row, name, value)
                changed.append(name)

    start, end = row.planned_start, row.planned_end
    if data.clear_window:
        start = end = None
    else:
        start = _naive(data.planned_start) or start
        end = _naive(data.planned_end) or end
    if start is not None and end is not None and end <= start:
        raise ChangeError("window_invalid", 422)
    if (start, end) != (row.planned_start, row.planned_end):
        locked("window")
        row.planned_start, row.planned_end = start, end
        changed.append("window")

    if data.service_ids is not None:
        wanted = [s for s in dict.fromkeys(data.service_ids) if s != facts.service_id]
        await _check_services(session, wanted)
        current = await _extra_services(session, ticket_id)
        if set(wanted) != set(current):
            await session.execute(
                delete(TiqoraItsmChangeService).where(
                    TiqoraItsmChangeService.ticket_id == ticket_id
                )
            )
            for service_id in wanted:
                session.add(TiqoraItsmChangeService(ticket_id=ticket_id, service_id=service_id))
            changed.append("services")

    if "cab_id" in fields_set and data.cab_id != row.cab_id:
        locked("cab_id")
        await _check_cab(session, data.cab_id)
        row.cab_id = data.cab_id
        changed.append("cab_id")

    if data.portal_visible is not None and data.portal_visible != row.portal_visible:
        row.portal_visible = data.portal_visible
        changed.append("portal_visible")

    if changed:
        now = utc_now()
        row.updated_at = now
        row.updated_by = user_id
        await session.flush()
        if "change_type" in changed:
            await mirror_dynamic_fields(
                session,
                ticket_id=ticket_id,
                change_type=row.change_type,
                phase=row.phase,
                user_id=user_id,
            )
        await record_event(
            session,
            ticket_id=ticket_id,
            event="updated",
            user_id=user_id,
            detail={"fields": changed},
        )
        await notify_listeners(
            session, ChangeEvent(ticket_id, "updated", row.phase, row.phase, user_id, now)
        )
        if "services" in changed:
            await enqueue_impact(session, ticket_id=ticket_id, user_id=user_id)
    return await _view(session, cfg, facts, row, user_id=user_id)
