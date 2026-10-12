"""Designer DTOs (plan D11): drafts, entity summaries, write bodies and the catalogue."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from tiqora.domain.schemas import UtcDateTime
from tiqora.process.designer.ids import EntityType
from tiqora.process.schemas import ProcessDialogOptionOut

SyncState = Literal["synced", "not_sync", "deleted"]


class DesignerEntityOut(BaseModel):
    entity_type: EntityType
    entity_id: str
    id: int
    name: str
    config: dict[str, Any]  # parsed YAML, as stored
    version: str
    sync_state: SyncState = "synced"
    changed_at: UtcDateTime
    changed_by: int


class ProcessDraftOut(BaseModel):
    process: DesignerEntityOut  # config = {Description, Path, StartActivity, StartActivityDialog}
    state_entity_id: str  # S1/S2/S3
    state_name: str  # Active | Inactive | FadeAway
    layout: dict[str, dict[str, str]]
    activities: list[DesignerEntityOut]  # Path keys, in Path order of first appearance
    activity_dialogs: list[DesignerEntityOut]  # referenced by those activities
    transitions: list[DesignerEntityOut]  # referenced in Path
    transition_actions: list[DesignerEntityOut]  # referenced in Path
    missing_references: list[str]  # ids referenced but not found (shown as broken)
    pending_changes: int  # COUNT(pm_entity_sync), global like Znuny's banner


class EntitySummaryOut(BaseModel):
    entity_type: EntityType
    entity_id: str
    name: str
    used_by: list[str]  # process ids (Activity/Transition/TransitionAction) or activity ids
    sync_state: SyncState


class ProcessDraftSummaryOut(EntitySummaryOut):
    """``GET /processes`` row: a summary plus the process state (ruling: list shows drafts)."""

    state_entity_id: str
    state_name: str


class ProcessCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=10000)


class ProcessUpdateIn(BaseModel):
    expected_version: str
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=10000)
    state_entity_id: Literal["S1", "S2", "S3"]
    start_activity: str = ""
    start_activity_dialog: str = ""
    path: dict[str, dict[str, dict[str, Any]]]
    layout: dict[str, dict[str, str | int]]


class EntityCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    config: dict[str, Any]


class EntityUpdateIn(EntityCreateIn):
    expected_version: str


# --- catalogue -----------------------------------------------------------------------------


class CatalogParamOut(BaseModel):
    name: str
    kind: str  # ParamKind
    required: bool
    one_of: list[str]
    list_value: bool


class CatalogActionOut(BaseModel):
    name: str
    module: str
    supported: bool  # run is not None
    tiqora_only: bool
    params: list[CatalogParamOut]


class CatalogFieldOut(BaseModel):
    name: str  # canonical dialog field / condition field name
    label: str
    kind: str  # ticket attribute kind or DF type
    dialog: bool  # offered as dialog field
    condition: bool  # offered as condition field
    options: list[ProcessDialogOptionOut] = Field(default_factory=list)


class DesignerCatalogOut(BaseModel):
    actions: list[CatalogActionOut]
    condition_types: list[str]
    condition_modules: list[str]  # registered Match strings
    fields: list[CatalogFieldOut]
    interfaces: list[str]
    permissions: list[str]  # Znuny permission keys ("" = none)
    account_time: bool  # Ticket::Frontend::AccountTime
