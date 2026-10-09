"""Source ``ticket_events``: history events joined to their ticket fact (integer join)."""

from __future__ import annotations

from sqlalchemy import FromClause

from tiqora.db.tiqora.models import TiqoraReportTicketEvent, TiqoraReportTicketFact
from tiqora.reporting.history_events import EVENT_KINDS
from tiqora.reporting.semantic import CompileContext, Measure, MeasureAgg, ReportSource
from tiqora.reporting.sources._common import (
    CHANNELS,
    STATE_TYPES,
    aliased,
    attribute_dim,
    col,
    dim,
    measure,
)

E = aliased(TiqoraReportTicketEvent, "e")
F = aliased(TiqoraReportTicketFact, "f")


def _from(ctx: CompileContext) -> FromClause:
    return E.join(F, F.c.ticket_id == E.c.ticket_id)


def _dwell(key: str, agg: MeasureAgg) -> Measure:
    return measure(key, agg, expr=lambda ctx: E.c.from_duration_seconds / 3600.0, unit="hours")


def ticket_events_source() -> ReportSource:
    return ReportSource(
        key="ticket_events",
        label_key="reports.sources.ticket_events",
        from_clause=_from,
        scope_column=col(F, "queue_id"),
        dimensions=(
            dim(
                "kind",
                "enum",
                col(E, "kind"),
                values="reports.values.event_kind",
                enum=tuple(EVENT_KINDS),
            ),
            dim("occurred", "date", col(E, "occurred_at")),
            dim("occurred_hour", "hour", col(E, "occurred_at")),
            dim("occurred_weekday", "weekday", col(E, "occurred_at")),
            dim("actor", "id", col(E, "actor_user_id"), labels="user"),
            attribute_dim("queue", col(E, "queue_id"), "queue"),
            attribute_dim("from_queue", col(E, "from_id"), "queue"),
            attribute_dim("to_queue", col(E, "to_id"), "queue"),
            attribute_dim("from_state", col(E, "from_id"), "state"),
            attribute_dim("to_state", col(E, "to_id"), "state"),
            dim(
                "from_state_type",
                "enum",
                col(E, "from_value"),
                values="reports.values.state_type",
                enum=STATE_TYPES,
            ),
            dim(
                "to_state_type",
                "enum",
                col(E, "to_value"),
                values="reports.values.state_type",
                enum=STATE_TYPES,
            ),
            attribute_dim("ticket_queue", col(F, "queue_id"), "queue"),
            attribute_dim("ticket_state", col(F, "state_id"), "state"),
            attribute_dim("ticket_priority", col(F, "priority_id"), "priority"),
            attribute_dim("ticket_type", col(F, "type_id"), "type"),
            dim(
                "channel",
                "enum",
                col(F, "channel"),
                values="reports.values.channel",
                enum=CHANNELS,
            ),
            dim("customer", "string", col(F, "customer_id"), labels="customer_company"),
        ),
        measures=(
            measure("events", "count"),
            measure("tickets", "count_distinct", expr=col(E, "ticket_id")),
            _dwell("dwell_avg_hours", "avg"),
            _dwell("dwell_p50_hours", "p50"),
            _dwell("dwell_p90_hours", "p90"),
        ),
        time_fields=("occurred",),
        default_time_field="occurred",
    )
