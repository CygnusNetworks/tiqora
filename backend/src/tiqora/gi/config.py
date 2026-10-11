"""Requester configuration model of a GenericInterface webservice (pure, no I/O).

Parses ``Requester`` and ``Debugger`` of an already YAML-decoded, placeholder-replaced webservice
config. Mirrors what ``Event/Handler.pm`` (which events/invokers fire), ``Requester.pm`` (mappings
only exist for non-empty hashes) and ``ErrorHandling.pm`` (priority list, module lookup) read.
Unknown keys are ignored here and kept in ``raw``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal

import structlog

from tiqora.api.compat.mapping import Mapper, MappingError, build_mapper
from tiqora.api.compat.mapping.perl import perl_str, perl_truthy
from tiqora.plugins.events import HISTORY_EVENT_MAP

logger = structlog.get_logger(__name__)

DebugLevel = Literal["debug", "info", "notice", "error"]
DEBUG_LEVELS: Final[dict[str, int]] = {"debug": 1, "info": 2, "notice": 3, "error": 4}

SUPPORTED_INVOKER_TYPES: Final = frozenset({"Ticket::Generic"})

_DYNAMIC_FIELD_PREFIX: Final = "TicketDynamicFieldUpdate_"
_TICKET_EVENTS: Final = frozenset(HISTORY_EVENT_MAP.values()) | {"ArticleCreate"}


@dataclass(frozen=True, slots=True)
class DebuggerConfig:
    threshold: DebugLevel
    test_mode: bool


@dataclass(frozen=True, slots=True)
class InvokerEvent:
    event: str
    asynchronous: bool
    condition: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class InvokerConfig:
    name: str
    type: str
    supported: bool
    events: tuple[InvokerEvent, ...]
    mapping_outbound: Mapper | None
    mapping_inbound: Mapper | None
    mapping_error: str | None
    get_all_article_attachments: bool
    sort_outbound: Any
    operation_name: str | None
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ErrorHandlingModule:
    key: str
    type: str
    invoker_filter: tuple[str, ...]
    error_stage_filter: tuple[str, ...]
    error_message_content_filter: str | None
    stop_after_match: str | None
    config: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RequesterTransport:
    type: str
    config: dict[str, Any]  # secrets inside - never dump unredacted


@dataclass(frozen=True, slots=True)
class RequesterConfig:
    transport: RequesterTransport | None
    invokers: dict[str, InvokerConfig]
    error_handling: tuple[ErrorHandlingModule, ...]
    debugger: DebuggerConfig
    unsupported_events: tuple[tuple[str, str], ...]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _str_or_none(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = perl_str(value)
    return text if text != "" else None


def _str_list(value: Any) -> tuple[str, ...]:
    # ErrorHandling.pm:202/211 only filters on IsArrayRefWithData; a scalar means "no filter".
    if not isinstance(value, list):
        return ()
    return tuple(perl_str(i) for i in value if i is not None and not isinstance(i, (dict, list)))


def is_ticket_event(event: str) -> bool:
    """True for every ticket/article event a GI invoker can be bound to in Tiqora."""
    if event in _TICKET_EVENTS:
        return True
    return event.startswith(_DYNAMIC_FIELD_PREFIX) and len(event) > len(_DYNAMIC_FIELD_PREFIX)


def parse_debugger(config: dict[str, Any]) -> DebuggerConfig:
    section = _dict(config.get("Debugger"))
    raw = section.get("DebugThreshold")
    threshold: DebugLevel = "error"
    if raw is not None:
        level = perl_str(raw).lower()  # Debugger.pm:90 matches case-insensitively
        if level in DEBUG_LEVELS:
            threshold = level  # type: ignore[assignment]
        else:
            logger.warning("gi_debug_threshold_invalid", value=level)
    return DebuggerConfig(threshold=threshold, test_mode=perl_truthy(section.get("TestMode")))


def _build_mapper(config: Any, direction: str, huge_xml: bool, errors: list[str]) -> Mapper | None:
    # Requester.pm only creates a mapper for a non-empty hash.
    if not isinstance(config, dict) or not config:
        return None
    try:
        return build_mapper(config, huge_xml=huge_xml)
    except MappingError as exc:
        errors.append(f"{direction}: {exc}")
        return None


def _parse_events(
    invoker: str, raw_events: Any, unsupported: list[tuple[str, str]]
) -> tuple[InvokerEvent, ...]:
    events: list[InvokerEvent] = []
    if not isinstance(raw_events, list):
        return ()
    for entry in raw_events:
        if not isinstance(entry, dict):
            continue
        name = entry.get("Event")
        if name is None or isinstance(name, (dict, list)) or perl_str(name) == "":
            continue
        event = perl_str(name)
        if not is_ticket_event(event):
            unsupported.append((invoker, event))
            continue
        condition = entry.get("Condition")
        # Handler.pm:119-120 checks only when Condition and Condition->{Condition} are non-empty
        # hashes; otherwise the event fires unconditionally.
        inner = condition.get("Condition") if isinstance(condition, dict) else None
        events.append(
            InvokerEvent(
                event=event,
                asynchronous=perl_truthy(entry.get("Asynchronous")),
                condition=condition if isinstance(inner, dict) and inner else None,
            )
        )
    return tuple(events)


def _parse_invoker(
    name: str, config: dict[str, Any], huge_xml: bool, unsupported: list[tuple[str, str]]
) -> InvokerConfig:
    errors: list[str] = []
    invoker_type = config.get("Type")
    type_text = perl_str(invoker_type) if invoker_type is not None else ""
    outbound = _build_mapper(config.get("MappingOutbound"), "MappingOutbound", huge_xml, errors)
    inbound = _build_mapper(config.get("MappingInbound"), "MappingInbound", huge_xml, errors)
    return InvokerConfig(
        name=name,
        type=type_text,
        supported=type_text in SUPPORTED_INVOKER_TYPES,
        events=_parse_events(name, config.get("Events"), unsupported),
        mapping_outbound=outbound,
        mapping_inbound=inbound,
        mapping_error="; ".join(errors) if errors else None,
        get_all_article_attachments=perl_truthy(config.get("GetAllArticleAttachments")),
        sort_outbound=config.get("SortOutbound"),
        operation_name=_str_or_none(config.get("OperationName")),
        raw=config,
    )


def _parse_error_handling(requester: dict[str, Any]) -> tuple[ErrorHandlingModule, ...]:
    modules = _dict(requester.get("ErrorHandling"))
    priority = requester.get("ErrorHandlingPriority")
    if not isinstance(priority, list):
        return ()
    out: list[ErrorHandlingModule] = []
    for key in priority:
        if key is None or isinstance(key, (dict, list)):
            continue
        module_key = perl_str(key)
        module = modules.get(module_key)
        if not isinstance(module, dict):
            continue
        out.append(
            ErrorHandlingModule(
                key=module_key,
                type=perl_str(module.get("Type")) if module.get("Type") is not None else "",
                invoker_filter=_str_list(module.get("InvokerFilter")),
                error_stage_filter=_str_list(module.get("ErrorStageFilter")),
                error_message_content_filter=_str_or_none(module.get("ErrorMessageContentFilter")),
                stop_after_match=_str_or_none(module.get("StopAfterMatch")),
                config=module,
            )
        )
    return tuple(out)


def parse_requester(config: dict[str, Any], *, huge_xml: bool) -> RequesterConfig:
    requester = _dict(config.get("Requester"))
    transport_raw = _dict(requester.get("Transport"))
    transport: RequesterTransport | None = None
    if transport_raw:
        transport_type = transport_raw.get("Type")
        transport = RequesterTransport(
            type=perl_str(transport_type) if transport_type is not None else "",
            config=_dict(transport_raw.get("Config")),
        )
    unsupported: list[tuple[str, str]] = []
    invokers = {
        str(name): _parse_invoker(str(name), inv, huge_xml, unsupported)
        for name, inv in sorted(_dict(requester.get("Invoker")).items(), key=lambda i: str(i[0]))
        if isinstance(inv, dict)
    }
    return RequesterConfig(
        transport=transport,
        invokers=invokers,
        error_handling=_parse_error_handling(requester),
        debugger=parse_debugger(config),
        unsupported_events=tuple(unsupported),
    )
