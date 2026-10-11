"""Rules shared by every Znuny statistic mapper (plan D5/D6.1-D6.4).

Pure: no database access. The result contracts (``StatMapping``, ``MappedSearch``) and the
object-mapper registry live here (ruling 24) so that ``mapper.py`` and the Task 4 mappers can
import them without a cycle.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Final, Literal

from pydantic import ValidationError

from tiqora.domain.timezones import valid_time_zone
from tiqora.filters import AllOf, Condition, Filter
from tiqora.reporting.definition import (
    AbsoluteTimeRange,
    DimensionRef,
    RelativeTimeRange,
    ReportDefinition,
    ReportParameter,
    TimeRange,
    Totals,
    Visualization,
)
from tiqora.reporting.sources._common import STATE_TYPES
from tiqora.reporting.sql import Grain
from tiqora.reporting.znuny_stats.model import StatElement, ZnunyStat
from tiqora.saved_searches.znuny_import import ZnunyLookups

IssueSeverity = Literal["dropped", "approximated"]
StatTarget = Literal["report", "saved_search", "none"]

MAX_DIMENSIONS: Final = 3
MAX_PARAMETERS: Final = 10
_SHOWN: Final = 200

USE_TAG: Final[Mapping[str, str]] = {
    "x": "UseAsXvalue",
    "series": "UseAsValueSeries",
    "restriction": "UseAsRestriction",
}


@dataclass(frozen=True, slots=True)
class ImportIssue:
    element: str  # "<UseAs...>.<Element>" for axes/restrictions, else the stat-level key
    value: str  # the offending value, shortened to 200
    reason: str  # German, Sie-form, a full sentence
    severity: IssueSeverity


@dataclass(frozen=True, slots=True)
class StatLookups:
    state_type_names: Mapping[int, str]  # ticket_state_type.id -> name
    queue_ids_by_name: Mapping[str, int]  # casefolded full queue name -> id (file import)
    state_ids_by_name: Mapping[str, int]  # casefolded state name -> id
    priority_ids_by_name: Mapping[str, int]
    user_ids_by_login: Mapping[str, int]  # casefolded login -> id
    group_ids_by_name: Mapping[str, int]  # casefolded group name -> id (file Permission)
    default_time_zone: str  # the importing agent's zone
    search: ZnunyLookups  # wave-2 lookups for map_search_profile (TicketList)


@dataclass(frozen=True, slots=True)
class FieldMap:
    field: str  # Tiqora source field key
    kind: Literal["id", "string", "state_type", "time"]


@dataclass(frozen=True, slots=True)
class MappedSearch:
    criteria: Filter
    include_archived: bool
    sort_by: str  # TicketSortKey
    sort_order: Literal["asc", "desc"]


@dataclass(frozen=True, slots=True)
class StatMapping:
    stat: ZnunyStat
    target: StatTarget
    definition: ReportDefinition | None  # target == "report"; passes validate_definition
    search: MappedSearch | None  # target == "saved_search"
    issues: tuple[ImportIssue, ...]  # ordered by (element, value)


#: Object mappers by object module name (dynamic) or ``"static:<File>"`` (static statistics).
OBJECT_MAPPERS: dict[str, Callable[[ZnunyStat, StatLookups, bool], StatMapping]] = {}


def element_ref(el: StatElement) -> str:
    return f"{USE_TAG[el.use]}.{el.element}"


def make_issue(element: str, value: object, reason: str, severity: IssueSeverity) -> ImportIssue:
    return ImportIssue(element=element, value=str(value)[:_SHOWN], reason=reason, severity=severity)


def sorted_issues(issues: Collection[ImportIssue]) -> tuple[ImportIssue, ...]:
    """Unique issues ordered by (element, value, reason)."""
    unique = dict.fromkeys(issues)
    return tuple(sorted(unique, key=lambda i: (i.element, i.value, i.reason)))


# --------------------------------------------------------------------------
# D6.1 elements -> fields of the ``tickets`` source
# --------------------------------------------------------------------------

ELEMENT_FIELDS_TICKETS: Final[Mapping[str, FieldMap]] = {
    "QueueIDs": FieldMap("queue", "id"),
    "CreatedQueueIDs": FieldMap("queue_at_create", "id"),
    "StateIDs": FieldMap("state", "id"),
    "StateTypeIDs": FieldMap("state_type", "state_type"),
    "PriorityIDs": FieldMap("priority", "id"),
    "TypeIDs": FieldMap("type", "id"),
    "ServiceIDs": FieldMap("service", "id"),
    "SLAIDs": FieldMap("sla", "id"),
    "OwnerIDs": FieldMap("owner", "id"),
    "ResponsibleIDs": FieldMap("responsible", "id"),
    "CustomerID": FieldMap("customer", "string"),
    "CustomerIDRaw": FieldMap("customer", "string"),
    "CustomerUserLogin": FieldMap("customer_user", "string"),
    "CustomerUserLoginRaw": FieldMap("customer_user", "string"),
    "CustomerUserID": FieldMap("customer_user", "string"),
    "CreateTime": FieldMap("created", "time"),
    "CloseTime": FieldMap("closed", "time"),
    "CloseTime2": FieldMap("closed", "time"),  # ruling 5: solution-time stats' period
}

#: Elements Znuny's ``Dynamic::Ticket::ExportWrapper`` converts to names in an export file.
FILE_NAME_ELEMENTS: Final[Mapping[str, Literal["queue", "state", "priority", "user"]]] = {
    "QueueIDs": "queue",
    "CreatedQueueIDs": "queue",
    "StateIDs": "state",
    "CreatedStateIDs": "state",
    "PriorityIDs": "priority",
    "CreatedPriorityIDs": "priority",
    "OwnerIDs": "user",
    "CreatedUserIDs": "user",
    "ResponsibleIDs": "user",
}

_NO_TIME_FIELD: Final = "Dieses Zeitfeld gibt es in den Berichtsdaten nicht."
_NOT_SUPPORTED: Final = "Wird in Berichten nicht unterstützt."
DROPPED_ELEMENTS_TICKETS: Final[Mapping[str, str]] = {
    **{
        name: _NO_TIME_FIELD
        for name in (
            "ChangeTime",
            "LastChangeTime",
            "PendingUntilTime",
            "EscalationTime",
            "EscalationResponseTime",
            "EscalationUpdateTime",
            "EscalationSolutionTime",
        )
    },
    **{
        name: _NOT_SUPPORTED
        for name in (
            "LockIDs",
            "Title",
            "CreatedStateIDs",
            "CreatedPriorityIDs",
            "CreatedUserIDs",
        )
    },
}
_DROPPED_PREFIXES: Final = (
    ("MIMEBase_", _NOT_SUPPORTED),
    ("DynamicField_", "Dynamische Felder stehen in Berichten noch nicht zur Verfügung."),
)
_FILE_IDS_REASON: Final = "IDs aus einer Exportdatei – bitte die Auswahl prüfen."


def dropped_reason(element: str, table: Mapping[str, str]) -> str:
    """Why an element without a field mapping is not taken over."""
    if element in table:
        return table[element]
    for prefix, reason in _DROPPED_PREFIXES:
        if element.startswith(prefix):
            return reason
    return "Unbekanntes Element."


def file_ids_unreliable(stat: ZnunyStat, el: StatElement, fmap: FieldMap) -> bool:
    """File origin, ``id`` element not exported by name: the ids are foreign (ruling 16)."""
    return stat.origin == "file" and fmap.kind == "id" and el.element not in FILE_NAME_ELEMENTS


def _int(value: object) -> int | None:
    text = str(value).strip()
    return int(text) if text.isascii() and text.isdigit() else None


def resolve_values(
    el: StatElement,
    fmap: FieldMap,
    stat: ZnunyStat,
    lookups: StatLookups,
    issues: list[ImportIssue],
) -> list[int | str]:
    """The element's selected values in Tiqora's spelling; unusable ones become issues."""
    ref = element_ref(el)
    out: list[int | str] = []
    by_name: Mapping[str, int] | None = None
    if stat.origin == "file" and fmap.kind == "id":
        kind = FILE_NAME_ELEMENTS.get(el.element)
        if kind is not None:
            by_name = {
                "queue": lookups.queue_ids_by_name,
                "state": lookups.state_ids_by_name,
                "priority": lookups.priority_ids_by_name,
                "user": lookups.user_ids_by_login,
            }[kind]
    for raw in el.selected:
        if fmap.kind == "id":
            if by_name is not None:
                found = by_name.get(raw.strip().casefold())
                if found is None:
                    issues.append(
                        make_issue(
                            ref, raw, f"„{raw[:80]}“ gibt es in diesem System nicht.", "dropped"
                        )
                    )
                else:
                    out.append(found)
                continue
            number = _int(raw)
            if number is None:
                issues.append(
                    make_issue(ref, raw, f"„{raw[:80]}“ ist keine gültige Nummer.", "dropped")
                )
            else:
                out.append(number)
        elif fmap.kind == "state_type":
            number = _int(raw)
            name = lookups.state_type_names.get(number) if number is not None else raw.strip()
            if name is None or name not in STATE_TYPES:
                issues.append(
                    make_issue(
                        ref,
                        raw,
                        "Diesen Status-Typ gibt es in Berichten nicht.",
                        "dropped",
                    )
                )
            else:
                out.append(name)
        else:
            if "*" in raw:
                issues.append(
                    make_issue(ref, raw, "Platzhalter (*) werden nicht übernommen.", "dropped")
                )
            elif raw.strip():
                out.append(raw)
    if stat.origin == "file" and fmap.kind == "state_type" and out:
        issues.append(make_issue(ref, ", ".join(el.selected), _FILE_IDS_REASON, "approximated"))
    return out


# --------------------------------------------------------------------------
# D6.3 time
# --------------------------------------------------------------------------

_RELATIVE_UNITS: Final[Mapping[str, Literal["day", "week", "month", "quarter", "year"]]] = {
    "Year": "year",
    "Quarter": "quarter",
    "Month": "month",
    "Week": "week",
    "Day": "day",
}
_SCALES: Final[Mapping[str, Grain]] = {
    "Day": "day",
    "Week": "week",
    "Month": "month",
    "Quarter": "quarter",
    "Year": "year",
}


def _parse_stamp(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def map_time_range(el: StatElement, issues: list[ImportIssue]) -> TimeRange | None:
    """The element's period (D6.3); ``None`` when it has none or it cannot be taken over."""
    ref = element_ref(el)
    t = el.time
    unit = t.get("TimeRelativeUnit")
    if unit:
        count = _int(t.get("TimeRelativeCount", ""))
        if count is None or count < 1:
            issues.append(
                make_issue(
                    ref,
                    t.get("TimeRelativeCount", ""),
                    "Der relative Zeitraum ist unvollständig.",
                    "dropped",
                )
            )
            return None
        if unit in ("Hour", "Minute", "Second"):
            issues.append(
                make_issue(
                    ref,
                    unit,
                    "Zeiträume in Stunden/Minuten gibt es in Berichten nicht.",
                    "dropped",
                )
            )
            return None
        target_unit: Literal["day", "week", "month", "quarter", "year"]
        if unit == "HalfYear":
            target_unit, count = "month", 6 * count
            issues.append(
                make_issue(ref, unit, "Halbjahre werden als 6 Monate übernommen.", "approximated")
            )
        elif unit in _RELATIVE_UNITS:
            target_unit = _RELATIVE_UNITS[unit]
        else:
            issues.append(make_issue(ref, unit, "Unbekannte Zeiteinheit.", "dropped"))
            return None
        upcoming = _int(t.get("TimeRelativeUpcomingCount", "")) or 0
        if upcoming > 1:
            issues.append(
                make_issue(ref, upcoming, "Zukünftige Zeiträume entfallen.", "approximated")
            )
        try:
            return RelativeTimeRange(unit=target_unit, count=count, include_current=upcoming >= 1)
        except ValidationError:
            issues.append(
                make_issue(ref, f"{count} {unit}", "Der Zeitraum ist zu lang.", "dropped")
            )
            return None
    start_raw, stop_raw = t.get("TimeStart"), t.get("TimeStop")
    if not start_raw and not stop_raw:
        return None
    start, stop = _parse_stamp(start_raw), _parse_stamp(stop_raw)
    if start is None or stop is None:
        issues.append(
            make_issue(
                ref,
                f"{start_raw or ''} – {stop_raw or ''}",
                "Der Zeitraum ist unvollständig oder nicht lesbar.",
                "dropped",
            )
        )
        return None
    if start.time() != datetime.min.time() or (stop.hour, stop.minute, stop.second) not in (
        (0, 0, 0),
        (23, 59, 59),
    ):
        issues.append(
            make_issue(
                ref,
                f"{start_raw} – {stop_raw}",
                "Uhrzeiten werden auf ganze Tage gerundet.",
                "approximated",
            )
        )
    try:
        return AbsoluteTimeRange(start=date(start.year, start.month, start.day), end=stop.date())
    except ValidationError:
        issues.append(
            make_issue(ref, f"{start_raw} – {stop_raw}", "Der Zeitraum ist ungültig.", "dropped")
        )
        return None


