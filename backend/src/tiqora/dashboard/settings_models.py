"""Typed settings of the core dashboard widgets and their validation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Annotated, Any, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    ValidationError,
    field_validator,
)

WIDGET_KEY_RE: Final = re.compile(r"^[a-z0-9][a-z0-9._-]{1,118}$")
MAX_WIDGET_SETTINGS_BYTES: Final = 20_000
#: bigint column; the saved-searches API bounds its path id the same way
MAX_SEARCH_ID: Final = 2**63 - 1
LINK_SCHEMES: Final = ("http", "https", "mailto")


class SettingsError(ValueError):
    """Invalid widget settings or layout; ``path`` points at the offending field."""

    def __init__(self, message: str, *, path: str = "settings") -> None:
        super().__init__(message)
        self.message = message
        self.path = path


_NOT_ALLOWED = "Diese Adresse ist nicht erlaubt."


def safe_link_url(url: str) -> str:
    """Allow ``/path``, ``http(s)://`` and ``mailto:`` only; return the url unchanged."""
    if not url or url != url.strip() or any(ord(c) < 0x20 or ord(c) == 0x7F for c in url):
        raise SettingsError(_NOT_ALLOWED, path="settings.url")
    if url.startswith("/"):
        if url.startswith("//") or "\\" in url:
            raise SettingsError(_NOT_ALLOWED, path="settings.url")
        return url
    scheme, sep, rest = url.partition(":")
    if sep and scheme.lower() in LINK_SCHEMES and rest.removeprefix("//"):
        return url
    raise SettingsError(_NOT_ALLOWED, path="settings.url")


class PresetSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["preset"] = "preset"
    preset: Literal["mine_open", "mine_new", "unowned_new", "escalated", "ai_escalated"] = (
        "mine_open"
    )


class SavedSearchSource(BaseModel):
    """A saved search; read as the **viewer** (not the dashboard owner) when the tile runs."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["saved_search"] = "saved_search"
    id: int = Field(ge=1, le=MAX_SEARCH_ID)


class FilterSource(BaseModel):
    """An own filter (wave-0 ``Filter`` JSON); checked against the ticket fields on save."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["filter"] = "filter"
    filter: dict[str, Any]


def _source_kind(value: Any) -> str:
    """Discriminator; a missing ``kind`` means a preset (the default source)."""
    if isinstance(value, Mapping):
        return str(value.get("kind", "preset"))
    return str(getattr(value, "kind", "preset"))


_SOURCE_TAGS: Final = frozenset({"preset", "saved_search", "filter"})

type TicketSource = Annotated[
    Annotated[PresetSource, Tag("preset")]
    | Annotated[SavedSearchSource, Tag("saved_search")]
    | Annotated[FilterSource, Tag("filter")],
    Discriminator(_source_kind),
]

type TicketColumn = Literal[
    "tn", "title", "customer", "state", "due", "queue", "owner", "priority", "changed", "created"
]


