"""Znuny ITSM stats mapping: first-level solution rate and ITSM solution time (plan D10).

Pure: no database, no network; every lookup comes from ``StatLookups``. Registers its mappers in
``OBJECT_MAPPERS`` on import; ``mapper.py`` imports this module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from tiqora.domain.timezones import valid_time_zone
from tiqora.filters import Condition
from tiqora.reporting.definition import MeasureRef, ReportDefinition
from tiqora.reporting.znuny_stats.map_more import _NO_PERIOD
from tiqora.reporting.znuny_stats.mapping_common import (
    _NOT_SUPPORTED,
    DROPPED_ELEMENTS_TICKETS,
    ELEMENT_FIELDS_TICKETS,
    OBJECT_MAPPERS,
    ImportIssue,
    StatLookups,
    StatMapping,
    common_issues,
    element_ref,
    make_issue,
    map_axes,
    map_totals,
    map_visualization,
    sorted_issues,
)
from tiqora.reporting.znuny_stats.model import ZnunyStat

_PREFIX: Final = "Kernel::System::Stats::Dynamic::"
CONFIG_ITEM = f"{_PREFIX}ITSMConfigItem"
FIRST_LEVEL = f"{_PREFIX}ITSMTicketFirstLevelSolutionRate"
SOLUTION_AVERAGE = f"{_PREFIX}ITSMTicketSolutionTimeAverage"
CHANGE = f"{_PREFIX}ITSMChangeManagement"
CHANGE_HISTORY = f"{_PREFIX}ITSMChangeManagementHistory"
CHANGES_INCIDENTS = f"{_PREFIX}ITSMChangeManagementChangesIncidents"
RFC_REQUESTER = f"{_PREFIX}ITSMChangeManagementRfcRequester"
CHANGES_PER_CI_CLASS = f"{_PREFIX}ITSMChangeManagementChangesPerCIClasses"

_FIRST_LEVEL_OBJECT: Final = (
    "Znuny zeigt die Anzahl der im ersten Kontakt gelösten Tickets; Tiqora zeigt dazu die Quote."
)
_SOLUTION_OBJECT: Final = (
    "Znuny zählt die Zeit in den Status von Ticket::ViewableStateType; "
    "Tiqora die Zeit zwischen Erstellen bzw. Wiedereröffnen und Schließen."
)
_IDS_AS_STORED: Final = "IDs wie in Znuny hinterlegt – bitte Typ und Status prüfen."
_STORED_ID_ELEMENTS: Final = frozenset({"TypeIDs", "StateIDs"})
_STATE_FIELDS: Final = frozenset({"state", "state_type"})
_DROPPED_TICKETS: Final[Mapping[str, str]] = {
    **DROPPED_ELEMENTS_TICKETS,
    **{name: _NOT_SUPPORTED for name in ("From", "To", "Cc", "Subject", "Body")},
}


def _ticket_itsm(
    stat: ZnunyStat,
    lookups: StatLookups,
    measures: Sequence[str],
    object_issue: str,
) -> StatMapping:
    """Ticket matrix over the ``tickets`` source with the ITSM stats' closed-state default."""
    issues = common_issues(stat)
    issues.append(
        make_issue("Object", stat.object_module.removeprefix(_PREFIX), object_issue, "approximated")
    )
    axes = map_axes(
        stat,
        lookups,
        ELEMENT_FIELDS_TICKETS,
        _DROPPED_TICKETS,
        issues,
        names_in_file=False,
    )
    if stat.origin == "db":
        for el in stat.elements:
            if el.element in _STORED_ID_ELEMENTS and el.selected:
                issues.append(
                    make_issue(
                        element_ref(el), ", ".join(el.selected), _IDS_AS_STORED, "approximated"
                    )
                )
    if not any(c.field in _STATE_FIELDS for c in axes.conditions):
        # Znuny counts closed tickets; a state selection that became a runtime parameter, or
        # none at all, must never let open tickets in.
        axes.conditions.append(Condition(field="state_type", op="in", value=["closed"]))
    if axes.time_range is None:
        issues.append(make_issue("UseAsRestriction", "", _NO_PERIOD, "approximated"))
    n_dims = len(axes.dimensions)
    definition = ReportDefinition(
        source="tickets",
        dimensions=axes.dimensions,
        measures=[MeasureRef(key=key) for key in measures],
        filter=axes.filter(),
        parameters=axes.parameters,
        time_field=axes.time_field,
        time_range=axes.time_range,
        time_zone=valid_time_zone(stat.time_zone),
        visualization=map_visualization(stat, n_dims),
        totals=map_totals(stat, n_dims),
        limit=None,
    )
    return StatMapping(
        stat=stat, target="report", definition=definition, search=None, issues=sorted_issues(issues)
    )


def map_first_level(stat: ZnunyStat, lookups: StatLookups, is_admin: bool) -> StatMapping:
    """``Dynamic::ITSMTicketFirstLevelSolutionRate``: solved in first contact, count and rate."""
    return _ticket_itsm(
        stat, lookups, ("first_level_solved", "first_level_solution_rate"), _FIRST_LEVEL_OBJECT
    )


def map_solution_average(stat: ZnunyStat, lookups: StatLookups, is_admin: bool) -> StatMapping:
    """``Dynamic::ITSMTicketSolutionTimeAverage``: average solution time."""
    return _ticket_itsm(stat, lookups, ("itsm_solution_avg",), _SOLUTION_OBJECT)


OBJECT_MAPPERS[FIRST_LEVEL] = map_first_level
OBJECT_MAPPERS[SOLUTION_AVERAGE] = map_solution_average

__all__ = [
    "CHANGE",
    "CHANGES_INCIDENTS",
    "CHANGES_PER_CI_CLASS",
    "CHANGE_HISTORY",
    "CONFIG_ITEM",
    "FIRST_LEVEL",
    "RFC_REQUESTER",
    "SOLUTION_AVERAGE",
    "ImportIssue",
    "map_first_level",
    "map_solution_average",
]
