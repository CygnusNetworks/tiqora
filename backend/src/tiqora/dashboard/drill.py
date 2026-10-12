"""Drill-down: the ticket list behind a widget or a chart point (E5).

Principle: the list is computed by the same code path as the widget, so a number and its list
agree by construction. The browser never sends a filter; it names the widget and, for a chart,
one dimension value. The server rebuilds everything from the stored settings, the viewer's
scope and the dashboard filters, and the viewer's queue scope is the first clause of every
statement (counters, lists and reminders through ``ticket_query_scope`` / ``TicketService``, the
report path through ``compile_ticket_filter``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final, Protocol

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings, get_settings
from tiqora.dashboard.filters import DashboardFilters, effective_filters
from tiqora.dashboard.providers.presets import preset_filter, preset_kwargs
from tiqora.dashboard.providers.reminders import reminders_stmt
from tiqora.dashboard.providers.report import (
    REPORT_DIMENSION_TO_TICKET_FIELD,
    REPORT_WIDGET_TYPES,
    DrillNotPossible,
    check_drillable,
    map_report_filter,
    map_report_node,
    report_spec,
)
from tiqora.dashboard.providers.sources import compile_for_viewer, resolve_source
from tiqora.dashboard.schemas import DrillPageOut
from tiqora.dashboard.settings_models import (
    CORE_WIDGET_SETTINGS,
    EscalationsSettings,
    PresetSource,
    RemindersSettings,
)
from tiqora.db.legacy.queue import Queue, Service, Sla
from tiqora.db.legacy.ticket import TicketPriority, TicketState, TicketType
from tiqora.domain.ticket_service import TicketService
from tiqora.extensions.core import get_registry
from tiqora.filters import AllOf, Condition, Filter
from tiqora.filters.model import FilterNode
from tiqora.filters.ticket_params import TicketListParams, filter_from_list_params
from tiqora.filters.tickets import compile_ticket_filter, ticket_query_scope
from tiqora.jobs.service import utc_now
from tiqora.reporting.access import require_report_access
from tiqora.reporting.compiler import (
    DEFAULT_TIME_RANGE,
    RunParams,
    _apply_choices_and_grain,
    _apply_parameters,
)
from tiqora.reporting.definition import (
    AbsoluteTimeRange,
    ReportDefinition,
    TimeWindow,
    resolve_time_window,
    shift_unit,
    unit_start,
)
from tiqora.reporting.run_service import (
    RunOverrides,
    parse_dimension_choices,
    prepare_ref,
)
from tiqora.reporting.semantic import REPORT_SOURCES, Dimension
from tiqora.reporting.settings import load_reporting_settings
from tiqora.reporting.sources._common import OPEN_TYPES

__all__ = [
    "MAX_POINT_BYTES",
    "MSG_BAD_POINT",
    "MAX_POINT_KEYS",
    "MSG_INVALID_SETTINGS",
    "MSG_NO_LIST",
    "DrillNotPossible",
    "point_filter",
    "widget_tickets",
]

MSG_NO_LIST: Final = "Für dieses Widget gibt es keine Ticketliste."
MSG_INVALID_SETTINGS: Final = "Die Einstellungen dieses Widgets sind ungültig."
MSG_BAD_POINT: Final = "Der gewählte Wert ist ungültig."
NOTE_FACTS_LAG: Final = "Der Bericht kann einige Minuten älter sein als diese Liste."
MAX_POINT_KEYS: Final = 3
MAX_POINT_BYTES: Final = 500

PRESET_TITLES: Final[Mapping[str, str]] = {
    "mine_open": "Meine offenen Tickets",
    "mine_new": "Meine neuen Tickets",
    "unowned_new": "Neue Tickets ohne Besitzer",
    "escalated": "Eskalierte Tickets",
    "ai_escalated": "Von der KI übergebene Tickets",
}
DIMENSION_LABELS: Final[Mapping[str, str]] = {
    "queue": "Queue",
    "state": "Status",
    "state_type": "Statustyp",
    "priority": "Priorität",
    "type": "Typ",
    "service": "Service",
    "sla": "SLA",
    "channel": "Kanal",
    "created": "Erstellt",
}
#: dimension -> (ORM model with ``id`` / ``name``) for the value label of the page title
_LABEL_MODELS: Final[Mapping[str, Any]] = {
    "queue": Queue,
    "state": TicketState,
    "priority": TicketPriority,
    "type": TicketType,
    "service": Service,
    "sla": Sla,
}
DRILL_WIDGET_TYPES: Final = frozenset(
    {"core.counter", "core.ticket-list", "core.escalations", "core.reminders"} | REPORT_WIDGET_TYPES
)


class DrillWidget(Protocol):
    """What the drill needs of a stored widget (``WidgetOut`` fits)."""

    @property
    def id(self) -> str: ...
    @property
    def type_key(self) -> str: ...
    @property
    def settings(self) -> Mapping[str, Any]: ...
    @property
    def pinned(self) -> bool: ...
    @property
    def title_override(self) -> str | None: ...


def with_queue_filter(flt: Filter, queue_ids: Sequence[int] | None) -> Filter:
    """``flt`` ANDed with the dashboard's queue filter (the exact filter to save)."""
    if not queue_ids:
        return flt
    cond = Condition(field="queue_id", op="in", value=list(queue_ids))
    if flt.where is None:
        return Filter(where=cond)
    return Filter(where=AllOf(all=[flt.where, cond]))


