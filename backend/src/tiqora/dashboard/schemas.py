"""API models of the dashboards (D3)."""

from __future__ import annotations

import re
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from tiqora.dashboard.filters import DashboardFilters
from tiqora.dashboard.layout import Rect
from tiqora.dashboard.settings_models import MAX_WIDGET_SETTINGS_BYTES, WIDGET_KEY_RE
from tiqora.domain.schemas import TicketListItem, UtcDateTime

__all__ = [
    "MAX_DASHBOARDS_PER_USER",
    "MAX_WIDGETS_PER_DASHBOARD",
    "MAX_WIDGET_SETTINGS_BYTES",
    "WIDGET_ID_RE",
    "WIDGET_KEY_RE",
    "Rect",
]

WIDGET_ID_RE: Final = re.compile(r"^[A-Za-z0-9_-]{8,36}$")
MAX_WIDGETS_PER_DASHBOARD: Final = 40
MAX_DASHBOARDS_PER_USER: Final = 20

_Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
#: Bounded ids: a value outside the column range is a 422, not an asyncpg ``DataError`` (500).
DashboardIdIn = Annotated[int, Field(ge=1, le=2**63 - 1)]  # tiqora_dashboard.id (BIGINT)
ZnunyIdIn = Annotated[int, Field(ge=1, le=2**31 - 1)]  # Znuny roles.id / groups.id (INT)


class WidgetLayouts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lg: Rect  # 12 columns
    md: Rect | None = None  # 8 columns; None = derived on the client


class WidgetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=WIDGET_ID_RE.pattern)
    type_key: str = Field(pattern=WIDGET_KEY_RE.pattern)
    type_version: int = Field(1, ge=1, le=1000)
    title_override: str | None = Field(None, max_length=200)
    settings: dict[str, Any] = Field(default_factory=dict)
    layout: WidgetLayouts
    #: True: this widget ignores the dashboard-wide filters
    pinned: bool = False


class WidgetOut(WidgetIn):
    """Same fields as ``WidgetIn``, settings as stored."""


class DashboardTabOut(BaseModel):
    id: int
    name: str
    icon: str | None
    kind: Literal["personal", "template", "shared"]
    locked: bool
    is_owner: bool
    can_edit: bool
    can_share: bool
    owner_name: str | None  # set when not is_owner
    version: int
    sort: int
    group_ids: list[int]
    source_template_id: int | None


class TemplateUpdateHint(BaseModel):
    """A newer version of the source template has widgets this copy lacks (Task 8 fills it)."""

    template_id: int
    template_name: str
    template_version: int
    new_widget_count: int


class DashboardOut(DashboardTabOut):
    widgets: list[WidgetOut]  # in phone order
    filters: DashboardFilters = Field(default_factory=DashboardFilters)
    #: set only by ``GET /dashboards/{id}`` for an editable template copy
    template_update: TemplateUpdateHint | None = None


class TemplateUpdateOut(BaseModel):
    """The widgets a merge would add to a template copy, already placed."""

    template_id: int
    template_name: str
    template_version: int
    widgets: list[WidgetOut]


class TemplateUpdateApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["merge", "dismiss"]
    version: int = Field(ge=1)


class DashboardSaveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: _Name
    icon: str | None = Field(None, max_length=40)
    version: int = Field(ge=1)
    widgets: list[WidgetIn] = Field(max_length=MAX_WIDGETS_PER_DASHBOARD)
    #: ``None`` keeps the stored dashboard filters
    filters: DashboardFilters | None = None

    @model_validator(mode="after")
    def _unique_widget_ids(self) -> DashboardSaveIn:
        ids = [w.id for w in self.widgets]
        if len(set(ids)) != len(ids):
            raise ValueError("Widget-IDs müssen eindeutig sein.")
        return self


class DashboardFiltersIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: DashboardFilters
    version: int = Field(ge=1)


class DashboardCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: _Name
    icon: str | None = Field(None, max_length=40)
    copy_from: DashboardIdIn | None = None  # duplicate a visible dashboard (widgets get new ids)


class DashboardOrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[DashboardIdIn] = Field(max_length=MAX_DASHBOARDS_PER_USER)


class WidgetSettingsPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: dict[str, Any]
    version: int = Field(ge=1)


class DashboardShareIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_ids: list[ZnunyIdIn] = Field(max_length=50)


class WidgetTypeOut(BaseModel):
    key: str
    version: int
    has_data: bool  # a provider is registered


class WidgetDataOut(BaseModel):
    status: Literal["ok", "pending", "error", "forbidden"]
    data: dict[str, Any] | None = None
    error: str | None = None  # German, user-presentable


class DashboardDataOut(BaseModel):
    widgets: dict[str, WidgetDataOut]  # widget id -> result; widgets without a provider are absent
    generated_at: UtcDateTime


class DrillPageOut(BaseModel):
    """The tickets behind a widget or a chart point, as one page (E5)."""

    title: str  # German, e.g. "Meine offenen Tickets" / "Bericht „Volumen“ · Queue Support"
    items: list[TicketListItem]
    total: int
    #: exact wave-0 filter for "Als Suche speichern"; ``None`` when none expresses the list
    filter: dict[str, Any] | None = None
    #: e.g. "Der Bericht kann einige Minuten älter sein als diese Liste."
    note: str | None = None


class WidgetPreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: dict[str, Any] = Field(default_factory=dict)
    #: the open dashboard's filters (they only narrow)
    filters: DashboardFilters | None = None


class DashboardTemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: _Name
    icon: str | None = Field(None, max_length=40)
    role_ids: list[ZnunyIdIn] = Field(max_length=50)
    locked: bool = False
    sort: int = Field(0, ge=0, le=1000)


class DashboardTemplateOut(DashboardTabOut):
    role_ids: list[int]
    widget_count: int


class KioskLinkIn(BaseModel):
    """Admin request for a new wallboard link."""

    model_config = ConfigDict(extra="forbid")

    name: _Name
    dashboard_ids: list[DashboardIdIn] = Field(min_length=1, max_length=10)
    rotate_seconds: int = Field(60, ge=15, le=900)
    show_details: bool = False
    expires_in_days: int = Field(90, ge=1, le=365)

    @model_validator(mode="after")
    def _unique_dashboards(self) -> KioskLinkIn:
        if len(set(self.dashboard_ids)) != len(self.dashboard_ids):
            raise ValueError("Jedes Dashboard darf nur einmal vorkommen.")
        return self


class KioskDashboardRef(BaseModel):
    id: int
    name: str
    #: False once the link's creator can no longer see the dashboard
    visible: bool


class KioskLinkOut(BaseModel):
    id: int
    name: str
    dashboards: list[KioskDashboardRef]
    rotate_seconds: int
    show_details: bool
    token_hint: str
    created_by: int
    created_by_name: str | None
    created_at: UtcDateTime
    expires_at: UtcDateTime
    revoked_at: UtcDateTime | None
    last_used_at: UtcDateTime | None
    #: not revoked and not expired
    active: bool


class KioskLinkCreatedOut(KioskLinkOut):
    #: the secret; the only response that ever carries it
    token: str


class KioskWidgetOut(BaseModel):
    """A widget as the wallboard gets it: only what it needs to render (E9 ruling).

    ``settings`` is a per-type whitelist subset; ``title_override`` only with details. Never the
    ticket source, report key, parameters, notes or links (unless the link shows details).
    """

    id: str
    type_key: str
    type_version: int
    layout: WidgetLayouts
    settings: dict[str, Any] = Field(default_factory=dict)
    title_override: str | None = None


class KioskDashboardOut(BaseModel):
    id: int
    name: str
    filters: DashboardFilters = Field(default_factory=DashboardFilters)
    widgets: list[KioskWidgetOut]


class KioskBoardOut(BaseModel):
    name: str
    rotate_seconds: int
    #: how often the wallboard polls a dashboard's data
    refresh_seconds: int
    show_details: bool
    #: the link's dashboards in rotation order (those the creator can still see)
    dashboards: list[KioskDashboardOut]
    server_time: UtcDateTime
