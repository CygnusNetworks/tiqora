"""Znuny stats mapping: accounted time, solution/response time, ticket list (plan D6.5/D6.6).

Pure. Registers its mappers in ``OBJECT_MAPPERS`` on import; ``mapper.py`` imports this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Literal

from tiqora.domain.timezones import valid_time_zone
from tiqora.reporting.definition import MeasureRef, ReportDefinition
from tiqora.reporting.znuny_stats.mapping_common import (
    DROPPED_ELEMENTS_TICKETS,
    ELEMENT_FIELDS_TICKETS,
    FILE_NAME_ELEMENTS,
    OBJECT_MAPPERS,
    FieldMap,
    ImportIssue,
    MappedSearch,
    StatLookups,
    StatMapping,
    _int,
    _parse_stamp,
    common_issues,
    dropped_reason,
    element_ref,
    make_issue,
    map_axes,
    map_totals,
    map_visualization,
    resolve_values,
    sorted_issues,
)
from tiqora.reporting.znuny_stats.model import StatElement, ZnunyStat
from tiqora.saved_searches.znuny_import import map_search_profile
from tiqora.saved_searches.znuny_reader import ProfileValues

ACCOUNTED_TIME = "Kernel::System::Stats::Dynamic::TicketAccountedTime"
SOLUTION_RESPONSE = "Kernel::System::Stats::Dynamic::TicketSolutionResponseTime"
TICKET_LIST = "Kernel::System::Stats::Dynamic::TicketList"

_NO_PERIOD: Final = "Ohne Zeitraum zeigt Tiqora die letzten 30 Tage."
_NO_KIND: Final = "Kennzahl gibt es in Tiqora nicht."
_MIN_MAX: Final = (
    "Minimum und Maximum gibt es nicht; Tiqora bietet Median (P50) und 90-%-Wert (P90)."
)
_ESCALATION: Final = (
    "Hängt in Znuny von der Eskalationskonfiguration ab; "
    "bitte die Werte ohne diesen Zusatz verwenden."
)
_ACCOUNTED_UNSUPPORTED: Final = "Wird bei der Zeiterfassung nicht unterstützt."
_KINDS: Final = "KindsOfReporting"

ELEMENT_FIELDS_ACCOUNTED: Final[Mapping[str, FieldMap]] = {
    "AccountedByAgent": FieldMap("agent", "id"),
    "QueueIDs": FieldMap("queue", "id"),
    "TypeIDs": FieldMap("type", "id"),
    "ServiceIDs": FieldMap("service", "id"),
    "CustomerID": FieldMap("customer", "string"),
    "CustomerIDRaw": FieldMap("customer", "string"),
    "CustomerUserLogin": FieldMap("customer_user", "string"),
    "CustomerUserLoginRaw": FieldMap("customer_user", "string"),
    "CustomerUserID": FieldMap("customer_user", "string"),
    "ArticleAccountedTime": FieldMap("booked", "time"),
}
_ACCOUNTED_KINDS: Final[Mapping[str, str]] = {
    "TotalTime": "time_units",
    "NumberOfTickets": "tickets",
}
_SOLUTION_KINDS: Final[Mapping[str, str]] = {
    "SolutionAverageAllOver": "solution_clock_avg",
    "NumberOfTicketsAllOver": "closed_tickets",
    "SolutionWorkingTimeAverage": "solution_avg",
    "ResponseWorkingTimeAverage": "first_response_avg",
}
_ESCALATION_KINDS: Final = frozenset({"SolutionAverage", "ResponseAverage", "NumberOfTickets"})
_MIN_MAX_PARTS: Final = ("MinTime", "MaxTime", "MinWorkingTime", "MaxWorkingTime")


def _zone(stat: ZnunyStat) -> str | None:
    return valid_time_zone(stat.time_zone)


def _none(stat: ZnunyStat, issues: list[ImportIssue]) -> StatMapping:
    return StatMapping(
        stat=stat, target="none", definition=None, search=None, issues=sorted_issues(issues)
    )


def _kind_reason(kind: str, *, solution: bool) -> str:
    if solution and kind in _ESCALATION_KINDS:
        return _ESCALATION
    if solution and any(part in kind for part in _MIN_MAX_PARTS):
        return _MIN_MAX
    return _NO_KIND


def _measures(
    stat: ZnunyStat, kinds: Mapping[str, str], issues: list[ImportIssue], *, solution: bool
) -> list[MeasureRef]:
    """``KindsOfReporting`` (an axis in Znuny) becomes the measures, in selection order."""
    keys: list[str] = []
    found = False
    for el in stat.elements:
        if el.element != _KINDS:
            continue
        found = True
        ref = element_ref(el)
        for kind in el.selected:
            key = kinds.get(kind)
            if key is None:
                issues.append(
                    make_issue(ref, kind, _kind_reason(kind, solution=solution), "dropped")
                )
            elif key not in keys:
                keys.append(key)
    if not found:
        issues.append(make_issue(f"UseAsXvalue.{_KINDS}", "", _NO_KIND, "dropped"))
    return [MeasureRef(key=key) for key in keys]


def _matrix(
    stat: ZnunyStat,
    lookups: StatLookups,
    *,
    source: str,
    kinds: Mapping[str, str],
    fields: Mapping[str, FieldMap],
    dropped: Mapping[str, str],
    default_dropped: str,
    solution: bool,
) -> StatMapping:
    issues = common_issues(stat)
    measures = _measures(stat, kinds, issues, solution=solution)
    if not measures:
        return _none(stat, issues)
    axes = map_axes(
        stat,
        lookups,
        fields,
        dropped,
        issues,
        skip=(_KINDS,),
        default_dropped=default_dropped,
    )
    if axes.time_range is None:
        issues.append(make_issue("UseAsRestriction", "", _NO_PERIOD, "approximated"))
    n_dims = len(axes.dimensions)
    definition = ReportDefinition(
        source=source,
        dimensions=axes.dimensions,
        measures=measures,
        filter=axes.filter(),
        parameters=axes.parameters,
        time_field=axes.time_field,
        time_range=axes.time_range,
        time_zone=_zone(stat),
        visualization=map_visualization(stat, n_dims),
        totals=map_totals(stat, n_dims),
        limit=None,
    )
    return StatMapping(
        stat=stat, target="report", definition=definition, search=None, issues=sorted_issues(issues)
    )


def map_accounted_time(stat: ZnunyStat, lookups: StatLookups, is_admin: bool) -> StatMapping:
    """``Dynamic::TicketAccountedTime``: booked time over agents, queues ... (D6.5)."""
    return _matrix(
        stat,
        lookups,
        source="accounted_time",
        kinds=_ACCOUNTED_KINDS,
        fields=ELEMENT_FIELDS_ACCOUNTED,
        dropped={},
        default_dropped=_ACCOUNTED_UNSUPPORTED,
        solution=False,
    )


def map_solution_response(stat: ZnunyStat, lookups: StatLookups, is_admin: bool) -> StatMapping:
    """``Dynamic::TicketSolutionResponseTime``: solution and response times (D6.5)."""
    return _matrix(
        stat,
        lookups,
        source="tickets",
        kinds=_SOLUTION_KINDS,
        fields=ELEMENT_FIELDS_TICKETS,
        dropped=DROPPED_ELEMENTS_TICKETS,
        default_dropped="Unbekanntes Element.",
        solution=True,
    )


# --------------------------------------------------------------------------
# D6.6 TicketList -> saved search
# --------------------------------------------------------------------------

#: Elements whose selection is a list of ids (names in an export file, see FILE_NAME_ELEMENTS).
_LIST_IDS: Final = frozenset(
    {
        "QueueIDs",
        "StateIDs",
        "StateTypeIDs",
        "PriorityIDs",
        "TypeIDs",
        "ServiceIDs",
        "SLAIDs",
        "OwnerIDs",
        "ResponsibleIDs",
        "LockIDs",
        "CreatedQueueIDs",
        "CreatedStateIDs",
        "CreatedPriorityIDs",
        "CreatedUserIDs",
    }
)
#: Time restriction -> (search-type key, key prefix); the mapper reports the unsupported ones.
_LIST_TIME: Final[Mapping[str, tuple[str, str]]] = {
    "CreateTime": ("TimeSearchType", "TicketCreate"),
    "ChangeTime": ("ChangeTimeSearchType", "TicketChange"),
    "PendingUntilTime": ("PendingTimeSearchType", "TicketPending"),
    "CloseTime": ("CloseTimeSearchType", "TicketClose"),
    "LastChangeTime": ("LastChangeTimeSearchType", "TicketLastChange"),
    "EscalationTime": ("EscalationTimeSearchType", "TicketEscalation"),
}
#: Relative unit -> (multiplier, search format).
_LIST_UNITS: Final[Mapping[str, tuple[int, str]]] = {
    "Year": (1, "year"),
    "HalfYear": (6, "month"),
    "Quarter": (3, "month"),
    "Month": (1, "month"),
    "Week": (1, "week"),
    "Day": (1, "day"),
    "Hour": (1, "hour"),
    "Minute": (1, "minute"),
}
_UNIT_DE: Final[Mapping[str, str]] = {
    "year": "Jahre",
    "month": "Monate",
    "week": "Wochen",
    "day": "Tage",
    "hour": "Stunden",
    "minute": "Minuten",
}
_SORT_KEYS: Final[Mapping[str, str]] = {
    "Age": "age",
    "Created": "created",
    "Changed": "changed",
    "TicketNumber": "tn",
    "Title": "title",
    "Priority": "priority",
}
_HISTORIC: Final = frozenset({"StateIDsHistoric", "StateTypeIDsHistoric", "HistoricTimeRange"})
_LIVE_ONLY: Final = frozenset({"OrderBy", "SortSequence", "Limit", "TicketAttributes"})
_FIXED_AT_IMPORT: Final = (
    "In Znuny zur Laufzeit änderbar – in der gespeicherten Suche fest eingestellt."
)


def _first(stat: ZnunyStat, name: str) -> StatElement | None:
    return next((e for e in stat.elements if e.element == name), None)


def _list_sort(stat: ZnunyStat) -> tuple[str, Literal["asc", "desc"], ImportIssue | None]:
    """``OrderBy`` / ``SortSequence`` by element name, whatever their ``use`` (ruling 18)."""
    order_el = _first(stat, "OrderBy")
    key, issue = "age", None
    if order_el is not None and order_el.selected:
        wanted = order_el.selected[0]
        if wanted in _SORT_KEYS:
            key = _SORT_KEYS[wanted]
        else:
            issue = make_issue(
                element_ref(order_el),
                wanted,
                f"Sortierung nach {wanted} gibt es nicht; es wird nach Alter sortiert.",
                "approximated",
            )
    seq_el = _first(stat, "SortSequence")
    direction: Literal["asc", "desc"] = "desc"
    if seq_el is not None and seq_el.selected and seq_el.selected[0] == "Up":
        direction = "asc"
    return key, direction, issue


def _list_time(el: StatElement, values: ProfileValues, issues: list[ImportIssue]) -> None:
    type_key, prefix = _LIST_TIME[el.element]
    ref = element_ref(el)
    t = el.time
    unit = t.get("TimeRelativeUnit")
    if unit:
        count = _int(t.get("TimeRelativeCount", ""))
        spec = _LIST_UNITS.get(unit)
        if count is None or count < 1 or spec is None:
            reason = (
                "Zeiträume in Sekunden gibt es in Suchen nicht."
                if unit == "Second"
                else "Der relative Zeitraum ist unvollständig oder unbekannt."
            )
            issues.append(make_issue(ref, unit, reason, "dropped"))
            return
        amount, fmt = spec[0] * count, spec[1]
        values[type_key] = "TimePoint"
        values[f"{prefix}TimePoint"] = str(amount)
        values[f"{prefix}TimePointStart"] = "Last"
        values[f"{prefix}TimePointFormat"] = fmt
        upcoming = _int(t.get("TimeRelativeUpcomingCount", "")) or 0
        if upcoming >= 1:
            issues.append(
                make_issue(
                    ref,
                    unit,
                    f"Als gleitender Zeitraum der letzten {amount} {_UNIT_DE[fmt]} übernommen.",
                    "approximated",
                )
            )
            if upcoming > 1:
                issues.append(
                    make_issue(ref, upcoming, "Zukünftige Zeiträume entfallen.", "approximated")
                )
        return
    start_raw, stop_raw = t.get("TimeStart"), t.get("TimeStop")
    if not start_raw and not stop_raw:
        return
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
        return
    values[type_key] = "TimeSlot"
    for side, moment in (("Start", start), ("Stop", stop)):
        values[f"{prefix}Time{side}Year"] = f"{moment.year:04d}"
        values[f"{prefix}Time{side}Month"] = f"{moment.month:02d}"
        values[f"{prefix}Time{side}Day"] = f"{moment.day:02d}"


def stat_to_profile_values(
    stat: ZnunyStat, lookups: StatLookups
) -> tuple[ProfileValues, list[ImportIssue]]:
    """A ticket-list statistic as a Znuny search profile (ids as strings) plus its issues."""
    values: ProfileValues = {}
    issues: list[ImportIssue] = []
    for el in stat.elements:
        name, ref = el.element, element_ref(el)
        if name in _LIVE_ONLY:
            if name == "TicketAttributes":
                issues.append(
                    make_issue(
                        ref,
                        ", ".join(el.selected),
                        "Die Ticketliste zeigt ihre festen Spalten; "
                        "die Spaltenauswahl wird nicht übernommen.",
                        "approximated",
                    )
                )
            elif name == "Limit":
                issues.append(
                    make_issue(
                        ref,
                        ", ".join(el.selected),
                        "Die Liste zeigt alle Treffer seitenweise.",
                        "approximated",
                    )
                )
            continue
        if name in _HISTORIC:
            issues.append(
                make_issue(
                    ref,
                    ", ".join(el.selected) or name,
                    "Historische Status werden in Suchen nicht unterstützt.",
                    "dropped",
                )
            )
            continue
        if name.startswith("DynamicField_"):
            issues.append(
                make_issue(
                    ref,
                    ", ".join(el.selected) or name,
                    dropped_reason(name, DROPPED_ELEMENTS_TICKETS),
                    "dropped",
                )
            )
            continue
        if not el.fixed:
            issues.append(make_issue(ref, name, _FIXED_AT_IMPORT, "approximated"))
        if name in _LIST_TIME:
            _list_time(el, values, issues)
        elif name in _LIST_IDS:
            if stat.origin == "file" and name not in FILE_NAME_ELEMENTS and name != "LockIDs":
                if name == "StateTypeIDs":
                    values[name] = list(el.selected)
                    issues.append(
                        make_issue(
                            ref,
                            ", ".join(el.selected),
                            "IDs aus einer Exportdatei – bitte die Auswahl prüfen.",
                            "approximated",
                        )
                    )
                else:
                    issues.append(
                        make_issue(
                            ref,
                            ", ".join(el.selected),
                            "IDs aus einer Exportdatei – bitte die Auswahl prüfen.",
                            "dropped",
                        )
                    )
                continue
            if name in FILE_NAME_ELEMENTS:
                resolved = resolve_values(el, FieldMap(name, "id"), stat, lookups, issues)
                chosen = [str(v) for v in resolved]
            else:
                chosen = list(el.selected)
            if chosen:
                values[name] = chosen
        elif name.startswith("MIMEBase_"):
            if el.selected:
                values[name.removeprefix("MIMEBase_")] = el.selected[0]
        elif name in DROPPED_ELEMENTS_TICKETS and name.endswith("Time"):
            issues.append(make_issue(ref, name, DROPPED_ELEMENTS_TICKETS[name], "dropped"))
        elif el.selected:
            # CustomerID*, CustomerUserLogin*, Title, SearchInArchive, anything unknown: the
            # wave-2 mapper maps it or reports it.
            values[name] = el.selected[0]
    _, _, sort_issue = _list_sort(stat)
    if sort_issue is not None:
        issues.append(sort_issue)
    return values, issues


def map_ticket_list(stat: ZnunyStat, lookups: StatLookups, is_admin: bool) -> StatMapping:
    """``Dynamic::TicketList``: becomes a saved ticket search (A1, D6.6)."""
    issues = common_issues(stat)
    values, more = stat_to_profile_values(stat, lookups)
    issues.extend(more)
    mapped = map_search_profile(stat.title, values, lookups.search, is_admin=is_admin)
    for item in mapped.unmapped:
        issues.append(
            make_issue(f"UseAsRestriction.{item.key}", item.value, item.reason, "dropped")
        )
    if mapped.criteria.where is None:
        issues.append(
            make_issue(
                "UseAsRestriction",
                "",
                "Ohne Einschränkung – zeigt alle Tickets, die Sie sehen dürfen.",
                "approximated",
            )
        )
    sort_by, sort_order, _ = _list_sort(stat)
    return StatMapping(
        stat=stat,
        target="saved_search",
        definition=None,
        search=MappedSearch(
            criteria=mapped.criteria,
            include_archived=mapped.include_archived,
            sort_by=sort_by,
            sort_order=sort_order,
        ),
        issues=sorted_issues(issues),
    )


OBJECT_MAPPERS[ACCOUNTED_TIME] = map_accounted_time
OBJECT_MAPPERS[SOLUTION_RESPONSE] = map_solution_response
OBJECT_MAPPERS[TICKET_LIST] = map_ticket_list
