"""Response and request models of the change-management agent API."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from tiqora.approvals.contract import ApprovalStatus
from tiqora.domain.schemas import UtcDateTime
from tiqora.itsm.change.cab import (
    MAX_AGENDA,
    AgendaItemIn,
    CabView,
    MeetingStatus,
    MeetingView,
    MemberKind,
    Rule,
)
from tiqora.itsm.change.freeze import Enforcement, FreezeOccurrences, Scope
from tiqora.itsm.change.models import TiqoraItsmChangeEvent
from tiqora.itsm.change.phases import ChangeType, Phase
from tiqora.itsm.change.read import (
    CalendarChange,
    ChangeCalendar,
    ChangeListItem,
    ChangePage,
    ServiceAvailabilityRow,
    ServiceWindow,
    TaskListItem,
)
from tiqora.itsm.change.related import RelatedTicket
from tiqora.itsm.change.service import ChangeView
from tiqora.itsm.change.tasks import TaskKind, TaskView
from tiqora.itsm.change.templates import TemplateDefaults, TemplateView
from tiqora.itsm.change.transitions import ChangeApprovalView


class ChangeStatusOut(BaseModel):
    mode: Literal["off", "znuny_readthrough", "tiqora"]
    available: bool
    reason: Literal["ok", "itsm_off", "mode_a", "setup_missing"]
    legacy: Literal["none", "live", "archive"]
    can_manage: bool
    can_see_legacy: bool
    setup_complete_change: bool


class ChangeOut(BaseModel):
    ticket_id: int
    tn: str
    title: str
    queue_id: int
    ticket_state: str
    ticket_state_type: str
    change_type: ChangeType
    phase: Phase
    outcome: str | None
    template_id: int | None
    cab_id: int | None
    planned_start: UtcDateTime | None
    planned_end: UtcDateTime | None
    actual_start: UtcDateTime | None
    actual_end: UtcDateTime | None
    reason: str | None
    implementation_plan: str | None
    backout_plan: str | None
    test_plan: str | None
    pir_notes: str | None
    risk_score: int | None
    risk_level: str | None
    risk_override_level: str | None
    risk_override_reason: str | None
    effective_risk_level: str | None
    approval_via: str | None
    approval_round: int
    approved_at: UtcDateTime | None
    impact_computed_at: UtcDateTime | None
    impact_truncated: bool
    portal_visible: bool
    service_ids: list[int]
    allowed_targets: list[Phase]
    drift: list[str]
    can_write: bool
    can_manage: bool
    process_ticket: bool
    created_at: UtcDateTime
    created_by: int
    updated_at: UtcDateTime
    updated_by: int


class ChangeCreatedOut(BaseModel):
    change: ChangeOut
    #: job that creates the template tasks; ``None`` when there is nothing to create
    tasks_job_id: int | None
    #: notes of the create, e.g. ``ci_refs_truncated`` (create-from cut the source's CIs)
    warnings: list[str] = Field(default_factory=list)


class ChangeEventOut(BaseModel):
    id: int
    created_at: UtcDateTime
    created_by: int
    event: str
    from_phase: str | None
    to_phase: str | None
    detail: dict[str, Any] | None


class AdoptIn(BaseModel):
    change_type: ChangeType


class RelatedTicketOut(BaseModel):
    ticket_id: int
    tn: str
    title: str
    state: str
    state_type: str
    #: ITSM role of the ticket type: incident, major_incident, problem, …
    role: str | None
    link_type: str
    #: the change's side of the link
    direction: Literal["source", "target"]


class RelatedLinkIn(BaseModel):
    ticket_id: int


def related_out(item: RelatedTicket) -> RelatedTicketOut:
    return RelatedTicketOut(
        ticket_id=item.ticket_id,
        tn=item.tn,
        title=item.title,
        state=item.state,
        state_type=item.state_type,
        role=item.role,
        link_type=item.link_type,
        direction=item.direction,
    )


def change_out(view: ChangeView) -> ChangeOut:
    rec, ticket = view.record, view.ticket
    return ChangeOut(
        ticket_id=rec.ticket_id,
        tn=ticket.tn,
        title=ticket.title,
        queue_id=ticket.queue_id,
        ticket_state=ticket.state,
        ticket_state_type=ticket.state_type,
        change_type=rec.change_type,
        phase=rec.phase,
        outcome=rec.outcome,
        template_id=rec.template_id,
        cab_id=rec.cab_id,
        planned_start=rec.planned_start,
        planned_end=rec.planned_end,
        actual_start=rec.actual_start,
        actual_end=rec.actual_end,
        reason=rec.reason,
        implementation_plan=rec.implementation_plan,
        backout_plan=rec.backout_plan,
        test_plan=rec.test_plan,
        pir_notes=rec.pir_notes,
        risk_score=rec.risk_score,
        risk_level=rec.risk_level,
        risk_override_level=rec.risk_override_level,
        risk_override_reason=rec.risk_override_reason,
        effective_risk_level=rec.effective_risk_level,
        approval_via=rec.approval_via,
        approval_round=rec.approval_round,
        approved_at=rec.approved_at,
        impact_computed_at=rec.impact_computed_at,
        impact_truncated=rec.impact_truncated,
        portal_visible=rec.portal_visible,
        service_ids=list(view.service_ids),
        allowed_targets=list(view.allowed_targets),
        drift=list(view.drift),
        can_write=view.can_write,
        can_manage=view.can_manage,
        process_ticket=view.process_ticket,
        created_at=rec.created_at,
        created_by=rec.created_by,
        updated_at=rec.updated_at,
        updated_by=rec.updated_by,
    )


def event_out(row: TiqoraItsmChangeEvent) -> ChangeEventOut:
    detail: dict[str, Any] | None = None
    if row.detail:
        try:
            loaded = json.loads(row.detail)
        except ValueError:
            loaded = None
        detail = loaded if isinstance(loaded, dict) else None
    return ChangeEventOut(
        id=int(row.id),
        created_at=row.created_at,
        created_by=int(row.created_by),
        event=row.event,
        from_phase=row.from_phase,
        to_phase=row.to_phase,
        detail=detail,
    )


class TaskOut(BaseModel):
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
    planned_start: UtcDateTime | None
    planned_end: UtcDateTime | None
    actual_start: UtcDateTime | None
    actual_end: UtcDateTime | None
    planned_effort_minutes: int | None
    template_task_key: str | None


class TaskOrderIn(BaseModel):
    order: list[int] = Field(..., max_length=200)


def task_out(view: TaskView) -> TaskOut:
    return TaskOut.model_validate(view.model_dump())


class ChangeTemplateSummaryOut(BaseModel):
    """What the create dialog needs from a valid template (no task texts)."""

    id: int
    name: str
    description: str | None
    change_type: ChangeType
    is_standard: bool
    task_count: int
    defaults: TemplateDefaults


def template_summary(view: TemplateView) -> ChangeTemplateSummaryOut:
    return ChangeTemplateSummaryOut(
        id=view.id,
        name=view.name,
        description=view.description,
        change_type=view.change_type,
        is_standard=view.is_standard,
        task_count=len(view.tasks),
        defaults=view.defaults,
    )


class FreezeOccurrenceOut(BaseModel):
    freeze_id: int
    name: str
    enforcement: Enforcement
    allow_emergency: bool
    scope: Scope
    service_ids: list[int]
    start: UtcDateTime
    end: UtcDateTime


class FreezeOccurrencesOut(BaseModel):
    items: list[FreezeOccurrenceOut]
    #: the answer was cut at the occurrence limit; ask for a shorter range
    truncated: bool


def freeze_occurrences_out(result: FreezeOccurrences) -> FreezeOccurrencesOut:
    return FreezeOccurrencesOut(
        items=[
            FreezeOccurrenceOut(
                freeze_id=o.freeze_id,
                name=o.name,
                enforcement=o.enforcement,
                allow_emergency=o.allow_emergency,
                scope=o.scope,
                service_ids=sorted(o.service_ids),
                start=o.start,
                end=o.end,
            )
            for o in result.items
        ],
        truncated=result.truncated,
    )


class ApprovalSnapshotOut(BaseModel):
    """The engine's view of one CAB approval round (vote counts ``None`` until it exposes them)."""

    approval_id: int
    approval_key: str
    status: ApprovalStatus
    reason: str | None
    round: int
    current_stage: int | None
    created_at: UtcDateTime
    decided_at: UtcDateTime | None
    votes_total: int | None
    votes_approved: int | None
    votes_rejected: int | None