def map_grain(el: StatElement, issues: list[ImportIssue]) -> Grain | None:
    """The time axis' resolution from its scale (``SelectedValues[0]``); ``None`` drops the axis."""
    ref = element_ref(el)
    scale = el.selected[0] if el.selected else "Day"
    grain = _SCALES.get(scale)
    if grain is None:
        issues.append(
            make_issue(ref, scale, "Diese Zeiteinteilung gibt es in Berichten nicht.", "dropped")
        )
        return None
    count = _int(el.time.get("TimeScaleCount", "")) or 1
    if count > 1:
        issues.append(
            make_issue(
                ref,
                f"{count} {scale}",
                "Zusammenfassung über mehrere Einheiten entfällt.",
                "approximated",
            )
        )
    return grain


# --------------------------------------------------------------------------
# D6.4 stat-level attributes
# --------------------------------------------------------------------------

_CHART_FORMATS: Final = (
    ("D3::BarChart", "bar"),
    ("D3::LineChart", "line"),
    ("D3::StackedAreaChart", "area"),
)
_PLAIN_FORMATS: Final = frozenset({"Print", "CSV", "Excel"})


def map_visualization(stat: ZnunyStat, n_dims: int) -> Visualization:
    series = 1 if n_dims >= 2 else None
    for fmt, kind in _CHART_FORMATS:
        if fmt in stat.formats and n_dims >= 1:
            return Visualization(type=kind, series_dimension=series)  # type: ignore[arg-type]
    return Visualization(type="pivot" if n_dims >= 2 else "table")


