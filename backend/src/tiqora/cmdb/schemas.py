"""API DTOs of the CMDB class/catalog/definition surface (D10).

Later tasks append their DTOs here (CIs, links, search, ...). Datetimes use
:data:`tiqora.domain.schemas.UtcDateTime`; definitions use the typed :class:`AttributeDef`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tiqora.cmdb.definition import AttributeDef, AttributeType
from tiqora.cmdb.render import RefKind
from tiqora.cmdb.transfer.mapping import ImportOptions, MappingRowIn
from tiqora.domain.field_options import FieldOptionOut
from tiqora.domain.schemas import UtcDateTime
from tiqora.filters.model import Filter

ItsmModeOut = Literal["off", "znuny_readthrough", "tiqora"]


class CmdbStatusOut(BaseModel):
    mode: ItsmModeOut
    readable_class_count: int
    can_admin: bool
    views_present: bool


class CmdbAdminStatusOut(BaseModel):
    mode: ItsmModeOut
    views_present: bool
    catalog_seeded: bool
    class_count: int
    ci_count: int
    archived_version_count: int


class CatalogItemOut(BaseModel):
    id: int
    catalog_class: str
    item_key: str
    name: str
    functionality: str | None
    position: int
    valid: bool


class CatalogItemIn(BaseModel):
    """``catalog_class`` and ``item_key`` are fixed after creation (a PUT must repeat them)."""

    catalog_class: str = Field(min_length=1, max_length=100)
    item_key: str = Field(pattern=r"^[A-Za-z0-9_]{1,100}$")
    name: str = Field(min_length=1, max_length=200)
    functionality: str | None = Field(None, max_length=50)
    position: int = Field(0, ge=0, le=100000)
    valid: bool = True


class DefinitionIssueOut(BaseModel):
    path: str
    code: str
    message: str


class DefinitionChangeOut(BaseModel):
    path: str
    kind: Literal[
        "added", "removed", "type_changed", "count_changed", "renamed_label", "flags_changed"
    ]
    detail: str


class CmdbDefinitionOut(BaseModel):
    id: int
    class_id: int
    version: int
    attributes: list[AttributeDef]
    comment: str | None
    created_by: int
    created_at: UtcDateTime


class CmdbDefinitionIn(BaseModel):
    attributes: list[AttributeDef] = Field(max_length=500)
    comment: str | None = Field(None, max_length=250)


class DefinitionValidateIn(BaseModel):
    class_id: int | None = Field(None, ge=1)
    attributes: list[AttributeDef] = Field(max_length=500)


class DefinitionImportIn(BaseModel):
    format: Literal["znuny_yaml", "znuny_perl", "tiqora_json"]
    text: str = Field(max_length=1024 * 1024)


class DefinitionImportOut(BaseModel):
    attributes: list[AttributeDef]
    warnings: list[str]
    missing_catalog_classes: list[str]


class CmdbClassOut(BaseModel):
    id: int
    class_key: str
    name: str
    icon: str | None
    number_prefix: str
    group_id: int
    group_name: str | None
    portal_visible: bool
    valid: bool
    position: int
    lock_version: int
    current_definition_version: int | None
    definition: CmdbDefinitionOut | None
    can_write: bool


class CmdbClassIn(BaseModel):
    """``class_key`` is immutable after create (a PUT must repeat it)."""

    class_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,49}$")
    name: str = Field(min_length=1, max_length=200)
    icon: str | None = Field(None, max_length=50)
    number_prefix: str | None = Field(None, max_length=20)
    group_id: int = Field(ge=1)
    portal_visible: bool = False
    valid: bool = True
    position: int = Field(0, ge=0, le=100000)


class CmdbClassUpdateIn(CmdbClassIn):
    expected_lock_version: int = Field(ge=1)


class ClassTemplateOut(BaseModel):
    key: str
    name: str
    icon: str
    attribute_count: int


class ClassTemplateApplyIn(BaseModel):
    group_id: int = Field(ge=1)


class CmdbLinkTypeOut(BaseModel):
    """Read model of a valid link type offered between CIs (route: Task 11)."""

    name: str
    source_label: str | None
    target_label: str | None
    pointed: bool
    propagation: Literal["none", "source", "target", "both"]
    offer_ci_ci: bool
    offer_ci_service: bool
    offer_ticket_ci: bool
    position: int


class RefLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: RefKind
    key: str


class RenderedAttrOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    path: str
    key: str
    label: str
    type: AttributeType
    display: str | None
    ref: RefLinkOut | None
    children: list[RenderedAttrOut]
    portal: bool


RenderedAttrOut.model_rebuild()


class CiVersionSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    definition_id: int
    definition_version: int
    name: str
    deployment_state_id: int
    incident_state_id: int
    deployment_state: str
    incident_state: str
    change_note: str | None
    created_by: int
    created_by_name: str | None
    created_at: UtcDateTime
    archived: bool


class CmdbCiVersionOut(BaseModel):
    """One version: raw document (``attributes``) and its display tree (``rendered``)."""

    id: int
    ci_id: int
    definition_id: int
    definition_version: int
    name: str
    deployment_state_id: int
    incident_state_id: int
    attributes: dict[str, Any]
    rendered: list[RenderedAttrOut]
    change_note: str | None
    created_by: int
    created_at: UtcDateTime
    archived: bool


class CiVersionPage(BaseModel):
    items: list[CiVersionSummaryOut]
    has_more: bool
    archived_count: int


class DiffRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    path: str
    label: str
    kind: Literal["added", "removed", "changed"]
    old: str | None
    new: str | None


class CiDiffOut(BaseModel):
    from_version_id: int
    to_version_id: int
    rows: list[DiffRowOut]


class CiHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event: str
    detail: str
    version_id: int | None
    created_by: int
    created_by_name: str | None
    created_at: UtcDateTime


class CiHistoryPage(BaseModel):
    items: list[CiHistoryOut]
    has_more: bool


class CmdbNamedRefOut(BaseModel):
    id: str
    label: str


class CmdbFilterFieldOut(BaseModel):
    key: str
    type: Literal["id", "string", "int", "datetime", "bool", "enum"]
    ops: list[str]
    label_key: str | None
    label: str | None
    enum_values: list[str]
    options_source: str | None


class CmdbFormOptionsOut(BaseModel):
    """Option lists for the reference attributes the class definition uses; a type the
    definition does not use is absent from the answer (``null`` is never sent)."""

    catalog: dict[str, list[FieldOptionOut]] | None = None
    service: list[FieldOptionOut] | None = None
    sla: list[FieldOptionOut] | None = None
    queue: list[FieldOptionOut] | None = None
    priority: list[FieldOptionOut] | None = None
    ticket_type: list[FieldOptionOut] | None = None
    state: list[FieldOptionOut] | None = None
    agent: list[FieldOptionOut] | None = None


class CiClassRefOut(BaseModel):
    id: int
    class_key: str
    name: str
    icon: str | None
    group_id: int


class CiStatesOut(BaseModel):
    deployment: CatalogItemOut
    incident_own: CatalogItemOut
    #: the derived state (own state, or ``warning`` through propagation)
    incident_current: CatalogItemOut


class CmdbAttachmentOut(BaseModel):
    id: int
    #: ``None`` = staged upload that is not attached to a CI yet
    ci_id: int | None
    filename: str
    content_type: str
    size_bytes: int
    created_at: UtcDateTime


class CiCreateIn(BaseModel):
    class_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=250)
    deployment_state_id: int = Field(ge=1)
    incident_state_id: int = Field(ge=1)
    attributes: dict[str, Any] = Field(default_factory=dict)
    change_note: str | None = Field(None, max_length=500)
    attachment_ids: list[int] = Field(default_factory=list, max_length=20)


class CiUpdateIn(BaseModel):
    expected_lock_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=250)
    deployment_state_id: int = Field(ge=1)
    incident_state_id: int = Field(ge=1)
    #: the full attribute set; absent or ``null`` keeps the current values (D11)
    attributes: dict[str, Any] | None = None
    change_note: str | None = Field(None, max_length=500)
    #: confirm that attribute keys the class definition no longer knows are lost
    drop_unknown: bool = False


class CiOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    number: str
    class_: CiClassRefOut = Field(serialization_alias="class")
    name: str
    deployment_state_id: int
    incident_state_id: int
    states: CiStatesOut
    version_count: int
    lock_version: int
    current_version: CmdbCiVersionOut | None
    rendered: list[RenderedAttrOut]
    #: top-level keys of the current version that the class's current definition lacks
    unknown_keys: list[str]
    can_write: bool
    created_by: int
    created_at: UtcDateTime
    changed_by: int
    changed_at: UtcDateTime


class CiSaveOut(BaseModel):
    ci: CiOut
    changed: bool


CiSortKey = Literal["number", "name", "class", "changed_at", "deployment_state", "incident_state"]


class CiSearchCriteria(BaseModel):
    """The stored part of a search: everything except paging."""

    class_ids: list[int] = Field(default_factory=list, max_length=100)
    filter: Filter | None = None
    q: str | None = Field(default=None, max_length=200)
    sort: CiSortKey = "number"
    order: Literal["asc", "desc"] = "asc"


class CiSearchIn(CiSearchCriteria):
    offset: int = Field(0, ge=0, le=100000)
    limit: int = Field(50, ge=1, le=200)


class CiListItemOut(BaseModel):
    id: int
    number: str
    name: str
    class_key: str
    class_name: str
    deployment_state: str
    deployment_functionality: str | None
    incident_state: str
    incident_functionality: str | None
    changed_at: UtcDateTime


class CiPage(BaseModel):
    items: list[CiListItemOut]
    #: exact up to the count cap (10000), then the cap itself with ``total_capped``
    total: int
    total_capped: bool


class CiRefOut(BaseModel):
    id: int
    number: str
    name: str
    class_key: str
    class_name: str


LinkKind = Literal["ci", "ticket", "service", "kb_article", "customer_user", "customer_company"]


class LinkIn(BaseModel):
    """``POST /links``: one stored orientation is chosen by the server (D8)."""

    source_kind: LinkKind
    source_key: str = Field(min_length=1, max_length=100)
    target_kind: LinkKind
    target_key: str = Field(min_length=1, max_length=100)
    link_type: str = Field(min_length=1, max_length=50)


class LinkedObjectOut(BaseModel):
    """The other end of a link; ``hidden`` ends carry no key, label or url."""

    kind: LinkKind
    key: str
    label: str
    hidden: bool
    url: str | None


class LinkOut(BaseModel):
    id: int
    link_type: str
    link_type_label: str
    direction: Literal["outgoing", "incoming", "undirected"]
    other: LinkedObjectOut
    created_by: str | None
    created_at: UtcDateTime


class ImpactNodeOut(BaseModel):
    """A graph node. A hidden CI carries only ``id``, ``kind``, ``hidden`` and ``depth``."""

    id: str
    kind: Literal["ci", "service"]
    ci_id: int | None = None
    number: str | None = None
    name: str | None = None
    class_key: str | None = None
    incident_functionality: str | None = None
    hidden: bool
    depth: int


class ImpactEdgeOut(BaseModel):
    source: str
    target: str
    link_type: str
    propagates: bool


class ImpactGraphOut(BaseModel):
    nodes: list[ImpactNodeOut]
    edges: list[ImpactEdgeOut]
    truncated: bool
    root: str


class CiTicketOut(BaseModel):
    ticket_id: int
    number: str
    title: str
    queue_id: int
    queue_name: str
    state_name: str
    state_type: str
    open: bool
    link_type: str


class AffectedOut(BaseModel):
    ci_count: int
    services: list[CmdbNamedRefOut]
    service_count: int
    customers: list[CmdbNamedRefOut]
    customer_count: int
    open_tickets: list[CiTicketOut]
    open_ticket_count: int
    truncated: bool


class CmdbSavedSearchIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    shared: bool = False
    criteria: CiSearchCriteria


class CmdbSavedSearchOut(BaseModel):
    id: int
    name: str
    shared: bool
    owned: bool
    owner_user_id: int
    criteria: CiSearchCriteria
    created_at: UtcDateTime
    changed_at: UtcDateTime


class ImportTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    class_id: int = Field(ge=1)
    file_format: Literal["csv", "xlsx"]
    options: ImportOptions
    mapping: list[MappingRowIn] = Field(max_length=500)
    shared: bool = False
    #: required for a PUT (optimistic locking), ignored on create
    lock_version: int | None = Field(None, ge=1)


class ImportTemplateOut(BaseModel):
    id: int
    name: str
    class_id: int
    file_format: Literal["csv", "xlsx"]
    options: ImportOptions
    mapping: list[MappingRowIn]
    shared: bool
    owned: bool
    owner_user_id: int
    lock_version: int
    created_at: UtcDateTime
    changed_at: UtcDateTime


class CmdbExportIn(BaseModel):
    """``POST /export-jobs``: one class (attribute columns need one definition)."""

    class_id: int = Field(ge=1)
    filter: Filter | None = None
    q: str | None = Field(default=None, max_length=200)
    file_format: Literal["csv", "xlsx"] = "csv"


class BulkLinkIn(BaseModel):
    """The link every CI of a bulk edit gets; ``ci_side`` says which end the CI is."""

    link_type_key: str = Field(min_length=1, max_length=50)
    target_kind: LinkKind
    target_key: str = Field(min_length=1, max_length=100)
    ci_side: Literal["source", "target"] = "source"


class CmdbBulkIn(BaseModel):
    """``POST /bulk-jobs``: up to 5000 CIs get the same states and/or the same link."""

    ci_ids: list[int] = Field(min_length=1, max_length=5000)
    deployment_state_id: int | None = Field(default=None, ge=1)
    incident_state_id: int | None = Field(default=None, ge=1)
    link: BulkLinkIn | None = None
    change_note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _something_to_do(self) -> CmdbBulkIn:
        if self.deployment_state_id is None and self.incident_state_id is None and not self.link:
            raise ValueError("Es fehlt eine Änderung: Status oder Verknüpfung.")
        return self


class CmdbImportRequestIn(BaseModel):
    """The ``request`` form field of ``POST /import-jobs``: a saved template, or mapping and
    options spelled out. Without both the columns are mapped by their header text."""

    class_id: int | None = Field(None, ge=1)
    template_id: int | None = Field(None, ge=1)
    file_format: Literal["csv", "xlsx"] | None = None
    options: ImportOptions | None = None
    mapping: list[MappingRowIn] | None = Field(None, max_length=500)
    dry_run: bool = True
    #: IANA zone of the person importing; naive date-times in the file are read in it
    time_zone: str = Field("UTC", max_length=64)


class PortalCiOut(BaseModel):
    """A configuration item as the customer portal shows it (no agent-only data)."""

    id: int
    number: str
    name: str
    class_name: str
    deployment_state: str
    incident_state: str
    incident_functionality: str | None


class PortalCiPage(BaseModel):
    items: list[PortalCiOut]
    total: int


class PortalCiDetailOut(PortalCiOut):
    #: only attributes flagged ``portal``, without links into the agent UI
    rendered: list[RenderedAttrOut]


__all__ = [
    "PortalCiDetailOut",
    "PortalCiOut",
    "PortalCiPage",
    "CmdbImportRequestIn",
    "ImportOptions",
    "ImportTemplateIn",
    "ImportTemplateOut",
    "BulkLinkIn",
    "CmdbBulkIn",
    "CmdbExportIn",
    "MappingRowIn",
    "AffectedOut",
    "CiTicketOut",
    "ImpactEdgeOut",
    "ImpactGraphOut",
    "ImpactNodeOut",
    "CiClassRefOut",
    "CiCreateIn",
    "CiOut",
    "CiSaveOut",
    "CiStatesOut",
    "CiUpdateIn",
    "CmdbFilterFieldOut",
    "CmdbFormOptionsOut",
    "CmdbNamedRefOut",
    "CiListItemOut",
    "CiPage",
    "CiRefOut",
    "CiSearchCriteria",
    "CiSearchIn",
    "CiSortKey",
    "CmdbSavedSearchIn",
    "CmdbSavedSearchOut",
    "CiDiffOut",
    "CiHistoryOut",
    "CiHistoryPage",
    "CmdbCiVersionOut",
    "CiVersionPage",
    "CiVersionSummaryOut",
    "DiffRowOut",
    "RefLinkOut",
    "RenderedAttrOut",
    "CatalogItemIn",
    "CatalogItemOut",
    "ClassTemplateApplyIn",
    "ClassTemplateOut",
    "CmdbAdminStatusOut",
    "CmdbClassIn",
    "CmdbClassOut",
    "CmdbClassUpdateIn",
    "CmdbDefinitionIn",
    "CmdbDefinitionOut",
    "CmdbStatusOut",
    "DefinitionChangeOut",
    "DefinitionImportIn",
    "DefinitionImportOut",
    "DefinitionIssueOut",
    "DefinitionValidateIn",
    "ItsmModeOut",
    "LinkIn",
    "LinkKind",
    "LinkOut",
    "CmdbLinkTypeOut",
    "LinkedObjectOut",
]