class ChangeApprovalOut(BaseModel):
    approval_id: int | None
    approval_round: int
    #: ``cab|template|risk|post``
    approval_via: str | None
    approved_at: UtcDateTime | None
    snapshot: ApprovalSnapshotOut | None
    #: an approval was requested but the engine no longer has it (erased):
    #: "Freigabeverlauf nicht mehr vorhanden"
    history_missing: bool


def approval_out(view: ChangeApprovalView) -> ChangeApprovalOut:
    snap = view.snapshot
    return ChangeApprovalOut(
        approval_id=view.approval_id,
        approval_round=view.approval_round,
        approval_via=view.approval_via,
        approved_at=view.approved_at,
        snapshot=None
        if snap is None
        else ApprovalSnapshotOut(
            approval_id=snap.approval_id,
            approval_key=snap.approval_key,
            status=snap.status,
            reason=snap.reason,
            round=snap.round,
            current_stage=snap.current_stage,
            created_at=snap.created_at,
            decided_at=snap.decided_at,
            votes_total=snap.votes_total,
            votes_approved=snap.votes_approved,
            votes_rejected=snap.votes_rejected,
        ),
        history_missing=view.history_missing,
    )


class ResolvedMemberOut(BaseModel):
    member_kind: MemberKind
    member_ref: str
    #: a name for users, groups and roles; "Freigabegruppe #<id>" for a list; the login or key
    label: str
    #: voters a group or role stands for (``None`` for single entries, lists and resolvers)
    count: int | None
    missing: bool


