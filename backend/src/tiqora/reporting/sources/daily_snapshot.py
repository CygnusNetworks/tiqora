"""Source ``daily_snapshot``: end-of-day open stock per queue/state type/priority."""

from __future__ import annotations

from sqlalchemy import FromClause

from tiqora.db.tiqora.models import TiqoraReportDailySnapshot
from tiqora.reporting.semantic import CompileContext, ReportSource
from tiqora.reporting.sources._common import (
    OPEN_TYPES,
    aliased,
    attribute_dim,
    col,
    dim,
    measure,
)

S = aliased(TiqoraReportDailySnapshot, "s")


def _from(ctx: CompileContext) -> FromClause:
    return S


def daily_snapshot_source() -> ReportSource:
    buckets = ("age_0_1d", "age_1_3d", "age_3_7d", "age_7_30d", "age_30d_plus")
    return ReportSource(
        key="daily_snapshot",
        label_key="reports.sources.daily_snapshot",
        from_clause=_from,
        scope_column=col(S, "queue_id"),
        dimensions=(
            # DATE column compared with datetime bounds is not portable (asyncpg):
            # the window (time_range) is the way to restrict the snapshot date.
            dim("snapshot", "local_date", col(S, "snapshot_date"), stock=True, filterable=False),
            attribute_dim("queue", col(S, "queue_id"), "queue"),
            dim(
                "state_type",
                "enum",
                col(S, "state_type"),
                values="reports.values.state_type",
                enum=OPEN_TYPES,
            ),
            attribute_dim("priority", col(S, "priority_id"), "priority"),
        ),
        measures=(
            measure("open_tickets", "sum", expr=col(S, "open_count"), stock=True),
            *(measure(b, "sum", expr=col(S, b), stock=True) for b in buckets),
        ),
        time_fields=("snapshot",),
        default_time_field="snapshot",
        common_fields={"queue": "queue", "state_type": "state_type", "priority": "priority"},
    )
