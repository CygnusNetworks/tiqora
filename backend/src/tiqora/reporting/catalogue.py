"""Built-in report catalogue v1: 25 read-only reports (plan R11).

Titles and descriptions are frontend texts under ``reports.builtin.<name>.*``;
this module only carries the keys, so the catalogue stays language-neutral.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from tiqora.extensions.registry import ExtensionPoint
from tiqora.reporting.definition import ReportDefinition

ReportCategory = Literal[
    "overview", "volume", "backlog", "sla", "quality", "agents", "time", "customers", "patterns"
]
_PREFIX = "builtin."


@dataclass(frozen=True, slots=True)
class BuiltinReport:
    key: str  # "builtin.<name>"
    category: ReportCategory
    definition: ReportDefinition
    overview: bool = False  # shown in the overview section of /agent/reports

    @property
    def name(self) -> str:
        return self.key.removeprefix(_PREFIX)

    @property
    def title_key(self) -> str:
        return f"reports.builtin.{self.name}.title"

    @property
    def description_key(self) -> str:
        return f"reports.builtin.{self.name}.description"


BUILTIN_REPORTS: ExtensionPoint[BuiltinReport] = ExtensionPoint(
    key="core.reporting.builtin_reports",
    item_key=lambda r: r.key,
    description="Built-in reports of the report catalogue.",
)

#: Field of the ``queues`` runtime parameter per source (every built-in is queue-scoped).
QUEUE_PARAM_FIELD = {
    "tickets": "queue",
    "ticket_events": "ticket_queue",
    "daily_snapshot": "queue",
    "accounted_time": "queue",
    "open_tickets": "queue",
}

LAST_30_DAYS: dict[str, Any] = {
    "kind": "relative",
    "unit": "day",
    "count": 30,
    "include_current": True,
}
LAST_12_WEEKS: dict[str, Any] = {"kind": "relative", "unit": "week", "count": 12}
LAST_MONTH: dict[str, Any] = {"kind": "relative", "unit": "month", "count": 1}
LAST_3_MONTHS: dict[str, Any] = {"kind": "relative", "unit": "month", "count": 3}
LAST_12_MONTHS_INCL: dict[str, Any] = {
    "kind": "relative",
    "unit": "month",
    "count": 12,
    "include_current": True,
}


def _report(
    name: str,
    category: ReportCategory,
    source: str,
    *,
    dims: list[dict[str, Any]],
    measures: list[str],
    viz: dict[str, Any],
    time_range: dict[str, Any] | None,
    overview: bool = False,
    filter_where: dict[str, Any] | None = None,
    time_field: str | None = None,
    totals: dict[str, bool] | None = None,
) -> BuiltinReport:
    return BuiltinReport(
        key=f"{_PREFIX}{name}",
        category=category,
        overview=overview,
        definition=ReportDefinition.model_validate(
            {
                "source": source,
                "dimensions": dims,
                "measures": [{"key": m} for m in measures],
                "filter": {"version": 1, "where": filter_where} if filter_where else None,
                "parameters": [
                    {
                        "key": "queues",
                        "field": QUEUE_PARAM_FIELD[source],
                        "op": "in",
                        "label_key": "reports.parameters.queues",
                    }
                ],
                "time_field": time_field,
                "time_range": time_range,
                "visualization": viz,
                "totals": totals or {},
            }
        ),
    )


def _kind_in(*kinds: str) -> dict[str, Any]:
    return {"field": "kind", "op": "in", "value": list(kinds)}


def _time_by(name: str, dim: str) -> BuiltinReport:
    return _report(
        name,
        "time",
        "accounted_time",
        dims=[{"key": dim, "top_n": 25} if dim != "type" else {"key": dim}],
        measures=["time_units", "bookings", "tickets"],
        viz={"type": "bar", "chart_measures": [0]},
        time_range=LAST_MONTH,
        totals={"grand": True},
    )


def core_builtin_reports() -> list[BuiltinReport]:
    """The 25 built-ins in catalogue order."""
    return [
        _report(
            "ticket_kpis",
            "overview",
            "tickets",
            dims=[],
            measures=[
                "tickets",
                "closed_tickets",
                "response_breach_rate",
                "first_response_p50",
                "solution_p50",
            ],
            viz={"type": "kpi"},
            time_range=LAST_30_DAYS,
            overview=True,
        ),
        _report(
            "volume",
            "volume",
            "ticket_events",
            dims=[{"key": "occurred", "grain": "week"}, {"key": "kind"}],
            measures=["events"],
            viz={"type": "line", "series_dimension": 1},
            time_range=LAST_12_WEEKS,
            overview=True,
            filter_where=_kind_in("create", "close"),
        ),
        _report(
            "backlog_trend",
            "backlog",
            "daily_snapshot",
            dims=[{"key": "snapshot", "grain": "day"}],
            measures=["open_tickets"],
            viz={"type": "line"},
            time_range=LAST_30_DAYS,
            overview=True,
        ),
        _report(
            "open_now",
            "backlog",
            "open_tickets",
            dims=[
                {
                    "key": "queue",
                    "choices": ["state", "priority", "owner", "type", "service"],
                    "top_n": 15,
                }
            ],
            measures=["tickets"],
            viz={"type": "bar"},
            time_range=None,
            overview=True,
        ),
        _report(
            "agent_activity",
            "agents",
            "ticket_events",
            dims=[{"key": "actor", "top_n": 25}, {"key": "kind"}],
            measures=["events"],
            viz={"type": "pivot"},
            time_range=LAST_30_DAYS,
            overview=True,
            filter_where=_kind_in("reply", "note", "close"),
            totals={"rows": True, "grand": True},
        ),
        _report(
            "volume_by_channel",
            "volume",
            "tickets",
            dims=[{"key": "created", "grain": "week"}, {"key": "channel"}],
            measures=["tickets"],
            viz={"type": "stacked_bar", "series_dimension": 1},
            time_range=LAST_12_WEEKS,
        ),
        _report(
            "channel_mix",
            "volume",
            "tickets",
            dims=[{"key": "channel"}],
            measures=["tickets"],
            viz={"type": "donut"},
            time_range=LAST_30_DAYS,
        ),
        _report(
            "volume_by_queue",
            "volume",
            "tickets",
            dims=[{"key": "queue", "choices": ["type", "service", "priority"], "top_n": 15}],
            measures=["tickets", "closed_tickets"],
            viz={"type": "bar", "chart_measures": [0]},
            time_range=LAST_30_DAYS,
        ),
        _report(
            "backlog_by_state",
            "backlog",
            "daily_snapshot",
            dims=[{"key": "snapshot", "grain": "day"}, {"key": "state_type"}],
            measures=["open_tickets"],
            viz={"type": "stacked_bar", "series_dimension": 1},
            time_range=LAST_30_DAYS,
        ),
        _report(
            "backlog_aging",
            "backlog",
            "open_tickets",
            dims=[{"key": "queue", "top_n": 15}],
            measures=["age_0_1d", "age_1_3d", "age_3_7d", "age_7_30d", "age_30d_plus"],
            viz={"type": "stacked_bar"},
            time_range=None,
        ),
        _report(
            "sla_compliance",
            "sla",
            "tickets",
            dims=[{"key": "queue", "choices": ["service", "sla", "customer", "type", "priority"]}],
            measures=[
                "tickets",
                "sla_met_rate",
                "response_breach_rate",
                "solution_breach_rate",
                "response_near_misses",
                "solution_near_misses",
                "update_breaches",
            ],
            viz={"type": "table"},
            time_range=LAST_MONTH,
            totals={"grand": True},
        ),
        _report(
            "sla_times",
            "sla",
            "tickets",
            dims=[{"key": "created", "grain": "week"}],
            measures=["first_response_p50", "first_response_p90", "solution_p50", "solution_p90"],
            viz={"type": "line"},
            time_range=LAST_12_WEEKS,
        ),
        _report(
            "sla_by_customer",
            "sla",
            "tickets",
            dims=[{"key": "customer", "top_n": 20}],
            measures=[
                "tickets",
                "sla_met_rate",
                "response_breaches",
                "solution_breaches",
                "first_response_p50",
                "solution_p50",
            ],
            viz={"type": "table"},
            time_range=LAST_3_MONTHS,
        ),
        _report(
            "first_contact_resolution",
            "quality",
            "tickets",
            dims=[{"key": "queue", "choices": ["type", "channel", "service"]}],
            measures=["closed_tickets", "fcr_rate", "reopen_rate"],
            viz={"type": "table"},
            time_range=LAST_MONTH,
            time_field="closed",
            totals={"grand": True},
        ),
        _report(
            "reopen_trend",
            "quality",
            "tickets",
            dims=[{"key": "closed", "grain": "week"}],
            measures=["reopen_rate", "reopened_tickets", "closed_tickets"],
            viz={"type": "line", "chart_measures": [0]},
            time_range=LAST_12_WEEKS,
            time_field="closed",
        ),
        _report(
            "queue_transfers",
            "quality",
            "ticket_events",
            dims=[{"key": "from_queue"}, {"key": "to_queue"}],
            measures=["events", "tickets"],
            viz={"type": "pivot"},
            time_range=LAST_30_DAYS,
            filter_where={"field": "kind", "op": "eq", "value": "queue"},
            totals={"rows": True, "columns": True, "grand": True},
        ),
        _report(
            "pingpong",
            "quality",
            "tickets",
            dims=[{"key": "queue", "top_n": 15}],
            measures=["tickets", "pingpong_tickets", "queue_moves_avg"],
            viz={"type": "table"},
            time_range=LAST_30_DAYS,
        ),
        _report(
            "time_in_state",
            "quality",
            "ticket_events",
            dims=[{"key": "from_state"}],
            measures=["events", "dwell_p50_hours", "dwell_p90_hours", "dwell_avg_hours"],
            viz={"type": "bar", "chart_measures": [1]},
            time_range=LAST_30_DAYS,
            filter_where={"field": "kind", "op": "eq", "value": "state"},
        ),
        _time_by("time_by_customer", "customer"),
        _time_by("time_by_queue", "queue"),
        _time_by("time_by_agent", "agent"),
        _time_by("time_by_type", "type"),
        _report(
            "time_by_agent_month",
            "time",
            "accounted_time",
            dims=[{"key": "agent"}, {"key": "booked", "grain": "month"}],
            measures=["time_units"],
            viz={"type": "pivot"},
            time_range=LAST_12_MONTHS_INCL,
            totals={"grand": True, "rows": True, "columns": True},
        ),
        _report(
            "top_customers",
            "customers",
            "tickets",
            dims=[{"key": "customer", "top_n": 20}],
            measures=["tickets", "closed_tickets", "solution_p50", "accounted_time"],
            viz={"type": "bar", "chart_measures": [0]},
            time_range=LAST_3_MONTHS,
        ),
        _report(
            "created_heatmap",
            "patterns",
            "tickets",
            dims=[{"key": "created_weekday"}, {"key": "created_hour"}],
            measures=["tickets"],
            viz={"type": "heatmap"},
            time_range=LAST_12_WEEKS,
        ),
    ]