def _default_columns() -> list[TicketColumn]:
    return ["tn", "title", "customer", "state", "due"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TicketListSettings(_Strict):
    source: TicketSource = Field(default_factory=PresetSource)
    columns: list[TicketColumn] = Field(
        default_factory=_default_columns, min_length=1, max_length=8
    )
    sort: Literal["age", "changed", "priority", "deadline"] = "age"
    order: Literal["asc", "desc"] = "desc"
    limit: int = Field(8, ge=1, le=50)
    density: Literal["compact", "comfortable"] = "comfortable"

    @field_validator("columns")
    @classmethod
    def _unique_columns(cls, v: list[TicketColumn]) -> list[TicketColumn]:
        if len(set(v)) != len(v):
            raise ValueError("Jede Spalte darf nur einmal vorkommen.")
        return v


class CounterSettings(_Strict):
    source: TicketSource = Field(default_factory=PresetSource)
    tone: Literal["neutral", "danger", "ok"] = "neutral"


class QueueOverviewSettings(_Strict):
    limit: int = Field(8, ge=1, le=12)
    only_with_work: bool = True


class EscalationsSettings(_Strict):
    limit: int = Field(8, ge=1, le=20)
    within_hours: int = Field(8, ge=0, le=72)


class RemindersSettings(_Strict):
    window_hours: int = Field(24, ge=1, le=168)
    limit: int = Field(6, ge=1, le=20)


class RecentViewsSettings(_Strict):
    limit: int = Field(8, ge=1, le=12)


class CalendarAgendaSettings(_Strict):
    days: int = Field(1, ge=1, le=14)
    limit: int = Field(6, ge=1, le=20)


class TeamPresenceSettings(_Strict):
    limit: int = Field(8, ge=1, le=30)


class NotesSettings(_Strict):
    markdown: str = Field("", max_length=5000)


class LinkItem(_Strict):
    label: str = Field(min_length=1, max_length=80)
    url: Annotated[str, Field(max_length=500)]

    @field_validator("url")
    @classmethod
    def _safe_url(cls, v: str) -> str:
        try:
            return safe_link_url(v)
        except SettingsError as exc:
            raise ValueError(exc.message) from None


class LinksSettings(_Strict):
    items: list[LinkItem] = Field(default_factory=list, max_length=12)


CORE_WIDGET_SETTINGS: Final[dict[str, type[BaseModel]]] = {
    "core.ticket-list": TicketListSettings,
    "core.counter": CounterSettings,
    "core.queue-overview": QueueOverviewSettings,
    "core.escalations": EscalationsSettings,
    "core.reminders": RemindersSettings,
    "core.recent-views": RecentViewsSettings,
    "core.calendar-agenda": CalendarAgendaSettings,
    "core.team-presence": TeamPresenceSettings,
    "core.notes": NotesSettings,
    "core.links": LinksSettings,
}


def _bounds(model: type[BaseModel], loc: tuple[int | str, ...]) -> tuple[Any, Any] | None:
    """``(ge, le)`` of a top-level numeric field, if declared."""
    if len(loc) != 1 or not isinstance(loc[0], str) or loc[0] not in model.model_fields:
        return None
    ge = le = None
    for meta in model.model_fields[loc[0]].metadata:
        ge = getattr(meta, "ge", ge)
        le = getattr(meta, "le", le)
    return (ge, le) if ge is not None and le is not None else None


def _message_de(model: type[BaseModel], err: Any) -> str:
    kind = err["type"]
    if kind in ("greater_than_equal", "less_than_equal", "int_parsing", "int_type"):
        b = _bounds(model, tuple(err["loc"]))
        if b:
            return f"Der Wert muss zwischen {b[0]} und {b[1]} liegen."
        return "Der Wert liegt außerhalb des erlaubten Bereichs."
    if kind == "extra_forbidden":
        return "Dieses Feld ist nicht erlaubt."
    if kind == "missing":
        return "Dieses Feld ist erforderlich."
    if kind in ("too_short", "too_long", "string_too_long", "string_too_short"):
        return "Die Länge liegt außerhalb des erlaubten Bereichs."
    if kind == "value_error":
        ctx = err.get("ctx") or {}
        return str(ctx.get("error") or "Ungültiger Wert.")
    return "Ungültiger Wert."


def validate_widget_settings(type_key: str, settings: Mapping[str, Any]) -> dict[str, Any]:
    """Validate ``settings`` for ``type_key``.

    Known core types: validated, defaults applied, dumped as JSON-ready dict.
    Unknown (plugin) types: returned unchanged, only the size is bounded.
    """
    size = len(json.dumps(settings, ensure_ascii=False, default=str).encode("utf-8"))
    if size > MAX_WIDGET_SETTINGS_BYTES:
        raise SettingsError("Die Einstellungen sind zu groß.", path="settings")
    model = CORE_WIDGET_SETTINGS.get(type_key)
    if model is None:
        return dict(settings)
    try:
        return model.model_validate(settings).model_dump(mode="json")
    except ValidationError as exc:
        first = exc.errors()[0]
        parts = [str(p) for p in first["loc"]]
        if len(parts) > 1 and parts[0] == "source" and parts[1] in _SOURCE_TAGS:
            del parts[1]  # the union tag is no field of the settings
        loc = ".".join(parts)
        path = f"settings.{loc}" if loc else "settings"
        raise SettingsError(_message_de(model, first), path=path) from None
