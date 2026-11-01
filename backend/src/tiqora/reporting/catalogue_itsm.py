"""ITSM built-in reports (category ``itsm``): ticket types, first-level solution, CMDB, changes.

Titles and descriptions are frontend texts under ``reports.builtin.<name>.*``. The registry lists
them after the core built-ins; the API hides those on sources that are unavailable in the current
ITSM mode (``api/v1/reports.py``).
"""

from __future__ import annotations

from typing import Any, Final

from tiqora.reporting.catalogue import (
    LAST_3_MONTHS,
    LAST_MONTH,
    BuiltinReport,
    _report,
)

ITSM_EXPECTED: Final = [
    "itsm_tickets_by_type",
    "itsm_created_by_type",
    "itsm_open_by_type",
    "itsm_first_level_solution",
    "itsm_first_level_trend",
    "itsm_solution_time",
    "itsm_solution_time_trend",
    "itsm_tickets_by_role",
    "cmdb_cis_by_class",
    "cmdb_cis_created",
    "itsm_changes_by_status",
    "itsm_change_outcomes",
    "itsm_change_transitions",
    "itsm_change_requesters",
    "itsm_changes_per_ci_class",
    "itsm_incident_prone_cis",
]

#: built-ins about changes on a source that outlives the change module (``ci_tickets``); the ones on
#: the change sources go with their source
CHANGE_ONLY_BUILTINS: Final = frozenset({"builtin.itsm_changes_per_ci_class"})

_ROLES_FLS: Final[dict[str, Any]] = {
    "field": "itsm_role",
    "op": "in",
    "value": ["incident", "major_incident", "service_request"],
}
_CLOSED: Final[dict[str, Any]] = {"field": "state_type", "op": "in", "value": ["closed"]}
_FIRST_LEVEL: Final[dict[str, Any]] = {"all": [_ROLES_FLS, _CLOSED]}
_GRAND_ROWS_COLUMNS: Final = {"grand": True, "rows": True, "columns": True}


def _day(key: str) -> dict[str, Any]:
    return {"key": key, "grain": "day"}


def _by(key: str, *choices: str, top_n: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"key": key}
    if choices:
        out["choices"] = list(choices)
    if top_n is not None:
        out["top_n"] = top_n
    return out


_TYPE_CHOICES = ("priority", "queue", "service")


