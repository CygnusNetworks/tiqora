"""Map a parsed Znuny statistic to a Tiqora report definition (plan D5/D6)."""

from __future__ import annotations

from tiqora.domain.timezones import valid_time_zone
from tiqora.extensions.core import get_registry
from tiqora.reporting.definition import (
    DefinitionError,
    DimensionRef,
    MeasureRef,
    RelativeTimeRange,
    ReportDefinition,
    Totals,
    Visualization,
    validate_definition,
)
from tiqora.reporting.znuny_stats import map_more  # noqa: F401  (registers the Task 4 mappers)
from tiqora.reporting.znuny_stats.mapping_common import (
    DROPPED_ELEMENTS_TICKETS,
    ELEMENT_FIELDS_TICKETS,
    OBJECT_MAPPERS,
    ImportIssue,
    MappedSearch,
    StatLookups,
    StatMapping,
    StatTarget,
    common_issues,
    make_issue,
    map_axes,
    map_totals,
    map_visualization,
    sorted_issues,
)
from tiqora.reporting.znuny_stats.model import ZnunyStat

__all__ = [
    "OBJECT_MAPPERS",
    "ImportIssue",
    "MappedSearch",
    "StatLookups",
    "StatMapping",
    "StatTarget",
    "map_stat",
]

DYNAMIC_TICKET = "Kernel::System::Stats::Dynamic::Ticket"
STATIC_STATE_ACTION = "static:StateAction"
_UNSUPPORTED = "Diese Art von Statistik kann Tiqora nicht übernehmen."
_NO_PERIOD = "Ohne Zeitraum zeigt Tiqora die letzten 30 Tage."


def _none(stat: ZnunyStat, issues: list[ImportIssue]) -> StatMapping:
    return StatMapping(
        stat=stat, target="none", definition=None, search=None, issues=sorted_issues(issues)
    )


def _zone(stat: ZnunyStat) -> str | None:
    return valid_time_zone(stat.time_zone)


def map_ticket_matrix(stat: ZnunyStat, lookups: StatLookups, is_admin: bool) -> StatMapping:
    """``Dynamic::Ticket``: count of tickets over up to three axes (D6.1-D6.4)."""
    issues = common_issues(stat)
    axes = map_axes(stat, lookups, ELEMENT_FIELDS_TICKETS, DROPPED_ELEMENTS_TICKETS, issues)
    if axes.time_range is None:
        issues.append(make_issue("UseAsRestriction", "", _NO_PERIOD, "approximated"))
    n_dims = len(axes.dimensions)
    definition = ReportDefinition(
        source="tickets",
        dimensions=axes.dimensions,
        measures=[MeasureRef(key="tickets")],
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
        stat=stat,
        target="report",
        definition=definition,
        search=None,
        issues=sorted_issues(issues),
    )


def map_state_action(stat: ZnunyStat, lookups: StatLookups, is_admin: bool) -> StatMapping:
    """Static ``StateAction``: events per day and kind over the last month (A13)."""
    issues = common_issues(stat)
    issues.append(
        make_issue(
            "Object",
            "StateAction",
            "Znuny zählt jeden Historientyp; Tiqora zählt die Ereignisarten der Berichtsdaten "
            "(Erstellt, Status, Queue, Antwort …). Der Monat ist zur Laufzeit wählbar.",
            "approximated",
        )
    )
    definition = ReportDefinition(
        source="ticket_events",
        dimensions=[
            DimensionRef(key="occurred", grain="day", top_n=None),
            DimensionRef(key="kind", top_n=None),
        ],
        measures=[MeasureRef(key="events")],
        time_range=RelativeTimeRange(unit="month", count=1),
        time_zone=_zone(stat),
        visualization=Visualization(type="pivot"),
        totals=Totals(grand=True, rows=True, columns=True),
        limit=None,
    )
    return StatMapping(
        stat=stat,
        target="report",
        definition=definition,
        search=None,
        issues=sorted_issues(issues),
    )


OBJECT_MAPPERS[DYNAMIC_TICKET] = map_ticket_matrix
OBJECT_MAPPERS[STATIC_STATE_ACTION] = map_state_action


def _mapper_key(stat: ZnunyStat) -> str | None:
    if stat.stat_type == "static":
        return f"static:{stat.static_file}" if stat.static_file else None
    if stat.stat_type == "dynamic":
        return stat.object_module
    return None


def map_stat(stat: ZnunyStat, lookups: StatLookups, *, is_admin: bool) -> StatMapping:
    """Map ``stat``; never raises. Unknown objects and invalid results become target ``none``."""
    key = _mapper_key(stat)
    mapper = OBJECT_MAPPERS.get(key) if key is not None else None
    if mapper is None:
        return _none(stat, [make_issue("Object", stat.object_module, _UNSUPPORTED, "dropped")])
    try:
        result = mapper(stat, lookups, is_admin)
        if result.target == "report" and result.definition is not None:
            validate_definition(result.definition, get_registry())
    except (DefinitionError, ValueError) as exc:
        message = getattr(exc, "message", None) or str(exc).splitlines()[0]
        if not message.endswith("."):
            message += "."
        return _none(
            stat,
            [
                make_issue(
                    "Object",
                    stat.object_module,
                    f"Die Statistik ergibt keinen gültigen Bericht: {message}",
                    "dropped",
                )
            ],
        )
    return result
