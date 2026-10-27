"""Change-management settings section (admin form from the schema; stored ``itsm_change.<field>``).

Only bool/int/str/Literal fields, every one with a default. The dynamic fields that mirror the
change type and phase have fixed names (``TIQORA_CHANGE_DYNAMIC_FIELDS``), so there is nothing to
configure for them. Znuny ticket-state names are checked lazily by the transition that uses them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.settings_sections.section import SettingsSection
from tiqora.settings_sections.store import load_section_values


class ItsmChangeSettings(BaseModel):
    #: 0 = the agent picks the queue in the create dialog
    change_queue_id: int = Field(0, ge=0)
    #: 0 = queue of the change
    task_queue_id: int = Field(0, ge=0)
    #: Znuny group; "" = admins only
    manager_group: str = Field("", max_length=200)
    #: 0 = must be chosen before the approval
    default_cab_id: int = Field(0, ge=0)
    #: 0 = default CAB
    emergency_cab_id: int = Field(0, ge=0)
    emergency_post_approval: bool = False
    auto_approve_low_risk: bool = False
    standard_requires_review: bool = False
    approval_due_hours: int = Field(72, ge=1, le=2160)
    #: what happens when the approval is not decided in time: only remind, or reject the change
    approval_timeout: Literal["notify", "reject"] = "notify"
    #: owner and submitter may vote on their own change
    cab_allow_self_approval: bool = False
    state_open: str = Field("open", max_length=200)
    state_closed_successful: str = Field("closed successful", max_length=200)
    state_closed_failed: str = Field("closed unsuccessful", max_length=200)
    state_closed_cancelled: str = Field("closed unsuccessful", max_length=200)
    #: change <-> incident/problem (Znuny ticket-ticket link type)
    related_link_type: str = Field("Normal", max_length=50)
    #: change (parent) -> task
    task_link_type: str = Field("ParentChild", max_length=50)
    risk_medium_from: int = Field(10, ge=1, le=1000)
    risk_high_from: int = Field(20, ge=1, le=1000)
    risk_very_high_from: int = Field(35, ge=1, le=1000)
    weight_service_criticality: int = Field(3, ge=0, le=50)
    weight_ci_incident: int = Field(5, ge=0, le=50)
    weight_open_incidents: int = Field(4, ge=0, le=50)
    weight_overlap: int = Field(4, ge=0, le=50)
    weight_freeze: int = Field(10, ge=0, le=50)
    weight_failed_history: int = Field(3, ge=0, le=50)
    weight_emergency: int = Field(5, ge=0, le=50)
    weight_missing_backout: int = Field(3, ge=0, le=50)
    incident_lookback_days: int = Field(7, ge=1, le=90)
    failed_change_lookback_days: int = Field(180, ge=1, le=1095)
    impact_refresh_hours: int = Field(24, ge=1, le=720)
    max_tasks: int = Field(50, ge=1, le=200)
    #: Znuny calendar for the appointment mirror; 0 = off
    calendar_id: int = Field(0, ge=0)
    portal_schedule_enabled: bool = False
    #: members (ro) may read the legacy Znuny changes; a missing group leaves it to admins
    legacy_change_group: str = Field("itsm-change", max_length=200)

    @model_validator(mode="after")
    def _thresholds_increase(self) -> ItsmChangeSettings:
        if not self.risk_medium_from < self.risk_high_from < self.risk_very_high_from:
            raise ValueError("Die Risikoschwellen müssen aufsteigen: mittel < hoch < sehr hoch.")
        return self


ITSM_CHANGE_SECTION = SettingsSection(
    key="itsm_change",
    model=ItsmChangeSettings,
    title_key="admin.settings.itsmChange.title",
    i18n_prefix="admin.settings.itsmChange",
)


async def load_change_settings(session: AsyncSession, settings: Settings) -> ItsmChangeSettings:
    """The one way every change module loads its settings (the section has no secrets)."""
    values = await load_section_values(session, ITSM_CHANGE_SECTION, secret_key=settings.secret_key)
    return ItsmChangeSettings.model_validate(values.model_dump())
