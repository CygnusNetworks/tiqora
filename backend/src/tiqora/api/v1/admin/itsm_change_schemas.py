"""Schemas of ``/api/v1/admin/itsm/change`` (templates and freeze windows; CAB and risk questions
add theirs)."""

from __future__ import annotations

from pydantic import BaseModel

from tiqora.domain.schemas import UtcDateTime
from tiqora.itsm.change.freeze import Enforcement, FreezeView, Scope
from tiqora.itsm.change.phases import ChangeType
from tiqora.itsm.change.templates import TemplateDefaults, TemplateTask, TemplateView


class ChangeTemplateOut(BaseModel):
    id: int
    name: str
    description: str | None
    change_type: ChangeType
    is_standard: bool
    valid: bool
    #: the stored JSON no longer parses; save the template again to repair it
    broken: bool
    defaults: TemplateDefaults
    tasks: list[TemplateTask]
    created_at: UtcDateTime
    created_by: int
    updated_at: UtcDateTime
    updated_by: int


def template_out(view: TemplateView) -> ChangeTemplateOut:
    return ChangeTemplateOut(**view.model_dump())


class FreezeRecurrenceOut(BaseModel):
    type: str
    interval: int
    count: int | None
    until: UtcDateTime | None


class FreezeWindowOut(BaseModel):
    id: int
    name: str
    note: str | None
    enforcement: Enforcement
    allow_emergency: bool
    scope: Scope
    service_ids: list[int]
    starts_at: UtcDateTime
    ends_at: UtcDateTime
    recurrence: FreezeRecurrenceOut | None
    valid: bool
    created_at: UtcDateTime
    created_by: int
    updated_at: UtcDateTime
    updated_by: int


def freeze_out(view: FreezeView) -> FreezeWindowOut:
    return FreezeWindowOut(**view.model_dump())
