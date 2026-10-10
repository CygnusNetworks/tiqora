"""Source ``accounted_time``: Znuny ``time_accounting`` bookings (integer join to ``ticket``).

Archived tickets are kept (historically correct totals), unlike Znuny's
TicketAccountedTime default when ArchiveSystem is on.
"""

from __future__ import annotations

from sqlalchemy import FromClause

from tiqora.db.legacy.misc import TimeAccounting
from tiqora.db.legacy.ticket import Ticket
from tiqora.reporting.semantic import CompileContext, ReportSource
from tiqora.reporting.sources._common import aliased, attribute_dim, col, dim, measure

TA = aliased(TimeAccounting, "ta")
T = aliased(Ticket, "t")


def _from(ctx: CompileContext) -> FromClause:
    return TA.join(T, T.c.id == TA.c.ticket_id)


def accounted_time_source() -> ReportSource:
    return ReportSource(
        key="accounted_time",
        label_key="reports.sources.accounted_time",
        from_clause=_from,
        scope_column=col(T, "queue_id"),
        dimensions=(
            dim("agent", "id", col(TA, "create_by"), labels="user"),
            dim("customer", "string", col(T, "customer_id"), labels="customer_company"),
            dim("customer_user", "string", col(T, "customer_user_id"), labels="customer_user"),
            attribute_dim("queue", col(T, "queue_id"), "queue"),
            attribute_dim("type", col(T, "type_id"), "type"),
            attribute_dim("service", col(T, "service_id"), "service"),
            dim("ticket", "id", col(T, "id"), labels="ticket"),
            dim("booked", "date", col(TA, "create_time")),
            dim("booked_weekday", "weekday", col(TA, "create_time")),
        ),
        measures=(
            measure("time_units", "sum", expr=col(TA, "time_unit"), unit="units"),
            measure("bookings", "count"),
            measure("tickets", "count_distinct", expr=col(TA, "ticket_id")),
        ),
        time_fields=("booked",),
        default_time_field="booked",
        requires_facts=False,
        common_fields={
            "queue": "queue",
            "type": "type",
            "service": "service",
            "customer": "customer",
            "customer_user": "customer_user",
        },
    )
