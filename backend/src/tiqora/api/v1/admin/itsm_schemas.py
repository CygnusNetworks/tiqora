"""Schemas of ``/api/v1/admin/itsm``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from tiqora.domain.schemas import UtcDateTime

ItsmModeName = Literal["off", "znuny_readthrough", "tiqora"]


class ItsmDetectedOut(BaseModel):
    """What of Znuny's ITSM is installed in the Znuny database."""

    packages: dict[str, str]
    core_installed: bool
    cmdb_installed: bool
    change_installed: bool
    profile: str | None
    #: e.g. "ITSMConfigurationManagement 7.4.0 not surveyed"; the UI points at the importer's
    #: "Umstellen ohne CI-Daten" run for such sites
    unsupported_reason: str | None


class ItsmSwitchOut(BaseModel):
    switched_at: str | None
    by: int | None
    run_id: int | None


class ItsmAdminOut(BaseModel):
    mode: ItsmModeName
    source: Literal["auto", "admin", "importer"]
    stored: bool
    changed_at: UtcDateTime | None
    changed_by: int | None
    import_run_id: int | None
    previous_mode: ItsmModeName | None
    #: an import switched the site to Mode B (``itsm.switch`` exists)
    switched: bool
    switch: ItsmSwitchOut | None
    detected: ItsmDetectedOut
    cmdb_empty: bool
    allowed_transitions: list[ItsmModeName]


class ItsmModeIn(BaseModel):
    mode: ItsmModeName