def map_totals(stat: ZnunyStat, n_dims: int) -> Totals:
    return Totals(
        grand=stat.sum_col,
        rows=stat.sum_row and n_dims >= 2,
        columns=stat.sum_col and n_dims >= 2,
    )


def common_issues(stat: ZnunyStat) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    if not stat.valid:
        issues.append(
            make_issue(
                "Valid", "0", "In Znuny ungültig – als normaler Bericht übernommen.", "approximated"
            )
        )
    if stat.show_as_dashboard_widget:
        issues.append(
            make_issue(
                "ShowAsDashboardWidget",
                "1",
                "Bitte den Bericht im Dashboard als Widget hinzufügen.",
                "dropped",
            )
        )
    for fmt in stat.formats:
        if fmt not in _PLAIN_FORMATS and fmt not in dict(_CHART_FORMATS):
            issues.append(
                make_issue(
                    "Format",
                    fmt,
                    "Diese Darstellung gibt es in Berichten nicht; es wird eine Tabelle gezeigt.",
                    "approximated",
                )
            )
    if stat.time_zone is not None and valid_time_zone(stat.time_zone) is None:
        issues.append(
            make_issue(
                "TimeZone",
                stat.time_zone,
                "Zeitzone ungültig – es gilt die Zeitzone des Betrachters.",
                "approximated",
            )
        )
    return issues


