"""Settings section ``cmdb`` (D14), stored in ``tiqora_settings`` as ``cmdb.<field>``."""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.settings_sections.section import SettingsSection
from tiqora.settings_sections.store import load_section_values


class CmdbSettings(BaseModel):
    propagation_node_cap: int = Field(5000, ge=100, le=50000)
    impact_graph_node_cap: int = Field(300, ge=50, le=1000)
    #: capped at 25 MB so the shared upload middleware needs no allow-list entry
    import_max_mb: int = Field(25, ge=1, le=25)
    version_archive_enabled: bool = False
    version_archive_keep: int = Field(50, ge=5, le=1000)
    version_archive_min_age_days: int = Field(365, ge=30, le=3650)
    #: optional Znuny group (name); empty = off. Non-admins need ``rw`` in it to use the CMDB
    #: at all, which keeps Znuny's ``itsm-configitem`` gate on sites that imported from Znuny.
    access_group: str = Field("", max_length=100)


CMDB_SECTION = SettingsSection(
    key="cmdb",
    model=CmdbSettings,
    title_key="admin.settings.cmdb.title",
    i18n_prefix="admin.settings.cmdb",
)


async def load_cmdb_settings(session: AsyncSession) -> CmdbSettings:
    """The section has no secret fields, so no secret key is needed to read it."""
    values = await load_section_values(session, CMDB_SECTION, secret_key="")
    return CmdbSettings.model_validate(values.model_dump())


__all__ = ["CMDB_SECTION", "CmdbSettings", "load_cmdb_settings"]
