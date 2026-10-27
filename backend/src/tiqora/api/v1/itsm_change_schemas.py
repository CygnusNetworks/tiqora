"""Response and request models of the change-management agent API."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from tiqora.domain.schemas import UtcDateTime
from tiqora.itsm.change.freeze import FreezeOccurrence
from tiqora.itsm.change.models import TiqoraItsmChangeEvent
from tiqora.itsm.change.phases import ChangeType, Phase
from tiqora.itsm.change.service import ChangeView
from tiqora.itsm.change.tasks import TaskKind, TaskView
from tiqora.itsm.change.templates import TemplateDefaults, TemplateView


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
    enforcement: Literal["block", "warn"]
    allow_emergency: bool
    scope: Literal["all", "services"]
    service_ids: list[int]
    start: UtcDateTime
    end: UtcDateTime


def freeze_occurrence_out(occ: FreezeOccurrence) -> FreezeOccurrenceOut:
    return FreezeOccurrenceOut(
        freeze_id=occ.freeze_id,
        name=occ.name,
        enforcement=occ.enforcement,  # type: ignore[arg-type]
        allow_emergency=occ.allow_emergency,
        scope=occ.scope,  # type: ignore[arg-type]
        service_ids=sorted(occ.service_ids),
        start=occ.start,
        end=occ.end,
    )
