"""Schemas of ``/api/v1/admin/itsm/change`` (the template part; CAB, risk questions and freeze
windows add theirs)."""

from __future__ import annotations

from pydantic import BaseModel

from tiqora.domain.schemas import UtcDateTime
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