def _dump(flt: Filter) -> dict[str, Any]:
    return flt.model_dump(by_alias=True, exclude_none=True)


# --------------------------------------------------------------------------
# report points
# --------------------------------------------------------------------------


def _between(window: TimeWindow) -> Condition:
    return Condition(
        field="created",
        op="between",
        value={"from": window.start_utc.isoformat(), "to": window.end_utc.isoformat()},
    )


def _bucket_window(value: object, grain: str, zone: str, now: datetime) -> TimeWindow:
    """The local-time bucket ``value`` (an ISO date = the bucket's first day) as UTC bounds."""
    if not isinstance(value, str):
        raise DrillNotPossible(MSG_BAD_POINT)
    try:
        start = date.fromisoformat(value)
    except ValueError:
        raise DrillNotPossible(MSG_BAD_POINT) from None
    if unit_start(grain, start) != start:  # not the first day of a bucket of this grain
        raise DrillNotPossible(MSG_BAD_POINT)
    end = shift_unit(grain, start, 1) - timedelta(days=1)
    return resolve_time_window(AbsoluteTimeRange(start=start, end=end), zone, now)


def _point_condition(dim: Dimension, grain: str, value: object, zone: str, now: datetime) -> Any:
    field_key = REPORT_DIMENSION_TO_TICKET_FIELD[dim.key]
    if dim.kind == "date":
        return _between(_bucket_window(value, grain, zone, now))
    if value is None:
        return Condition(field=field_key, op="is_empty")
    if dim.kind == "id":
        if isinstance(value, bool) or not isinstance(value, int):
            raise DrillNotPossible(MSG_BAD_POINT)
    elif not isinstance(value, str) or (dim.enum_values and value not in dim.enum_values):
        raise DrillNotPossible(MSG_BAD_POINT)  # also the folded "__other__" bucket
    return Condition(field=field_key, op="eq", value=value)


