"""Admin switch for deploying processes and ACLs from Tiqora (settings section)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tiqora.settings_sections.section import SettingsSection


class ProcessDeploySettings(BaseModel):
    deploy_from_tiqora: bool = Field(
        False,
        title="Prozesse und ACLs in Tiqora bereitstellen (Znuny läuft nicht mehr)",
        description=(
            "Nur einschalten, wenn Znuny nicht mehr parallel läuft. "
            "Sonst zeigt Znuny weiter den alten Stand an."
        ),
    )


PROCESS_DEPLOY_SECTION = SettingsSection(
    key="process",
    model=ProcessDeploySettings,
    title_key="admin.settings.process.title",
    i18n_prefix="admin.settings.process",
)
