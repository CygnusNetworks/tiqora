"""Reporting settings section (admin-configurable, stored in ``tiqora_settings``)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.domain.timezones import default_time_zone, valid_time_zone
from tiqora.settings_sections.section import SettingsSection
from tiqora.settings_sections.store import load_section_values
from tiqora.znuny.sysconfig import SysConfig


class ReportingSettings(BaseModel):
    #: "" = Znuny default zone (UserDefaultTimeZone / OTRSTimeZone)
    time_zone: str = Field("", max_length=64)
    access: Literal["all_agents", "stats_group"] = "all_agents"
    near_miss_percent: int = Field(10, ge=1, le=50)
    cache_seconds: int = Field(300, ge=0, le=3600)
    inline_row_threshold: int = Field(200_000, ge=1_000, le=10_000_000)
    max_result_rows: int = Field(5_000, ge=100, le=10_000)
    export_row_limit: int = Field(100_000, ge=1_000, le=1_000_000)
    query_timeout_seconds: int = Field(60, ge=5, le=600)


REPORTING_SECTION = SettingsSection(
    key="reporting",
    model=ReportingSettings,
    title_key="admin.settings.reporting.title",
    i18n_prefix="admin.settings.reporting",
)


async def load_reporting_settings(session: AsyncSession, settings: Settings) -> ReportingSettings:
    """The one way every reporting module loads its settings (the section has no secrets)."""
    values = await load_section_values(session, REPORTING_SECTION, secret_key=settings.secret_key)
    return ReportingSettings.model_validate(values.model_dump())


async def reporting_time_zone(session: AsyncSession, values: ReportingSettings) -> str:
    return valid_time_zone(values.time_zone) or await default_time_zone(SysConfig(session))