def itsm_builtin_reports() -> list[BuiltinReport]:
    """The ITSM built-ins in catalogue order (``ITSM_EXPECTED``)."""
    return [
        _report(
            "itsm_tickets_by_type",
            "itsm",
            "tickets",
            dims=[_by("type"), _by("priority", "state", "queue", "service")],
            measures=["tickets"],
            viz={"type": "pivot"},
            time_range=LAST_MONTH,
            totals=_GRAND_ROWS_COLUMNS,
        ),
        _report(
            "itsm_created_by_type",
            "itsm",
            "tickets",
            dims=[_day("created"), _by("type", "priority", "state", "queue", "service")],
            measures=["tickets"],
            viz={"type": "stacked_bar", "series_dimension": 1},
            time_range=LAST_MONTH,
        ),
        _report(
            "itsm_open_by_type",
            "itsm",
            "open_tickets",
            dims=[_by("type"), _by("priority", "queue", "service")],
            measures=["tickets"],
            viz={"type": "pivot"},
            time_range=None,
            totals=_GRAND_ROWS_COLUMNS,
        ),
        _report(
            "itsm_first_level_solution",
            "itsm",
            "tickets",
            dims=[_by("type", *_TYPE_CHOICES)],
            measures=["tickets", "first_level_solved", "first_level_solution_rate"],
            viz={"type": "table"},
            time_range=LAST_MONTH,
            filter_where=_FIRST_LEVEL,
            totals={"grand": True},
        ),
        _report(
            "itsm_first_level_trend",
            "itsm",
            "tickets",
            dims=[_day("created"), _by("type", *_TYPE_CHOICES)],
            measures=["first_level_solution_rate"],
            viz={"type": "line", "series_dimension": 1},
            time_range=LAST_MONTH,
            filter_where=_FIRST_LEVEL,
        ),
        _report(
            "itsm_solution_time",
            "itsm",
            "tickets",
            dims=[_by("type", *_TYPE_CHOICES)],
            measures=["tickets", "itsm_solution_avg", "itsm_solution_p50"],
            viz={"type": "table"},
            time_range=LAST_MONTH,
            filter_where=_CLOSED,
            totals={"grand": True},
        ),
        _report(
            "itsm_solution_time_trend",
            "itsm",
            "tickets",
            dims=[_day("created"), _by("type", *_TYPE_CHOICES)],
            measures=["itsm_solution_avg"],
            viz={"type": "line", "series_dimension": 1},
            time_range=LAST_MONTH,
            filter_where=_CLOSED,
        ),
        _report(
            "itsm_tickets_by_role",
            "itsm",
            "tickets",
            dims=[_day("created"), _by("itsm_role")],
            measures=["tickets"],
            viz={"type": "stacked_bar", "series_dimension": 1},
            time_range=LAST_MONTH,
        ),
        _report(
            "cmdb_cis_by_class",
            "itsm",
            "config_items",
            dims=[
                _by("class"),
                _by(
                    "deployment_state", "incident_state", "deployment_category", "incident_category"
                ),
            ],
            measures=["config_items"],
            viz={"type": "pivot"},
            time_range=None,
            totals=_GRAND_ROWS_COLUMNS,
        ),
        _report(
            "cmdb_cis_created",
            "itsm",
            "config_items",
            dims=[_day("created"), _by("class", "deployment_state", "incident_state")],
            measures=["config_items"],
            viz={"type": "stacked_bar", "series_dimension": 1},
            time_range=LAST_MONTH,
            time_field="created",
        ),
        _report(
            "itsm_changes_by_status",
            "itsm",
            "changes",
            dims=[_day("created"), _by("change_status", "phase", "change_type", "risk_level")],
            measures=["changes"],
            viz={"type": "stacked_bar", "series_dimension": 1},
            time_range=LAST_MONTH,
        ),
        _report(
            "itsm_change_outcomes",
            "itsm",
            "changes",
            dims=[_by("change_type", "risk_level", "service", "queue")],
            measures=[
                "changes",
                "successful_changes",
                "failed_changes",
                "change_success_rate",
                "rejected_changes",
                "late_changes",
            ],
            viz={"type": "table"},
            time_range=LAST_3_MONTHS,
            totals={"grand": True},
        ),
        _report(
            "itsm_change_transitions",
            "itsm",
            "change_events",
            dims=[_day("occurred"), _by("to_phase")],
            measures=["changes"],
            viz={"type": "stacked_bar", "series_dimension": 1},
            time_range=LAST_MONTH,
            filter_where={"field": "to_phase", "op": "in", "value": ["rejected", "cancelled"]},
        ),
        _report(
            "itsm_change_requesters",
            "itsm",
            "changes",
            dims=[_by("customer_user", "created_by", "owner", top_n=20)],
            measures=["changes"],
            viz={"type": "bar"},
            time_range=LAST_MONTH,
        ),
        _report(
            "itsm_changes_per_ci_class",
            "itsm",
            "ci_tickets",
            dims=[_by("ci_class")],
            measures=["changes", "cis"],
            viz={"type": "bar", "chart_measures": [0]},
            time_range=LAST_MONTH,
            filter_where={"field": "is_change", "op": "is_true"},
        ),
        _report(
            "itsm_incident_prone_cis",
            "itsm",
            "ci_tickets",
            dims=[_by("ci", "ci_class", top_n=20)],
            measures=["incidents", "open_incidents", "tickets"],
            viz={"type": "bar", "chart_measures": [0]},
            time_range=LAST_3_MONTHS,
        ),
    ]
