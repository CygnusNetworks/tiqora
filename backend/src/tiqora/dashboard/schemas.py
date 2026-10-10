"""API models of the dashboards (D3)."""

from __future__ import annotations

import re
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from tiqora.dashboard.layout import Rect
from tiqora.dashboard.settings_models import MAX_WIDGET_SETTINGS_BYTES, WIDGET_KEY_RE
from tiqora.domain.schemas import UtcDateTime

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


class DashboardOut(DashboardTabOut):
    widgets: list[WidgetOut]  # in phone order


class DashboardSaveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: _Name
    icon: str | None = Field(None, max_length=40)
    version: int = Field(ge=1)
    widgets: list[WidgetIn] = Field(max_length=MAX_WIDGETS_PER_DASHBOARD)

    @model_validator(mode="after")
    def _unique_widget_ids(self) -> DashboardSaveIn:
        ids = [w.id for w in self.widgets]
        if len(set(ids)) != len(ids):
            raise ValueError("Widget-IDs müssen eindeutig sein.")
        return self


class DashboardCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: _Name
    icon: str | None = Field(None, max_length=40)
    copy_from: int | None = None  # duplicate a visible dashboard (widgets get new ids)


class DashboardOrderIn(BaseModel):
    ids: list[int] = Field(max_length=MAX_DASHBOARDS_PER_USER)


class WidgetSettingsPatchIn(BaseModel):
    settings: dict[str, Any]
    version: int = Field(ge=1)


class DashboardShareIn(BaseModel):
    group_ids: list[int] = Field(max_length=50)


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


class WidgetPreviewIn(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: _Name
    icon: str | None = None
    role_ids: list[int] = Field(max_length=50)
    locked: bool = False
    sort: int = Field(0, ge=0, le=1000)


class TemplateOut(DashboardTabOut):
    role_ids: list[int]
    widget_count: int
