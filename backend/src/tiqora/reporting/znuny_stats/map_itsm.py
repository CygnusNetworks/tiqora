"""Znuny ITSM stats mapping: ticket, CI and change statistics (plan D10).

Pure: no database, no network; every lookup comes from ``StatLookups``. Registers its mappers in
``OBJECT_MAPPERS`` on import; ``mapper.py`` imports this module.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Final

from tiqora.domain.timezones import valid_time_zone
from tiqora.filters import Condition
from tiqora.reporting.definition import (
    DimensionRef,
    MeasureRef,
    ReportDefinition,
    ReportParameter,
)
from tiqora.reporting.znuny_stats.change_states import ZNUNY_CHANGE_STATES
from tiqora.reporting.znuny_stats.map_more import _NO_PERIOD
from tiqora.reporting.znuny_stats.mapping_common import (
    _NOT_SUPPORTED,
    DROPPED_ELEMENTS_TICKETS,
    ELEMENT_FIELDS_TICKETS,
    MAX_PARAMETERS,
    OBJECT_MAPPERS,
    FieldMap,
    ImportIssue,
    StatLookups,
    StatMapping,
    _parameter_key,
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
_CLOSED_WITHOUT_SELECTION: Final = (
    "Der Bericht zählt nur geschlossene Tickets, auch ohne Status-Auswahl."
)
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
        for el in stat.elements:
            fmap = ELEMENT_FIELDS_TICKETS.get(el.element)
            if fmap is not None and fmap.field in _STATE_FIELDS:
                issues.append(
                    make_issue(
                        element_ref(el),
                        ", ".join(el.selected),
                        "Der Bericht zählt immer nur geschlossene Tickets; "
                        "wählen Sie einen offenen Status, bleibt das Ergebnis leer."
                        if el.selected
                        else _CLOSED_WITHOUT_SELECTION,
                        "approximated",
                    )
                )
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


# --------------------------------------------------------------------------
# CI and change statistics (Mode B sources)
# --------------------------------------------------------------------------

_MODE_B_CI: Final = (
    "Die Tiqora-CMDB ist nicht aktiv; der Bericht zeigt erst Daten, "
    "wenn Tiqora das ITSM führt (Modus B)."
)
_MODE_B_CHANGE: Final = "Tiqora-Changes gibt es nur, wenn Tiqora das ITSM führt (Modus B)."
_CIP_DROPPED: Final = (
    "Kategorie, Auswirkung und Priorität aus Znuny gibt es bei Tiqora-Changes nicht; "
    "Tiqora bewertet das Risiko."
)
_CHANGE_DROPPED: Final[Mapping[str, str]] = {
    name: _CIP_DROPPED for name in ("CategoryIDs", "ImpactIDs", "PriorityIDs")
}
_CI_STATE_QUIRK: Final = (
    "Das Element StateIDs gibt es im CI-Modul nicht mehr; es wird als Verwendungsstatus übernommen."
)
_CHANGE_STATE_QUIRK: Final = (
    "Das Element StateIDs heißt im Change-Modul ChangeStateIDs; "
    "es wird als Change-Status übernommen."
)
_HISTORY_OBJECT: Final = (
    "Znuny listet die Nummern der Changes, die im Zeitraum in diesen Status wechselten; "
    "Tiqora zählt sie."
)
_INCIDENTS_OBJECT: Final = "Tiqora zeigt alle Ticket-Typen; Changes sind Tickets vom Typ „Change“."
_REQUESTER_OBJECT: Final = (
    "Znuny zählt Tickets der RfC-Typen je Agent oder Kunde; Tiqora zählt Changes je Kundenbenutzer."
)
_CI_CLASS_OBJECT: Final = (
    "Tiqora ordnet Changes über ihre verknüpften CIs zu (Znuny über Arbeitsaufträge)."
)
_CI_STATE_PERIOD: Final = "Znuny wertet den Vorfallsstatus im Zeitraum aus; Tiqora den aktuellen."
_CI_STATE_NAMES: Final = (
    "Die Statusnamen aus Znuny lassen sich nicht zuordnen. "
    "Solange Sie keine Auswahl treffen, zählt der Bericht alle Vorfallsstatus."
)

_FIELDS_CONFIG_ITEM: Final[Mapping[str, FieldMap]] = {
    "ClassIDs": FieldMap("class", "cmdb_class"),
    "DeplStateIDs": FieldMap("deployment_state", "cmdb_catalog"),
    "InciStateIDs": FieldMap("incident_state", "cmdb_catalog"),
    "StateIDs": FieldMap("deployment_state", "cmdb_catalog"),
    "CreateTime": FieldMap("created", "time"),
    "ChangeTime": FieldMap("changed", "time"),
}
_DROPPED_CONFIG_ITEM: Final[Mapping[str, str]] = {
    "Number": _NOT_SUPPORTED,
    "Name": _NOT_SUPPORTED,
}
_FIELDS_CHANGE: Final[Mapping[str, FieldMap]] = {
    "ChangeStateIDs": FieldMap("change_status", "change_state"),
    "StateIDs": FieldMap("change_status", "change_state"),
    "TimePeriod": FieldMap("created", "time"),
}
_FIELDS_HISTORY: Final[Mapping[str, FieldMap]] = {
    "NewValues": FieldMap("to_phase", "change_phase"),
    "TimePeriod": FieldMap("occurred", "time"),
}
_FIELDS_CREATED: Final[Mapping[str, FieldMap]] = {"TimePeriod": FieldMap("created", "time")}
_FIELDS_CI_CLASS: Final[Mapping[str, FieldMap]] = {
    "CIClassIDs": FieldMap("ci_class", "cmdb_class"),
    "CIStateIDs": FieldMap("ci_incident_state", "cmdb_catalog"),
    "TimePeriod": FieldMap("created", "time"),
}


def _mode_b_issue(
    stat: ZnunyStat, lookups: StatLookups, text: str, issues: list[ImportIssue]
) -> None:
    if lookups.itsm_mode != "tiqora":
        issues.append(
            make_issue("Object", stat.object_module.removeprefix(_PREFIX), text, "approximated")
        )


def _object_issue(stat: ZnunyStat, text: str, issues: list[ImportIssue]) -> None:
    issues.append(
        make_issue("Object", stat.object_module.removeprefix(_PREFIX), text, "approximated")
    )


def _quirk_issue(stat: ZnunyStat, element: str, text: str, issues: list[ImportIssue]) -> None:
    for el in stat.elements:
        if el.element == element:
            issues.append(make_issue(element_ref(el), element, text, "approximated"))


def _matrix(
    stat: ZnunyStat,
    lookups: StatLookups,
    issues: list[ImportIssue],
    *,
    source: str,
    measure: str,
    fields: Mapping[str, FieldMap],
    dropped: Mapping[str, str],
    names_in_file: bool = True,
    skip: Collection[str] = (),
    extra_dimensions: Sequence[str] = (),
    fixed: Sequence[Condition] = (),
    extra_parameters: Sequence[str] = (),
    default_period: bool = True,
) -> StatMapping:
    """Matrix over one of the Mode-B sources (``config_items``, ``changes``, ...)."""
    axes = map_axes(stat, lookups, fields, dropped, issues, skip=skip, names_in_file=names_in_file)
    axes.dimensions.extend(DimensionRef(key=key, top_n=None) for key in extra_dimensions)
    axes.conditions.extend(fixed)
    for field_key in extra_parameters:
        if len(axes.parameters) < MAX_PARAMETERS:
            axes.parameters.append(
                ReportParameter(
                    key=_parameter_key(len(axes.parameters), field_key),
                    field=field_key,
                    op="in",
                    default=None,
                    allowed_values=None,
                )
            )
    if axes.time_range is None and default_period:
        issues.append(make_issue("UseAsRestriction", "", _NO_PERIOD, "approximated"))
    n_dims = len(axes.dimensions)
    definition = ReportDefinition(
        source=source,
        dimensions=axes.dimensions,
        measures=[MeasureRef(key=measure)],
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


def map_config_item(stat: ZnunyStat, lookups: StatLookups, is_admin: bool) -> StatMapping:
    """``Dynamic::ITSMConfigItem``: count of CIs; no period means all CIs."""
    issues = common_issues(stat)
    _mode_b_issue(stat, lookups, _MODE_B_CI, issues)
    _quirk_issue(stat, "StateIDs", _CI_STATE_QUIRK, issues)
    dropped = {
        **_DROPPED_CONFIG_ITEM,
        **{
            el.element: "CI-Attribute stehen in Berichten noch nicht zur Verfügung."
            for el in stat.elements
            if el.element.startswith("XML::")
        },
    }
    return _matrix(
        stat,
        lookups,
        issues,
        source="config_items",
        measure="config_items",
        fields=_FIELDS_CONFIG_ITEM,
        dropped=dropped,
        names_in_file=False,
        default_period=False,
    )


def map_change(stat: ZnunyStat, lookups: StatLookups, is_admin: bool) -> StatMapping:
    """``Dynamic::ITSMChangeManagement``: count of changes by creation date and state."""
    issues = common_issues(stat)
    _mode_b_issue(stat, lookups, _MODE_B_CHANGE, issues)
    _quirk_issue(stat, "StateIDs", _CHANGE_STATE_QUIRK, issues)
    return _matrix(
        stat,
        lookups,
        issues,
        source="changes",
        measure="changes",
        fields=_FIELDS_CHANGE,
        dropped=_CHANGE_DROPPED,
    )


def map_change_history(stat: ZnunyStat, lookups: StatLookups, is_admin: bool) -> StatMapping:
    """``Dynamic::ITSMChangeManagementHistory``: state changes of changes in the period."""
    issues = common_issues(stat)
    _object_issue(stat, _HISTORY_OBJECT, issues)
    _mode_b_issue(stat, lookups, _MODE_B_CHANGE, issues)
    return _matrix(
        stat,
        lookups,
        issues,
        source="change_events",
        measure="changes",
        fields=_FIELDS_HISTORY,
        dropped={},
    )


def map_changes_incidents(stat: ZnunyStat, lookups: StatLookups, is_admin: bool) -> StatMapping:
    """``Dynamic::ITSMChangeManagementChangesIncidents``: tickets by type over the period."""
    issues = common_issues(stat)
    for el in stat.elements:
        if el.element == "Object":
            issues.append(
                make_issue(
                    element_ref(el), ", ".join(el.selected), _INCIDENTS_OBJECT, "approximated"
                )
            )
    return _matrix(
        stat,
        lookups,
        issues,
        source="tickets",
        measure="tickets",
        fields=_FIELDS_CREATED,
        dropped={},
        skip=("Object",),
        extra_dimensions=("type",),
    )


def map_rfc_requester(stat: ZnunyStat, lookups: StatLookups, is_admin: bool) -> StatMapping:
    """``Dynamic::ITSMChangeManagementRfcRequester``: changes per customer user."""
    issues = common_issues(stat)
    _object_issue(stat, _REQUESTER_OBJECT, issues)
    _mode_b_issue(stat, lookups, _MODE_B_CHANGE, issues)
    return _matrix(
        stat,
        lookups,
        issues,
        source="changes",
        measure="changes",
        fields=_FIELDS_CREATED,
        dropped={},
        skip=("Requester",),
        extra_dimensions=("customer_user",),
    )


def map_changes_per_ci_class(stat: ZnunyStat, lookups: StatLookups, is_admin: bool) -> StatMapping:
    """``Dynamic::ITSMChangeManagementChangesPerCIClasses``: changes per class of linked CIs."""
    issues = common_issues(stat)
    _object_issue(stat, _CI_CLASS_OBJECT, issues)
    _mode_b_issue(stat, lookups, _MODE_B_CHANGE, issues)
    skip: list[str] = []
    extra_parameters: list[str] = []
    for el in stat.elements:
        if el.element != "CIStateIDs" or not el.selected:
            continue
        if any(raw.strip().isascii() and raw.strip().isdigit() for raw in el.selected):
            issues.append(
                make_issue(
                    element_ref(el), ", ".join(el.selected), _CI_STATE_PERIOD, "approximated"
                )
            )
            continue
        # state names (export files) cannot be looked up; the shipped selection is all of them
        skip.append("CIStateIDs")
        issues.append(
            make_issue(element_ref(el), ", ".join(el.selected), _CI_STATE_NAMES, "approximated")
        )
        if not el.fixed:
            extra_parameters.append("ci_incident_state")
    return _matrix(
        stat,
        lookups,
        issues,
        source="ci_tickets",
        measure="changes",
        fields=_FIELDS_CI_CLASS,
        dropped=_CHANGE_DROPPED,
        skip=skip,
        fixed=[Condition(field="is_change", op="is_true")],
        extra_parameters=extra_parameters,
    )


OBJECT_MAPPERS[FIRST_LEVEL] = map_first_level
OBJECT_MAPPERS[SOLUTION_AVERAGE] = map_solution_average
OBJECT_MAPPERS[CONFIG_ITEM] = map_config_item
OBJECT_MAPPERS[CHANGE] = map_change
OBJECT_MAPPERS[CHANGE_HISTORY] = map_change_history
OBJECT_MAPPERS[CHANGES_INCIDENTS] = map_changes_incidents
OBJECT_MAPPERS[RFC_REQUESTER] = map_rfc_requester
OBJECT_MAPPERS[CHANGES_PER_CI_CLASS] = map_changes_per_ci_class

__all__ = [
    "CHANGE",
    "CHANGES_INCIDENTS",
    "CHANGES_PER_CI_CLASS",
    "CHANGE_HISTORY",
    "CONFIG_ITEM",
    "FIRST_LEVEL",
    "RFC_REQUESTER",
    "SOLUTION_AVERAGE",
    "ZNUNY_CHANGE_STATES",
    "ImportIssue",
    "map_change",
    "map_change_history",
    "map_changes_incidents",
    "map_changes_per_ci_class",
    "map_config_item",
    "map_first_level",
    "map_rfc_requester",
    "map_solution_average",
]
