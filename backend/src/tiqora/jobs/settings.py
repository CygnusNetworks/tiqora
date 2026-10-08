"""Admin-editable settings for the background job worker."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tiqora.settings_sections.section import SettingsSection


class JobsSettings(BaseModel):
    max_concurrency: int = Field(2, ge=1, le=8)
    retention_days: int = Field(14, ge=1, le=365)


JOBS_SECTION = SettingsSection(
    key="jobs",
    model=JobsSettings,
    title_key="admin.settings.jobs.title",
    i18n_prefix="admin.settings.jobs",
)
