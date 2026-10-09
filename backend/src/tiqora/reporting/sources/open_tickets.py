"""Source ``open_tickets``: live view of open tickets from the Znuny tables ("now")."""

from __future__ import annotations

from datetime import UTC, timedelta

from sqlalchemy import ColumnElement, FromClause, and_

from tiqora.db.legacy.ticket import Ticket, TicketState, TicketStateType
from tiqora.domain.queue_service import OPEN_STATE_TYPES
from tiqora.domain.ticket_service import UNASSIGNED_OWNER_ID
from tiqora.reporting.semantic import BoolFn, CompileContext, Measure, ReportSource
from tiqora.reporting.sources._common import (
    OPEN_TYPES,
    aliased,
    attribute_dim,
    col,
    dim,
    measure,
)

T = aliased(Ticket, "t")
ST = aliased(TicketState, "st")
STT = aliased(TicketStateType, "stt")

#: (measure key, lower age bound in days (exclusive), upper age bound in days (inclusive))
_AGE_EDGES: tuple[tuple[str, int | None, int | None], ...] = (
    ("age_0_1d", None, 1),
    ("age_1_3d", 1, 3),
    ("age_3_7d", 3, 7),
    ("age_7_30d", 7, 30),
    ("age_30d_plus", 30, None),
)


def _from(ctx: CompileContext) -> FromClause:
    return T.join(ST, ST.c.id == T.c.ticket_state_id).join(STT, STT.c.id == ST.c.type_id)


def _base_where(ctx: CompileContext) -> list[ColumnElement[bool]]:
    return [STT.c.name.in_(sorted(OPEN_STATE_TYPES)), T.c.archive_flag == 0]


def _age(lower: int | None, upper: int | None) -> BoolFn:
    """Age in ``[lower, upper)`` days at ``ctx.now`` (matches the daily snapshot buckets)."""

    def cond(ctx: CompileContext) -> ColumnElement[bool]:
        created = T.c.create_time
        parts: list[ColumnElement[bool]] = []
        if upper is not None:
            parts.append(created > ctx.now - timedelta(days=upper))
        if lower is not None:
            parts.append(created <= ctx.now - timedelta(days=lower))
        return and_(*parts)

    return cond


def _escalated(ctx: CompileContext) -> ColumnElement[bool]:
    now_epoch = int(ctx.now.replace(tzinfo=UTC).timestamp())
    return and_(T.c.escalation_time > 0, T.c.escalation_time < now_epoch)


def _measures() -> tuple[Measure, ...]:
    return (
        measure("tickets", "count"),
        *(measure(k, "count_if", condition=_age(lo, hi)) for k, lo, hi in _AGE_EDGES),
        measure("escalated", "count_if", condition=_escalated),
        measure("unassigned", "count_if", condition=lambda ctx: T.c.user_id == UNASSIGNED_OWNER_ID),
    )


def open_tickets_source() -> ReportSource:
    return ReportSource(
        key="open_tickets",
        label_key="reports.sources.open_tickets",
        from_clause=_from,
        scope_column=col(T, "queue_id"),
        dimensions=(
            attribute_dim("queue", col(T, "queue_id"), "queue"),
            attribute_dim("state", col(T, "ticket_state_id"), "state"),
            dim(
                "state_type",
                "enum",
                col(STT, "name"),
                values="reports.values.state_type",
                enum=OPEN_TYPES,
            ),
            attribute_dim("priority", col(T, "ticket_priority_id"), "priority"),
            attribute_dim("type", col(T, "type_id"), "type"),
            attribute_dim("service", col(T, "service_id"), "service"),
            attribute_dim("sla", col(T, "sla_id"), "sla"),
            dim("owner", "id", col(T, "user_id"), labels="user"),
            dim("responsible", "id", col(T, "responsible_user_id"), labels="user"),
            dim("customer", "string", col(T, "customer_id"), labels="customer_company"),
            dim("customer_user", "string", col(T, "customer_user_id"), labels="customer_user"),
            dim("created", "date", col(T, "create_time")),
        ),
        measures=_measures(),
        base_where=_base_where,
        time_fields=("created",),
        default_time_field=None,
        requires_facts=False,
    )
