"""Plugin manifests: ``tiqora-plugin.toml`` and ``web/manifest.json``.

Pure parsing and validation; nothing in here imports plugin code. Error messages
are German because admins see them on the extensions page.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections import Counter
from typing import Annotated, Any, Final, Literal

import structlog
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

log = structlog.get_logger(__name__)

PLUGIN_ID_RE: Final = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
SHORT_ID_RE: Final = re.compile(r"^[a-z][a-z0-9]{1,15}$")
FRONTEND_SLOTS: Final = ("agent.nav", "agent.route", "ticket.sidebar", "dashboard.widget")

# Short ids that could collide with (future) core MCP tool prefixes (R-SHORTID-RESERVED).
RESERVED_SHORT_IDS: Final = frozenset(
    {"ticket", "list", "kb", "customer", "get", "core", "tiqora", "plugin", "mcp"}
)

_ENTRY_RE: Final = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")
_HOST_RE: Final = re.compile(r"^(\*\.)?[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")
_ROUTE_PATH_RE: Final = re.compile(r"^[a-z0-9-]+(/[a-z0-9-]+)*$")
_WEB_FILE_RE: Final = re.compile(r"^[A-Za-z0-9_./-]+$")


class ManifestError(ValueError):
    """A manifest is unusable; the message is German and shown to admins."""


def _specifier(value: str, field: str) -> str:
    try:
        SpecifierSet(value)
    except InvalidSpecifier as exc:
        raise ValueError(f"{field}: ungültige Versionsangabe „{value}“") from exc
    return value


def _web_file(value: str, suffix: str) -> str:
    ok = (
        _WEB_FILE_RE.fullmatch(value) is not None
        and value.endswith(suffix)
        and not value.startswith("/")
        and ".." not in value.split("/")
    )
    if not ok:
        raise ValueError(f"ungültiger Dateipfad „{value}“ (relativ zu web/, Endung {suffix})")
    return value


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extension_points: list[str] = []
    outbound_hosts: list[str] = []  # "host" or "*.suffix"; lower-cased; no scheme/port
    frontend_slots: list[str] = []  # unknown values kept (forward compat) but never rendered

    @field_validator("outbound_hosts")
    @classmethod
    def _hosts(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for host in value:
            low = host.strip().lower()
            if not _HOST_RE.fullmatch(low):
                raise ValueError(f"ungültiger Hostname „{host}“ (ohne Schema und Port)")
            out.append(low)
        return out


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version: Literal[1]
    id: str
    short_id: str
    name: str
    version: str
    publisher: str
    publisher_id: str | None = None
    description: str = ""
    license: str | None = None
    homepage: str | None = None
    support: str | None = None
    categories: list[str] = []
    entry: str
    sdk: str
    tiqora: str | None = None
    requires: dict[str, str] = {}
    capabilities: Capabilities = Capabilities()

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if len(value) > 100 or not PLUGIN_ID_RE.fullmatch(value):
            raise ValueError(
                "Kennung muss aus kleingeschriebenen Teilen mit Punkten bestehen "
                "(z. B. org.example.hello, höchstens 100 Zeichen)"
            )
        return value

    @field_validator("publisher_id")
    @classmethod
    def _publisher_id(cls, value: str | None) -> str | None:
        if value is not None and (len(value) > 100 or not PLUGIN_ID_RE.fullmatch(value)):
            raise ValueError("Herausgeber-Kennung muss die Form org.example haben")
        return value

    @field_validator("short_id")
    @classmethod
    def _short_id(cls, value: str) -> str:
        if not SHORT_ID_RE.fullmatch(value):
            raise ValueError(
                "Kurzname: 2 bis 16 Zeichen, a-z und 0-9, beginnt mit einem Buchstaben"
            )
        if value in RESERVED_SHORT_IDS:
            raise ValueError(f"Kurzname „{value}“ ist für Tiqora reserviert")
        return value

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not 1 <= len(value) <= 80:
            raise ValueError("Name muss 1 bis 80 Zeichen lang sein")
        return value

    @field_validator("version")
    @classmethod
    def _version(cls, value: str) -> str:
        try:
            Version(value)
        except InvalidVersion as exc:
            raise ValueError(f"ungültige Version „{value}“") from exc
        return value

    @field_validator("entry")
    @classmethod
    def _entry(cls, value: str) -> str:
        if not _ENTRY_RE.fullmatch(value):
            raise ValueError("entry muss die Form modul:attribut haben")
        return value

    @field_validator("sdk")
    @classmethod
    def _sdk(cls, value: str) -> str:
        return _specifier(value, "sdk")

    @field_validator("tiqora")
    @classmethod
    def _tiqora(cls, value: str | None) -> str | None:
        return None if value is None else _specifier(value, "tiqora")

    @field_validator("requires")
    @classmethod
    def _requires(cls, value: dict[str, str]) -> dict[str, str]:
        for plugin_id, spec in value.items():
            if not PLUGIN_ID_RE.fullmatch(plugin_id):
                raise ValueError(f"ungültige Kennung „{plugin_id}“ in requires")
            _specifier(spec, f"requires.{plugin_id}")
        return value

    @field_validator("categories")
    @classmethod
    def _categories(cls, value: list[str]) -> list[str]:
        if any(not 1 <= len(c) <= 40 for c in value):
            raise ValueError("Kategorien dürfen höchstens 40 Zeichen lang sein")
        return value


def _format_errors(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        msg = str(err["msg"]).removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)


def parse_manifest(text: str) -> PluginManifest:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"tiqora-plugin.toml: ungültige TOML-Syntax ({exc})") from exc
    try:
        return PluginManifest.model_validate(data)
    except ValidationError as exc:
        raise ManifestError(f"tiqora-plugin.toml: {_format_errors(exc)}") from exc


# --- web/manifest.json -------------------------------------------------------------------


class NavContribution(BaseModel):
    slot: Literal["agent.nav"]
    key: str
    route: str  # key of an agent.route contribution of the same plugin
    label_key: str
    icon: str = "puzzle"
    group: Literal["workspace", "knowledge", "insights", "plugins"] = "plugins"
    order: int = 100
    admin_only: bool = False


class RouteContribution(BaseModel):
    slot: Literal["agent.route"]
    key: str
    path: str  # -> /agent/plugins/<id>/<path>
    component: str
    title_key: str
    admin_only: bool = False

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        if not _ROUTE_PATH_RE.fullmatch(value):
            raise ValueError(f"ungültiger Pfad „{value}“")
        return value


class TicketSidebarContribution(BaseModel):
    slot: Literal["ticket.sidebar"]
    key: str
    component: str
    title_key: str
    order: int = 100


class WidgetSize(BaseModel):
    default: tuple[int, int] = (4, 3)
    min: tuple[int, int] = (2, 2)
    max: tuple[int, int] = (12, 8)


class DashboardWidgetContribution(BaseModel):
    slot: Literal["dashboard.widget"]
    key: str
    component: str
    version: int = 1
    title_key: str
    description_key: str | None = None
    category: Literal["tickets", "reports", "team", "ai", "itsm", "other"] = "other"
    icon: str = "puzzle"
    size: WidgetSize = WidgetSize()
    settings_schema: dict[str, Any] | None = None
    refresh_on: list[str] = []
    permission: str | None = None


FrontendContribution = Annotated[
    NavContribution | RouteContribution | TicketSidebarContribution | DashboardWidgetContribution,
    Field(discriminator="slot"),
]


class WebManifest(BaseModel):
    manifest_version: Literal[1]
    entry: str = "index.js"  # relative to web/
    styles: list[str] = []
    contributions: list[FrontendContribution] = []

    @field_validator("entry")
    @classmethod
    def _entry(cls, value: str) -> str:
        return _web_file(value, ".js")

    @field_validator("styles")
    @classmethod
    def _styles(cls, value: list[str]) -> list[str]:
        return [_web_file(v, ".css") for v in value]


def parse_web_manifest(raw: bytes, capabilities: Capabilities) -> WebManifest:
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ManifestError(f"web/manifest.json: ungültiges JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ManifestError("web/manifest.json: Objekt erwartet")

    items = data.get("contributions", [])
    if isinstance(items, list):
        kept: list[Any] = []
        for item in items:
            slot = item.get("slot") if isinstance(item, dict) else None
            if slot not in FRONTEND_SLOTS:
                log.warning("plugin_unknown_slot_dropped", slot=slot)
                continue
            kept.append(item)
        data = {**data, "contributions": kept}
    try:
        web = WebManifest.model_validate(data)
    except ValidationError as exc:
        raise ManifestError(f"web/manifest.json: {_format_errors(exc)}") from exc

    declared = set(capabilities.frontend_slots)
    for c in web.contributions:
        if c.slot not in declared:
            raise ManifestError(
                f"web/manifest.json: Erweiterungspunkt „{c.slot}“ ist in tiqora-plugin.toml "
                "nicht unter frontend_slots angegeben"
            )
    dupes = [k for k, n in Counter((c.slot, c.key) for c in web.contributions).items() if n > 1]
    if dupes:
        slot, key = dupes[0]
        raise ManifestError(
            f"web/manifest.json: Schlüssel „{key}“ in „{slot}“ ist doppelt vergeben"
        )
    routes = [c for c in web.contributions if isinstance(c, RouteContribution)]
    paths = Counter(r.path for r in routes)
    for path, n in paths.items():
        if n > 1:
            raise ManifestError(f"web/manifest.json: Pfad „{path}“ ist doppelt vergeben")
    route_keys = {r.key for r in routes}
    for c in web.contributions:
        if isinstance(c, NavContribution) and c.route not in route_keys:
            raise ManifestError(
                f"web/manifest.json: Navigationseintrag „{c.key}“ verweist auf die unbekannte "
                f"Seite „{c.route}“"
            )
    return web
