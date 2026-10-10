"""Source ``ticket_events``: history events joined to their ticket fact (integer join)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, FromClause, case, false, null

from tiqora.db.tiqora.models import TiqoraReportTicketEvent, TiqoraReportTicketFact
from tiqora.reporting.history_events import EVENT_KINDS
from tiqora.reporting.semantic import CompileContext, ExprFn, Measure, MeasureAgg, ReportSource
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


#: Event kinds whose from/to columns hold the given meaning (history_events.derive_events):
#: ``from_id``/``to_id`` is a queue id for queue (and create), a state id for state/close/reopen;
#: ``from_value``/``to_value`` is a state type name for create/state/close/reopen only
#: (process_activity and merge store other values there).
_QUEUE_KINDS = ("create", "queue")
_STATE_KINDS = ("state", "close", "reopen")
_STATE_TYPE_KINDS = ("create", *_STATE_KINDS)


def _only_for(column: str, kinds: tuple[str, ...]) -> ExprFn:
    """``e.<column>`` for events of ``kinds``, NULL otherwise (no cross-kind leakage)."""

    def expr(ctx: CompileContext) -> ColumnElement[Any]:
        return case((E.c.kind.in_(kinds), E.c[column]), else_=null())

    return expr


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
            attribute_dim("from_queue", _only_for("from_id", _QUEUE_KINDS), "queue"),
            attribute_dim("to_queue", _only_for("to_id", _QUEUE_KINDS), "queue"),
            attribute_dim("from_state", _only_for("from_id", _STATE_KINDS), "state"),
            attribute_dim("to_state", _only_for("to_id", _STATE_KINDS), "state"),
            dim(
                "from_state_type",
                "enum",
                _only_for("from_value", _STATE_TYPE_KINDS),
                values="reports.values.state_type",
                enum=STATE_TYPES,
            ),
            dim(
                "to_state_type",
                "enum",
                _only_for("to_value", _STATE_TYPE_KINDS),
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
        archive_exclusion=lambda ctx: F.c.archived.is_(false()),
        common_fields={
            "queue": "ticket_queue",
            "priority": "ticket_priority",
            "type": "ticket_type",
            "customer": "customer",
            "channel": "channel",
        },
    )