def point_filter(
    definition: ReportDefinition,
    params: RunParams,
    point: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> Filter:
    """The ticket filter behind a report's chart point, rebuilt from the report's own parts.

    ``definition`` / ``params`` are the prepared run (``run_service.prepare_ref``): the filter
    ANDs the report window, the report's filter, its applied parameters and the narrowing
    filter (dashboard queues) mapped onto ticket fields, then one condition per ``point`` key
    (``None`` point = the whole report population). Raises :class:`DrillNotPossible` when any
    part has no ticket-field equivalent (other source, person dimension, ...) or the point does
    not name dimensions of this report.
    """
    when = now if now is not None else utc_now()
    src = get_registry().get(REPORT_SOURCES, definition.source)
    if src is None:
        raise DrillNotPossible
    effective = _apply_choices_and_grain(definition, src, params)
    effective, param_conds = _apply_parameters(effective, src, params)
    keys = [d.key for d in effective.dimensions]
    check_drillable(effective, keys)
    nodes: list[FilterNode] = []
    time_range = params.time_range or effective.time_range
    time_key = effective.time_field or src.default_time_field
    if time_key is None and time_range is not None and src.time_fields:
        time_key = src.time_fields[0]
    if time_key is not None and (time_range is not None or src.default_time_field):
        nodes.append(
            _between(resolve_time_window(time_range or DEFAULT_TIME_RANGE, params.time_zone, when))
        )
    if src.key == "open_tickets":
        nodes.append(Condition(field="state_type", op="in", value=list(OPEN_TYPES)))
    for flt in (effective.filter, params.extra_filter):
        mapped = map_report_filter(flt)
        if mapped is not None and mapped.where is not None:
            nodes.append(mapped.where)
    nodes.extend(map_report_node(cond) for _key, cond in param_conds)
    chosen = dict(point or {})
    if len(chosen) > MAX_POINT_KEYS:
        raise DrillNotPossible(MSG_BAD_POINT)
    for key, value in chosen.items():
        if key not in keys:
            raise DrillNotPossible(MSG_BAD_POINT)
        dim = src.dimension(key)
        assert dim is not None  # validated definition
        grain = effective.dimensions[keys.index(key)].grain or "day"
        nodes.append(_point_condition(dim, grain, value, params.time_zone, when))
    if not nodes:
        return Filter()
    return Filter(where=nodes[0] if len(nodes) == 1 else AllOf(all=nodes))


async def _value_label(session: AsyncSession, key: str, value: object) -> str:
    model = _LABEL_MODELS.get(key)
    if model is not None and isinstance(value, int) and not isinstance(value, bool):
        name = (await session.execute(select(model.name).where(model.id == value))).scalar()
        if name:
            return str(name)
    return "(leer)" if value is None else str(value)


# --------------------------------------------------------------------------
# widget lists
# --------------------------------------------------------------------------


async def _report_page(
    session: AsyncSession,
    viewer: int,
    widget: DrillWidget,
    settings: BaseModel,
    eff: DashboardFilters,
    *,
    offset: int,
    limit: int,
    sort: str | None,
    order: str | None,
    point: Mapping[str, Any] | None,
    app_settings: Settings | None,
) -> DrillPageOut:
    spec = report_spec(
        widget.type_key,
        settings,
        {"queue_ids": list(eff.queue_ids), "time_range": eff.time_range},
    )
    reporting = await load_reporting_settings(session, app_settings or get_settings())
    await require_report_access(session, viewer, reporting)
    prepared = await prepare_ref(
        session,
        viewer,
        report_key=spec.report_key,
        definition=spec.definition,
        overrides=RunOverrides(
            time_range=spec.time_range,
            parameters=dict(spec.parameters),
            dimension_choices=parse_dimension_choices(spec.dimension_choices),
            common={"queue": list(eff.queue_ids)} if eff.queue_ids else {},
        ),
    )
    flt = point_filter(prepared.definition, prepared.params, point)
    scope = await ticket_query_scope(session, viewer, include_archived=False)
    stmt = await compile_ticket_filter(session, flt, scope)
    page = await TicketService(session).page_from_stmt(
        viewer, stmt, offset=offset, limit=limit, sort=sort or "age", order=order or "desc"
    )
    name = widget.title_override or (prepared.saved.name if prepared.saved else None)
    title = f"Bericht „{name}“" if name else "Tickets zum Bericht"
    for key, value in (point or {}).items():
        title += f" · {DIMENSION_LABELS[key]} {await _value_label(session, key, value)}"
    return DrillPageOut(
        title=title,
        items=page.items,
        total=page.total,
        filter=_dump(flt),
        note=NOTE_FACTS_LAG if prepared.definition.source == "tickets" else None,
    )


async def widget_tickets(
    session: AsyncSession,
    viewer: int,
    widget: DrillWidget,
    filters: DashboardFilters,
    *,
    offset: int = 0,
    limit: int = 50,
    sort: str | None = None,
    order: str | None = None,
    point: Mapping[str, Any] | None = None,
    app_settings: Settings | None = None,
) -> DrillPageOut:
    """The page of tickets behind ``widget`` for ``viewer`` (E5).

    ``filters`` are the dashboard's; a pinned widget ignores them. Raises
    :class:`DrillNotPossible` (422), ``ProviderForbidden`` (a saved search the viewer lost),
    ``RequestError`` / ``DefinitionError`` / ``FilterError`` (report path) and the 403
    ``HTTPException`` of missing report access.
    """
    if widget.type_key not in DRILL_WIDGET_TYPES:
        raise DrillNotPossible(MSG_NO_LIST)
    if point is not None and widget.type_key not in REPORT_WIDGET_TYPES:
        raise DrillNotPossible(MSG_BAD_POINT)
    try:
        settings = CORE_WIDGET_SETTINGS[widget.type_key].model_validate(dict(widget.settings))
    except ValidationError:
        raise DrillNotPossible(MSG_INVALID_SETTINGS) from None
    eff = effective_filters(filters, pinned=widget.pinned)
    queue_ids = list(eff.queue_ids) or None
    service = TicketService(session)
    custom = widget.title_override

    if widget.type_key in REPORT_WIDGET_TYPES:
        return await _report_page(
            session,
            viewer,
            widget,
            settings,
            eff,
            offset=offset,
            limit=limit,
            sort=sort,
            order=order,
            point=point,
            app_settings=app_settings,
        )

    if widget.type_key == "core.escalations":
        assert isinstance(settings, EscalationsSettings)
        within = settings.within_hours * 3600
        page = await service.list_tickets(
            viewer,
            state_type="open",
            escalating_within=within,
            offset=offset,
            limit=limit,
            sort=sort or "deadline",
            order=order or "asc",
            queue_ids=queue_ids,
        )
        flt = filter_from_list_params(TicketListParams(state_type="open", escalating_within=within))
        return DrillPageOut(
            title=custom or "Eskalierende Tickets",
            items=page.items,
            total=page.total,
            filter=_dump(with_queue_filter(flt, queue_ids)),
        )

    if widget.type_key == "core.reminders":
        assert isinstance(settings, RemindersSettings)
        now_epoch = int(utc_now().replace(tzinfo=UTC).timestamp())
        stmt = await reminders_stmt(
            session,
            viewer,
            now_epoch=now_epoch,
            window_hours=settings.window_hours,
            queue_ids=queue_ids,
        )
        page = await service.page_from_stmt(
            viewer, stmt, offset=offset, limit=limit, sort=sort or "until", order=order or "asc"
        )
        return DrillPageOut(title=custom or "Erinnerungen", items=page.items, total=page.total)

    # core.counter / core.ticket-list: the provider's source as a page
    list_sort: str = sort or str(getattr(settings, "sort", "age"))
    list_order: str = order or str(getattr(settings, "order", "desc"))
    source = settings.source  # type: ignore[attr-defined]
    if isinstance(source, PresetSource):
        page = await service.list_tickets(
            viewer,
            offset=offset,
            limit=limit,
            sort=list_sort,
            order=list_order,
            queue_ids=queue_ids,
            **preset_kwargs(source.preset, viewer),
        )
        preset = preset_filter(source.preset, viewer)
        return DrillPageOut(
            title=custom or PRESET_TITLES[source.preset],
            items=page.items,
            total=page.total,
            filter=None if preset is None else _dump(with_queue_filter(preset, queue_ids)),
        )
    resolved = await resolve_source(session, viewer, source, queue_ids)
    stmt = await compile_for_viewer(session, resolved)
    page = await service.page_from_stmt(
        viewer, stmt, offset=offset, limit=limit, sort=list_sort, order=list_order
    )
    return DrillPageOut(
        title=custom or resolved.name or "Gefilterte Tickets",
        items=page.items,
        total=page.total,
        filter=_dump(with_queue_filter(resolved.criteria, queue_ids)),
    )