class CabOut(BaseModel):
    id: int
    name: str
    description: str | None
    rule: Rule
    quorum: int | None
    is_emergency: bool
    valid: bool
    members: list[ResolvedMemberOut]
    created_at: UtcDateTime
    created_by: int
    updated_at: UtcDateTime
    updated_by: int


def cab_out(view: CabView) -> CabOut:
    return CabOut.model_validate(view.model_dump())


class AgendaEntryOut(BaseModel):
    """``hidden`` lines carry nothing but their ``position``."""

    position: int
    hidden: bool
    change_ticket_id: int | None
    tn: str | None
    title: str | None
    change_type: ChangeType | None
    phase: Phase | None
    planned_start: UtcDateTime | None
    planned_end: UtcDateTime | None
    note: str | None
    approval: ApprovalSnapshotOut | None


class MeetingOut(BaseModel):
    id: int
    cab_id: int
    cab_name: str
    title: str
    starts_at: UtcDateTime
    ends_at: UtcDateTime
    location: str | None
    status: MeetingStatus
    minutes: str | None
    agenda_count: int
    agenda: list[AgendaEntryOut]
    created_at: UtcDateTime
    created_by: int
    updated_at: UtcDateTime
    updated_by: int


def meeting_out(view: MeetingView) -> MeetingOut:
    return MeetingOut.model_validate(view.model_dump())


class AgendaIn(BaseModel):
    items: list[AgendaItemIn] = Field(default_factory=list, max_length=MAX_AGENDA)


class ChangeListItemOut(BaseModel):
    ticket_id: int
    tn: str
    title: str
    queue_id: int
    owner_id: int
    change_type: ChangeType
    phase: Phase
    outcome: str | None
    #: effective level (override wins over the computed one)
    risk_level: str | None
    planned_start: UtcDateTime | None
    planned_end: UtcDateTime | None
    #: the ticket's service plus the change's extra services; a ``service_id`` filter can also
    #: match a service reached through the impact set, which is then not listed here
    service_ids: list[int]
    updated_at: UtcDateTime


