"""Source ``tickets``: one row per ticket from ``tiqora_report_ticket_fact``."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, FromClause, and_, false, not_, or_, true

from tiqora.db.tiqora.models import TiqoraReportTicketFact
from tiqora.reporting.semantic import BoolFn, CompileContext, Measure, ReportSource
from tiqora.reporting.sources._common import (
    CHANNELS,
    OPEN_TYPES,
    STATE_TYPES,
    aliased,
    attribute_dim,
    col,
    dim,
    flag,
    measure,
)

F = aliased(TiqoraReportTicketFact, "f")


def _c(name: str) -> ColumnElement[Any]:
    return F.c[name]


def _gt0(name: str) -> BoolFn:
    return lambda ctx: _c(name) > 0


def _breach(prefix: str) -> BoolFn:
    """Breached, or still undecided with a due date in the past (``ctx.now``)."""

    def cond(ctx: CompileContext) -> ColumnElement[bool]:
        breached = _c(f"{prefix}_breached")
        due = _c(f"{prefix}_due_at")
        return or_(
            breached.is_(true()),
            and_(breached.is_(None), due.is_not(None), due < ctx.now),
        )

    return cond


def _near_miss(prefix: str, working: str) -> BoolFn:
    def cond(ctx: CompileContext) -> ColumnElement[bool]:
        target = _c(f"{prefix}_target_minutes")
        return and_(
            _c(f"{prefix}_breached").is_(False),
            target > 0,
            _c(working) * 100 >= target * (100 - ctx.near_miss_percent),
        )

    return cond


def _has_target(ctx: CompileContext) -> ColumnElement[bool]:
    return or_(
        _c("first_response_target_minutes") > 0,
        _c("solution_target_minutes") > 0,
        _c("update_target_minutes") > 0,
    )


def _sla_met(ctx: CompileContext) -> ColumnElement[bool]:
    return and_(
        _has_target(ctx),
        not_(_breach("first_response")(ctx)),
        not_(_breach("solution")(ctx)),
        _c("update_breach_count") == 0,
    )


def _fcr(ctx: CompileContext) -> ColumnElement[bool]:
    return and_(_c("close_count") > 0, _c("reopen_count") == 0, _c("article_out_count") <= 1)


def _stat(key: str, agg: Any, column: str, unit: Any = "minutes") -> Measure:
    return measure(key, agg, expr=col(F, column), unit=unit)


def _from(ctx: CompileContext) -> FromClause:
    return F


def _measures() -> tuple[Measure, ...]:
    return (
        measure("tickets", "count"),
        measure("closed_tickets", "count_if", condition=lambda ctx: _c("state_type") == "closed"),
        measure(
            "open_tickets",
            "count_if",
            condition=lambda ctx: _c("state_type").in_(OPEN_TYPES),
        ),
        measure("reopened_tickets", "count_if", condition=_gt0("reopen_count")),
        measure(
            "reopen_rate",
            "rate",
            condition=_gt0("reopen_count"),
            denominator=_gt0("close_count"),
            unit="percent",
        ),
        measure(
            "fcr_rate", "rate", condition=_fcr, denominator=_gt0("close_count"), unit="percent"
        ),
        _stat("first_response_avg", "avg", "first_response_working_minutes"),
        _stat("first_response_p50", "p50", "first_response_working_minutes"),
        _stat("first_response_p90", "p90", "first_response_working_minutes"),
        _stat("first_response_clock_avg", "avg", "first_response_minutes"),
        _stat("first_response_clock_p50", "p50", "first_response_minutes"),
        _stat("solution_avg", "avg", "solution_working_minutes"),
        _stat("solution_p50", "p50", "solution_working_minutes"),
        _stat("solution_p90", "p90", "solution_working_minutes"),
        _stat("solution_clock_avg", "avg", "solution_minutes"),
        _stat("solution_clock_p50", "p50", "solution_minutes"),
        measure("response_breaches", "count_if", condition=_breach("first_response")),
        measure(
            "response_breach_rate",
            "rate",
            condition=_breach("first_response"),
            denominator=_gt0("first_response_target_minutes"),
            unit="percent",
        ),
        measure("solution_breaches", "count_if", condition=_breach("solution")),
        measure(
            "solution_breach_rate",
            "rate",
            condition=_breach("solution"),
            denominator=_gt0("solution_target_minutes"),
            unit="percent",
        ),
        measure("update_breaches", "sum", expr=col(F, "update_breach_count")),
        measure("update_breached_tickets", "count_if", condition=_gt0("update_breach_count")),
        measure(
            "response_near_misses",
            "count_if",
            condition=_near_miss("first_response", "first_response_working_minutes"),
        ),
        measure(
            "solution_near_misses",
            "count_if",
            condition=_near_miss("solution", "solution_working_minutes"),
        ),
        measure(
            "sla_met_rate", "rate", condition=_sla_met, denominator=_has_target, unit="percent"
        ),
        measure("accounted_time", "sum", expr=col(F, "accounted_time"), unit="units"),
        measure("agent_touches_avg", "avg", expr=col(F, "agent_touch_count"), unit="number"),
        measure("queue_moves_avg", "avg", expr=col(F, "queue_move_count"), unit="number"),
        measure(
            "pingpong_tickets", "count_if", condition=lambda ctx: _c("queue_pingpong").is_(true())
        ),
    )


def tickets_source() -> ReportSource:
    return ReportSource(
        key="tickets",
        label_key="reports.sources.tickets",
        from_clause=_from,
        scope_column=col(F, "queue_id"),
        dimensions=(
            attribute_dim("queue", col(F, "queue_id"), "queue"),
            attribute_dim("queue_at_create", col(F, "queue_id_at_create"), "queue"),
            attribute_dim("state", col(F, "state_id"), "state"),
            dim(
                "state_type",
                "enum",
                col(F, "state_type"),
                values="reports.values.state_type",
                enum=STATE_TYPES,
            ),
            attribute_dim("priority", col(F, "priority_id"), "priority"),
            attribute_dim("type", col(F, "type_id"), "type"),
            attribute_dim("service", col(F, "service_id"), "service"),
            attribute_dim("sla", col(F, "sla_id"), "sla"),
            dim("owner", "id", col(F, "owner_id"), labels="user"),
            dim("responsible", "id", col(F, "responsible_id"), labels="user"),
            dim("customer", "string", col(F, "customer_id"), labels="customer_company"),
            dim("customer_user", "string", col(F, "customer_user"), labels="customer_user"),
            dim(
                "channel",
                "enum",
                col(F, "channel"),
                values="reports.values.channel",
                enum=CHANNELS,
            ),
            dim("created", "date", col(F, "created_at")),
            dim("closed", "date", col(F, "closed_at")),
            dim("first_response", "date", col(F, "first_response_at")),
            dim("created_hour", "hour", col(F, "created_at")),
            dim("created_weekday", "weekday", col(F, "created_at")),
            dim("process", "string", col(F, "process_entity_id")),
        ),
        measures=_measures(),
        filter_fields=(
            flag("archived", col(F, "archived")),
            flag("merged", col(F, "merged")),
            flag("reopened", lambda ctx: _c("reopen_count") > 0),
            flag("has_first_response", lambda ctx: _c("first_response_at").is_not(None)),
            flag("ai_triaged", col(F, "ai_triaged")),
            flag("ai_auto_replied", col(F, "ai_auto_replied")),
            flag("ai_handed_off", col(F, "ai_handed_off")),
        ),
        time_fields=("created", "closed", "first_response"),
        default_time_field="created",
        archive_exclusion=lambda ctx: F.c.archived.is_(false()),
    )