# --------------------------------------------------------------------------
# D6.2 axes, restrictions, runtime parameters (all dynamic matrix objects)
# --------------------------------------------------------------------------


@dataclass(slots=True)
class MappedAxes:
    dimensions: list[DimensionRef] = field(default_factory=list)
    conditions: list[Condition] = field(default_factory=list)
    parameters: list[ReportParameter] = field(default_factory=list)
    time_field: str | None = None
    time_range: TimeRange | None = None

    def filter(self) -> Filter | None:
        if not self.conditions:
            return None
        if len(self.conditions) == 1:
            return Filter(where=self.conditions[0])
        return Filter(where=AllOf(all=list(self.conditions)))


def _parameter_key(index: int, field_key: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", f"p{index}_{field_key}".lower())[:40]


def map_axes(
    stat: ZnunyStat,
    lookups: StatLookups,
    fields: Mapping[str, FieldMap],
    dropped: Mapping[str, str],
    issues: list[ImportIssue],
    *,
    skip: Collection[str] = (),
) -> MappedAxes:
    """Axes, fixed filters, runtime parameters and the period of a dynamic matrix statistic."""
    out = MappedAxes()
    seen_dims: set[str] = set()
    empty_rows_noted = False

    def add_dimension(ref: str, ref_dim: DimensionRef) -> None:
        if ref_dim.key in seen_dims:
            issues.append(
                make_issue(ref, ref_dim.key, "Dieses Feld ist bereits eine Achse.", "dropped")
            )
        elif len(out.dimensions) >= MAX_DIMENSIONS:
            issues.append(
                make_issue(ref, ref_dim.key, "Berichte haben höchstens drei Achsen.", "dropped")
            )
        else:
            seen_dims.add(ref_dim.key)
            out.dimensions.append(ref_dim)

    # x axis first, then series, then restrictions (stable; the parser already orders them)
    for el in sorted(stat.elements, key=lambda e: tuple(USE_TAG).index(e.use)):
        if el.element in skip:
            continue
        ref = element_ref(el)
        if el.element == "SearchInArchive":
            mode = el.selected[0] if el.selected else ""
            if mode not in ("", "NotArchivedTickets"):
                issues.append(
                    make_issue(
                        ref,
                        mode,
                        "Archivierte Tickets werden in Berichten nicht mitgezählt.",
                        "dropped",
                    )
                )
            continue
        fmap = fields.get(el.element)
        if fmap is None:
            issues.append(
                make_issue(ref, el.element, dropped_reason(el.element, dropped), "dropped")
            )
            continue
        if fmap.kind == "time":
            if el.use != "restriction":
                grain = map_grain(el, issues)
                if grain is None:
                    continue
                add_dimension(ref, DimensionRef(key=fmap.field, grain=grain, top_n=None))
            period = map_time_range(el, issues)
            if period is not None:
                if out.time_range is None:
                    out.time_range, out.time_field = period, fmap.field
                else:
                    issues.append(
                        make_issue(ref, el.element, "Nur ein Zeitraum pro Bericht.", "dropped")
                    )
            continue
        if el.use != "restriction":
            add_dimension(ref, DimensionRef(key=fmap.field, top_n=None))
            if el.selected and not empty_rows_noted:
                empty_rows_noted = True
                issues.append(
                    make_issue(
                        ref,
                        el.element,
                        "Zeilen ohne Tickets werden nicht angezeigt.",
                        "approximated",
                    )
                )
        foreign = file_ids_unreliable(stat, el, fmap) and bool(el.selected)
        if foreign:
            issues.append(make_issue(ref, ", ".join(el.selected), _FILE_IDS_REASON, "approximated"))
            values: list[int | str] = []
        else:
            values = resolve_values(el, fmap, stat, lookups, issues)
        if not el.fixed or foreign:
            if len(out.parameters) >= MAX_PARAMETERS:
                issues.append(
                    make_issue(
                        ref, el.element, "Zu viele zur Laufzeit änderbare Felder.", "dropped"
                    )
                )
                continue
            out.parameters.append(
                ReportParameter(
                    key=_parameter_key(len(out.parameters), fmap.field),
                    field=fmap.field,
                    op="in",
                    default=values or None,
                    allowed_values=None,
                )
            )
        elif values:
            out.conditions.append(Condition(field=fmap.field, op="in", value=values))
    return out
