"""ITSM settings section (admin form generated from the schema; stored as ``itsm.<field>``).

The mode itself is not a field: ``itsm.mode`` / ``itsm.mode_meta`` / ``itsm.switch`` /
``itsm.znuny_readonly`` live in the same key space but change only through
``tiqora.itsm.mode`` (guarded transitions). ``RESERVED_ITSM_SECTION_FIELDS`` lists those names.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.itsm.mode import RESERVED_ITSM_SECTION_FIELDS
from tiqora.settings_sections.section import SettingsSection
from tiqora.settings_sections.store import load_section_values


class ItsmSettings(BaseModel):
    # Defaults are the names the Znuny ITSM packages create (Task 2/3 survey, docs/itsm-import.md).
    incident_type: str = Field("Incident", max_length=200)
    major_incident_type: str = Field("Incident::Major", max_length=200)
    service_request_type: str = Field("ServiceRequest", max_length=200)
    problem_type: str = Field("Problem", max_length=200)
    change_type: str = Field("Change", max_length=200)
    change_task_type: str = Field("Change::Task", max_length=200)
    workaround_state: str = Field("closed with workaround", max_length=200)
    criticality_field: str = Field("ITSMCriticality", max_length=200)
    impact_field: str = Field("ITSMImpact", max_length=200)
    priority_matrix_enabled: bool = True
    ci_ticket_link_type: str = Field("RelevantTo", max_length=200)
    problem_link_type: str = Field("ParentChild", max_length=200)
    #: 0 = not configured -> the "known error" button is disabled with a hint
    known_error_kb_category_id: int = Field(0, ge=0)
    #: default for services without an own value; 0 = off
    min_minutes_between_incidents: int = Field(0, ge=0, le=10080)
    linked_articles_max_tickets: int = Field(200, ge=1, le=1000)
    major_incident_customer_reply: bool = False
    #: "" = derive from SysConfig HttpType/FQDN/ScriptAlias
    znuny_agent_url: str = Field("", max_length=500)

    @field_validator("znuny_agent_url")
    @classmethod
    def _http_url_or_empty(cls, value: str) -> str:
        value = value.strip()
        if value and not value.lower().startswith(("http://", "https://")):
            raise ValueError("Die Adresse muss mit http:// oder https:// beginnen.")
        return value


assert not RESERVED_ITSM_SECTION_FIELDS & set(ItsmSettings.model_fields)  # noqa: S101

ITSM_SECTION = SettingsSection(
    key="itsm",
    model=ItsmSettings,
    title_key="admin.settings.itsm.title",
    i18n_prefix="admin.settings.itsm",
)


async def load_itsm_settings(session: AsyncSession, settings: Settings) -> ItsmSettings:
    """The one way every ITSM module loads its settings (the section has no secrets)."""
    values = await load_section_values(session, ITSM_SECTION, secret_key=settings.secret_key)
    return ItsmSettings.model_validate(values.model_dump())
