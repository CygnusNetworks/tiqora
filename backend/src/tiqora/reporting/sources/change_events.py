"""Source ``change_events``: one row per phase change of a change record (Mode B).

``tiqora_itsm_change_event`` (``event = 'phase'`` with a ``from_phase``: one row per phase change on every path; the
initial NULL -> draft row of creation is no change) joined
with the change record and the change ticket's report fact. The fact join carries the queue scope
(the ticket's current queue, as for ``changes``) and the archive rule. The event table's
``ticket_id`` is a cascading foreign key to the change, so both inner joins keep every event;
all three tables are Tiqora's own.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, FromClause, false

from tiqora.db.tiqora.models import TiqoraReportTicketFact
from tiqora.itsm.change.models import TiqoraItsmChange, TiqoraItsmChangeEvent
from tiqora.itsm.change.phases import CHANGE_TYPES, PHASES
from tiqora.reporting.semantic import CompileContext, ReportSource
from tiqora.reporting.sources._common import aliased, attribute_dim, col, dim, measure

__all__ = ["change_events_source"]

E = aliased(TiqoraItsmChangeEvent, "e")
C = aliased(TiqoraItsmChange, "c")
F = aliased(TiqoraReportTicketFact, "f")


def _from(ctx: CompileContext) -> FromClause:
    return E.join(C, C.c.ticket_id == E.c.ticket_id).join(F, F.c.ticket_id == E.c.ticket_id)


def _base_where(ctx: CompileContext) -> list[ColumnElement[bool]]:
    # creation writes a ``phase`` row too (``from_phase`` NULL -> draft): not a phase change
    return [E.c.event == "phase", E.c.from_phase.is_not(None)]


def change_events_source() -> ReportSource:
    return ReportSource(
        key="change_events",
        label_key="reports.sources.change_events",
        from_clause=_from,
        base_where=_base_where,
        scope_column=col(F, "queue_id"),
        availability="itsm_tiqora",
        dimensions=(
            dim("occurred", "date", col(E, "created_at")),
            dim(
                "from_phase",
                "enum",
                col(E, "from_phase"),
                values="reports.values.change_phase",
                enum=tuple(PHASES),
            ),
            dim(
                "to_phase",
                "enum",
                col(E, "to_phase"),
                values="reports.values.change_phase",
                enum=tuple(PHASES),
            ),
            dim(
                "change_type",
                "enum",
                col(C, "change_type"),
                values="reports.values.change_type",
                enum=tuple(CHANGE_TYPES),
            ),
            attribute_dim("queue", col(F, "queue_id"), "queue"),
            attribute_dim("priority", col(F, "priority_id"), "priority"),
            attribute_dim("service", col(F, "service_id"), "service"),
        ),
        measures=(
            measure("phase_changes", "count"),
            measure("changes", "count_distinct", expr=col(E, "ticket_id")),
        ),
        time_fields=("occurred",),
        default_time_field="occurred",
        requires_facts=True,
        archive_exclusion=lambda ctx: F.c.archived.is_(false()),
        common_fields={"queue": "queue", "priority": "priority", "service": "service"},
    )
