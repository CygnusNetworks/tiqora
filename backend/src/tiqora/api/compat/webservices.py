"""GenericInterface webservice model, route matching and registry (``gi_webservice_config``).

Ports the parts of ``Kernel::System::GenericInterface::Webservice`` (``WebserviceConfigReplace``)
and ``Transport::HTTP::REST`` (route table) that decide *which* operation a request reaches.
Mappers are built once per registry snapshot; one broken webservice never takes the others down.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

import regex
import structlog
import yaml
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.compat.mapping import DataInclude, Mapper, MappingError, build_mapper
from tiqora.api.compat.mapping.perl import (
    REGEX_TIMEOUT_SECONDS,
    compile_perl_regex,
    perl_str,
    perl_truthy,
)
from tiqora.api.compat.soap import DEFAULT_NAMESPACE
from tiqora.db.legacy.config import GiWebserviceConfig
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

OPERATION_TYPES: dict[str, str] = {  # Znuny operation Type -> op handler key
    "Session::SessionCreate": "SessionCreate",
    "Session::SessionGet": "SessionGet",
    "Session::SessionRemove": "SessionRemove",
    "Ticket::TicketCreate": "TicketCreate",
    "Ticket::TicketUpdate": "TicketUpdate",
    "Ticket::TicketGet": "TicketGet",
    "Ticket::TicketSearch": "TicketSearch",
    "Ticket::TicketHistoryGet": "TicketHistoryGet",
    "Ticket::TimeAccountingGet": "TimeAccountingGet",
    "User::OutOfOffice": "OutOfOffice",
}

_ROUTE_VAR = re.compile(r":([^/]+)")
_PLACEHOLDER = re.compile(r"(?:<|&lt;)OTRS_CONFIG_(.+?)(?:>|&gt;)")
_INTEGER = re.compile(r"[+-]?\d+")


@dataclass(frozen=True, slots=True)
class RouteMapping:
    operation: str
    route: str  # as configured, e.g. "/Ticket/:TicketID"
    methods: tuple[str, ...]  # () = any method (Znuny: no RequestMethod list -> no filter)
    pattern: regex.Pattern[str] | None  # None when the route regex does not compile


@dataclass(frozen=True, slots=True)
class OperationConfig:
    name: str
    type: str
    handler: str | None  # OPERATION_TYPES.get(type)
    mapping_inbound: Mapper | None
    mapping_outbound: Mapper | None
    mapping_error: str | None  # MappingConfigError text when a mapper could not be built
    include_ticket_data: bool
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Webservice:
    id: int
    name: str
    transport: str  # "HTTP::REST" | "HTTP::SOAP" | other
    namespace: str
    max_length: int | None
    routes: tuple[RouteMapping, ...]  # sorted by operation name (Znuny iterates sorted keys)
    operations: dict[str, OperationConfig]
    config: dict[str, Any]  # full parsed YAML after placeholder replacement


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _build_route_pattern(operation: str, route: str) -> regex.Pattern[str] | None:
    """Znuny: ``s{:([^/]+)}{(?<$1>[^/]+)}g``, matched as ``m{^ $RouteRegEx $}xms``."""
    if not route:
        return None
    source = _ROUTE_VAR.sub(lambda m: f"(?<{m.group(1)}>[^/]+)", route)
    try:
        return compile_perl_regex(source, verbose=True)
    except MappingError as exc:
        logger.warning("compat_route_invalid", operation=operation, route=route, error=str(exc))
        return None


def _parse_routes(transport_config: dict[str, Any]) -> tuple[RouteMapping, ...]:
    mapping = transport_config.get("RouteOperationMapping")
    if not isinstance(mapping, dict):
        return ()
    routes: list[RouteMapping] = []
    for key in sorted(mapping, key=str):
        entry = mapping[key]
        if not isinstance(entry, dict) or not entry:
            continue
        operation = str(key)
        route = entry.get("Route")
        route = perl_str(route) if route is not None else ""
        methods = entry.get("RequestMethod")
        # Znuny filters by method only for a non-empty LIST; a bare string means "any method".
        method_list = (
            tuple(str(m) for m in methods) if isinstance(methods, list) and methods else ()
        )
        routes.append(
            RouteMapping(operation, route, method_list, _build_route_pattern(operation, route))
        )
    return tuple(routes)


def _build_mapper(config: Any, direction: str, huge_xml: bool, errors: list[str]) -> Mapper | None:
    # Provider.pm only creates a mapper for a non-empty hash ("MappingInbound: {}" = no mapping).
    if not isinstance(config, dict) or not config:
        return None
    try:
        return build_mapper(config, huge_xml=huge_xml)
    except MappingError as exc:
        errors.append(f"{direction}: {exc}")
        return None


def _parse_operation(name: str, config: dict[str, Any], huge_xml: bool) -> OperationConfig:
    errors: list[str] = []
    op_type = config.get("Type")
    op_type = perl_str(op_type) if op_type is not None else ""
    inbound = _build_mapper(config.get("MappingInbound"), "MappingInbound", huge_xml, errors)
    outbound = _build_mapper(config.get("MappingOutbound"), "MappingOutbound", huge_xml, errors)
    return OperationConfig(
        name=name,
        type=op_type,
        handler=OPERATION_TYPES.get(op_type),
        mapping_inbound=inbound,
        mapping_outbound=outbound,
        mapping_error="; ".join(errors) if errors else None,
        include_ticket_data=perl_truthy(config.get("IncludeTicketData")),
        raw=config,
    )


def parse_webservice(
    ws_id: int, name: str, config: dict[str, Any], *, huge_xml: bool
) -> Webservice:
    provider = _dict(config.get("Provider"))
    transport = _dict(provider.get("Transport"))
    transport_config = _dict(transport.get("Config"))
    max_length_raw = transport_config.get("MaxLength")
    max_length = (
        int(perl_str(max_length_raw))
        if _INTEGER.fullmatch(perl_str(max_length_raw or ""))
        else None
    )
    namespace = transport_config.get("NameSpace")
    operations = {
        str(op_name): _parse_operation(str(op_name), op_config, huge_xml)
        for op_name, op_config in _dict(provider.get("Operation")).items()
        if isinstance(op_config, dict)
    }
    transport_type = transport.get("Type")
    return Webservice(
        id=ws_id,
        name=name,
        transport=perl_str(transport_type) if transport_type is not None else "",
        namespace=perl_str(namespace) if namespace else DEFAULT_NAMESPACE,
        max_length=max_length,
        routes=_parse_routes(transport_config),
        operations=operations,
        config=config,
    )


def match_rest_route(ws: Webservice, path: str, method: str) -> tuple[str, dict[str, str]] | None:
    """First route (sorted by operation name) whose method list and pattern accept the request.

    *path* is the RAW request path after ``/Webservice/<name>`` (starts with ``/``, no query);
    captured variables are percent-decoded once, like ``URI::Escape::uri_unescape``.
    """
    from urllib.parse import unquote

    for route in ws.routes:
        if route.methods and method not in route.methods:
            continue
        if route.pattern is None:
            continue
        try:
            found = route.pattern.fullmatch(path, timeout=REGEX_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("compat_route_timeout", operation=route.operation, route=route.route)
            continue
        if found is None:
            continue
        variables = {
            key: unquote(value, errors="replace")
            for key, value in sorted(found.groupdict().items())
            if value is not None
        }
        return route.operation, variables
    return None


async def replace_config_placeholders(value: Any, sysconfig: SysConfig) -> Any:
    """``WebserviceConfigReplace``: ``<OTRS_CONFIG_x>`` (or ``&lt;..&gt;``) -> SysConfig x."""
    if isinstance(value, str):
        names = {m.group(1) for m in _PLACEHOLDER.finditer(value)}
        if not names:
            return value
        resolved: dict[str, str] = {}
        for config_name in names:
            raw = await sysconfig.get(config_name)
            resolved[config_name] = (
                perl_str(raw) if isinstance(raw, (str, int, float)) and raw is not None else ""
            )
        return _PLACEHOLDER.sub(lambda m: resolved[m.group(1)], value)
    if isinstance(value, list):
        return [await replace_config_placeholders(entry, sysconfig) for entry in value]
    if isinstance(value, dict):
        return {
            key: await replace_config_placeholders(entry, sysconfig) for key, entry in value.items()
        }
    return value


async def run_mapper(mapper: Mapper, data: Any, data_include: DataInclude | None = None) -> Any:
    """Run ``mapper.map`` in a worker thread so a slow mapping never blocks the event loop."""
    return await asyncio.to_thread(mapper.map, data, data_include)


Fingerprint = tuple[int, str, int, int]  # (count, max(change_time) iso, max(id), sum(valid_id))


async def load_fingerprint(session: AsyncSession) -> Fingerprint:
    row = (
        await session.execute(
            select(
                func.count(),
                func.max(GiWebserviceConfig.change_time),
                func.max(GiWebserviceConfig.id),
                func.sum(GiWebserviceConfig.valid_id),
            )
        )
    ).one()
    changed = row[1]
    return (
        int(row[0] or 0),
        changed.isoformat() if changed is not None else "",
        int(row[2] or 0),
        int(row[3] or 0),
    )


def _config_text(blob: bytes | memoryview | str) -> str:
    """``config`` is a BLOB on MariaDB and may come back as text on PostgreSQL."""
    return blob if isinstance(blob, str) else bytes(blob).decode("utf-8")


class WebserviceRegistry:
    """In-memory snapshot of all valid webservices, swapped atomically on refresh."""

    def __init__(self) -> None:
        self._snapshot: tuple[dict[str, Webservice], dict[int, Webservice]] = ({}, {})
        self._fingerprint: Fingerprint | None = None
        self._loaded = False
        self._lock = asyncio.Lock()

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def fingerprint(self) -> Fingerprint | None:
        return self._fingerprint

    def by_name(self, name: str) -> Webservice | None:
        return self._snapshot[0].get(name)

    def by_id(self, ws_id: int) -> Webservice | None:
        return self._snapshot[1].get(ws_id)

    async def refresh(self, session: AsyncSession, *, force: bool = False) -> bool:
        async with self._lock:
            fingerprint = await load_fingerprint(session)
            if not force and self._loaded and fingerprint == self._fingerprint:
                return False
            sysconfig = SysConfig(session)
            huge_xml = perl_truthy(await sysconfig.get("LibXML::EnableHugeXMLDataSupport"))
            rows = (
                await session.execute(
                    select(
                        GiWebserviceConfig.id, GiWebserviceConfig.name, GiWebserviceConfig.config
                    )
                    .where(GiWebserviceConfig.valid_id == 1)
                    .order_by(GiWebserviceConfig.id)
                )
            ).all()
            by_name: dict[str, Webservice] = {}
            by_id: dict[int, Webservice] = {}
            for ws_id, ws_name, blob in rows:
                try:
                    parsed = yaml.safe_load(_config_text(blob))
                    if not isinstance(parsed, dict) or not isinstance(parsed.get("Provider"), dict):
                        raise ValueError("webservice config has no Provider section")
                    replaced = await replace_config_placeholders(parsed, sysconfig)
                    ws = await asyncio.to_thread(
                        parse_webservice, ws_id, ws_name, replaced, huge_xml=huge_xml
                    )
                except Exception as exc:  # one broken row must not take the others down
                    logger.warning(
                        "compat_webservice_invalid",
                        webservice_id=ws_id,
                        webservice_name=ws_name,
                        error=str(exc),
                    )
                    continue
                by_name[ws.name] = ws
                by_id[ws.id] = ws
            self._snapshot = (by_name, by_id)
            self._fingerprint = fingerprint
            self._loaded = True
            return True

    async def ensure_loaded(self, session: AsyncSession) -> None:
        if not self._loaded:
            await self.refresh(session, force=True)


def get_registry(app: Any) -> WebserviceRegistry:
    registry = getattr(app.state, "compat_webservices", None)
    if registry is None:
        registry = WebserviceRegistry()
        app.state.compat_webservices = registry
    return registry