class CabChangeItemOut(ChangeListItemOut):
    """An agenda suggestion: the list item of the change."""


def change_list_item_out(item: ChangeListItem) -> ChangeListItemOut:
    return ChangeListItemOut.model_validate(item, from_attributes=True)


def cab_change_item_out(item: ChangeListItem) -> CabChangeItemOut:
    return CabChangeItemOut.model_validate(item, from_attributes=True)


class ChangePageOut(BaseModel):
    items: list[ChangeListItemOut]
    #: matches among the scanned changes (a lower bound when ``truncated``)
    total: int
    #: more changes (or more title matches of the search text) than one scan reads; narrow the
    #: filters or make the search text more specific
    truncated: bool


def change_page_out(page: ChangePage) -> ChangePageOut:
    return ChangePageOut(
        items=[change_list_item_out(i) for i in page.items],
        total=page.total,
        truncated=page.truncated,
    )


class TaskListItemOut(BaseModel):
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
    planned_start: UtcDateTime | None
    planned_end: UtcDateTime | None
    change_ticket_id: int
    #: ``null`` when the caller may not read the change ticket
    change_tn: str | None
    change_title: str | None


def task_list_item_out(item: TaskListItem) -> TaskListItemOut:
    return TaskListItemOut.model_validate(item, from_attributes=True)


class CalendarChangeOut(ChangeListItemOut):
    #: highest severity among the change's conflicts; ``null`` = none
    conflict_severity: Literal["info", "warn", "block"] | None
    conflict_kinds: list[str]


def calendar_change_out(c: CalendarChange) -> CalendarChangeOut:
    return CalendarChangeOut(
        **change_list_item_out(c.item).model_dump(),
        conflict_severity=c.conflict_severity,
        conflict_kinds=list(c.conflict_kinds),
    )


class ChangeCalendarOut(BaseModel):
    start: UtcDateTime
    end: UtcDateTime
    changes: list[CalendarChangeOut]
    tasks: list[TaskListItemOut]
    freezes: list[FreezeOccurrenceOut]
    #: more changes or tasks than one read covers; flags and tasks may be incomplete
    truncated: bool
    #: freeze occurrences were cut off; ask for a shorter range
    freezes_truncated: bool


def change_calendar_out(cal: ChangeCalendar) -> ChangeCalendarOut:
    return ChangeCalendarOut(
        start=cal.start,
        end=cal.end,
        changes=[calendar_change_out(c) for c in cal.changes],
        tasks=[task_list_item_out(t) for t in cal.tasks],
        freezes=freeze_occurrences_out(FreezeOccurrences(list(cal.freezes), False)).items,
        truncated=cal.truncated,
        freezes_truncated=cal.freezes_truncated,
    )


class ServiceWindowOut(BaseModel):
    kind: Literal["change", "freeze"]
    #: change ticket id or freeze id
    ref_id: int
    tn: str | None
    title: str
    phase: str | None
    change_type: str | None
    enforcement: str | None
    #: the change names the service itself; ``null`` for a freeze
    direct: bool | None
    start: UtcDateTime
    end: UtcDateTime


class ServiceAvailabilityOut(BaseModel):
    service_id: int
    service_name: str
    #: minutes of the range covered by planned change windows
    minutes_affected: int
    change_count: int
    windows: list[ServiceWindowOut]


def _window_out(w: ServiceWindow) -> ServiceWindowOut:
    return ServiceWindowOut.model_validate(w, from_attributes=True)


def service_availability_out(row: ServiceAvailabilityRow) -> ServiceAvailabilityOut:
    return ServiceAvailabilityOut(
        service_id=row.service_id,
        service_name=row.service_name,
        minutes_affected=row.minutes_affected,
        change_count=row.change_count,
        windows=[_window_out(w) for w in row.windows],
    )
