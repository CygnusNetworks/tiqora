"""Schemas of ``/api/v1/itsm``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from tiqora.api.v1.admin.itsm_schemas import LinkPropagation
from tiqora.domain.schemas import UtcDateTime
from tiqora.itsm.priority import MatrixView


class LinkTypeOptionOut(BaseModel):
    """A link type offered for an object pair. Labels: override, else i18n, else Znuny's name."""

    name: str
    source_label: str | None
    target_label: str | None
    source_name: str | None
    target_name: str | None
    pointed: bool
    propagation: LinkPropagation
    origin: Literal["znuny", "tiqora"]


# --- CI search, detail and ticket links (Task 14) ------------------------------------------


class ItsmZnunyInfoOut(BaseModel):
    """What of Znuny's ITSM is installed (same facts as the admin page, without the change flag)."""

    core_installed: bool
    cmdb_installed: bool
    profile: str | None
    packages: dict[str, str]


class ItsmStatusOut(BaseModel):
    """``GET /itsm/status`` (no gate): what the UI may offer right now."""

    mode: Literal["off", "znuny_readthrough", "tiqora"]
    #: who set the mode: the automatic default, an admin or the importer
    source: Literal["auto", "admin", "importer"]
    #: a CI provider answers (mode on, its tables usable)
    ci_available: bool
    ci_read_only: bool
    #: ``znuny`` (Mode A) or ``tiqora`` (Mode B); ``None`` while no provider answers
    ci_source: Literal["znuny", "tiqora"] | None
    #: ``unsupported_znuny_itsm_version`` | ``znuny_itsm_missing`` | ``cmdb_not_installed``
    unavailable_code: str | None
    znuny: ItsmZnunyInfoOut
    #: the ticket types, the workaround state and the criticality/impact fields exist and are valid
    setup_complete: bool


class CatalogValueOut(BaseModel):
    key: str
    name: str
    functionality: str | None


class CiClassOut(BaseModel):
    key: str
    name: str
    icon: str | None
    can_read: bool
    can_write: bool


class CatalogOut(BaseModel):
    classes: list[CiClassOut]
    deployment_states: list[CatalogValueOut]
    incident_states: list[CatalogValueOut]


class CiSummaryOut(BaseModel):
    #: ``"znuny:123"`` — the form every CI link in the API and the UI uses
    ref: str
    source: Literal["znuny", "tiqora"]
    id: int
    number: str
    name: str
    class_key: str
    class_name: str
    deployment_state: CatalogValueOut | None
    incident_state: CatalogValueOut | None
    changed_at: UtcDateTime | None
    edit_url: str | None


class CiPageOut(BaseModel):
    items: list[CiSummaryOut]
    #: exact unless ``truncated``
    total: int
    truncated: bool


class CiAttributeOut(BaseModel):
    key: str
    label: str
    kind: str
    display: str
    raw: str | None
    ref: str | None
    children: list[CiAttributeOut] = []


class CiVersionOut(BaseModel):
    id: int
    seq: int
    name: str
    deployment_state: CatalogValueOut | None
    incident_state: CatalogValueOut | None
    created_at: UtcDateTime
    created_by: int | None
    created_by_name: str | None
    change_note: str | None


class CiDetailOut(BaseModel):
    summary: CiSummaryOut
    version: CiVersionOut | None
    attributes: list[CiAttributeOut]
    definition_version: int | None
    read_only: bool


class CiVersionPageOut(BaseModel):
    items: list[CiVersionOut]
    total: int


class CiTicketLinkOut(BaseModel):
    ticket_id: int
    ci: CiSummaryOut
    link_type: str
    #: the ticket's side of the link; ``both`` for unpointed types
    direction: Literal["source", "target", "both"]
    created_at: UtcDateTime | None


class CiTicketLinkIn(BaseModel):
    #: ``"znuny:12"``
    ci: str = Field(min_length=3, max_length=40)
    #: default: ``ItsmSettings.ci_ticket_link_type``
    link_type: str | None = Field(None, max_length=200)


# --- priority matrix (Task 16) -------------------------------------------------------------


class MatrixCellOut(BaseModel):
    criticality: str
    impact: str
    priority_id: int


class MatrixOut(BaseModel):
    """The criticality x impact grid; source "znuny" is ``cip_allocate`` (read-only)."""

    source: Literal["znuny", "tiqora"]
    editable: bool
    #: whether the matrix is applied to tickets (setting ``priority_matrix_enabled``, mode not off)
    enabled: bool
    criticality_values: list[str]
    impact_values: list[str]
    cells: list[MatrixCellOut]


class PrioritySuggestionOut(BaseModel):
    priority_id: int | None


def matrix_out(view: MatrixView, *, enabled: bool) -> MatrixOut:
    return MatrixOut(
        source=view.source,
        editable=view.editable,
        enabled=enabled,
        criticality_values=list(view.criticality_values),
        impact_values=list(view.impact_values),
        cells=[
            MatrixCellOut(criticality=c.criticality, impact=c.impact, priority_id=c.priority_id)
            for c in view.cells
        ],
    )
