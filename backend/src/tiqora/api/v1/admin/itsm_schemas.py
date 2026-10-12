"""Schemas of ``/api/v1/admin/itsm``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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


# --- link types ----------------------------------------------------------------------------------

LinkPropagation = Literal["none", "source", "target", "both"]


class LinkTypeOut(BaseModel):
    id: int
    name: str
    origin: Literal["znuny", "tiqora"]
    source_label: str | None
    target_label: str | None
    pointed: bool
    propagation: LinkPropagation
    offer_ticket_ticket: bool
    offer_ticket_ci: bool
    offer_ci_ci: bool
    offer_ci_service: bool
    position: int
    valid: bool
    #: Znuny's own names (SysConfig ``LinkObject::Type###``), None for a Tiqora-only type
    znuny_source_name: str | None = None
    znuny_target_name: str | None = None


class LinkTypeListOut(BaseModel):
    items: list[LinkTypeOut]
    #: Znuny types without a row: still offered, "Aus Znuny aktualisieren" creates the rows
    missing_from_table: list[str]


class LinkTypeCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,49}$")
    source_label: str | None = Field(default=None, max_length=100)
    target_label: str | None = Field(default=None, max_length=100)
    pointed: bool = False
    propagation: LinkPropagation = "none"
    offer_ticket_ticket: bool = False
    offer_ticket_ci: bool = False
    offer_ci_ci: bool = True
    offer_ci_service: bool = False
    position: int | None = Field(default=None, ge=0, le=100000)


class LinkTypePatchIn(BaseModel):
    """Only the keys sent are changed. ``name`` may be sent but never differ (immutable)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    source_label: str | None = Field(default=None, max_length=100)
    target_label: str | None = Field(default=None, max_length=100)
    pointed: bool | None = None
    propagation: LinkPropagation | None = None
    offer_ticket_ticket: bool | None = None
    offer_ticket_ci: bool | None = None
    offer_ci_ci: bool | None = None
    offer_ci_service: bool | None = None
    position: int | None = Field(default=None, ge=0, le=100000)
    valid: bool | None = None


class LinkTypeSyncOut(BaseModel):
    changed: int
