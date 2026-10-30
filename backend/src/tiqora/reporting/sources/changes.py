"""Source ``changes``: one row per change record of the Tiqora ITSM (Mode B).

``tiqora_itsm_change`` joined with the change ticket's report fact (queue, owner, customer, ...).
The fact join carries the queue scope and the archive rule; a change whose ticket has no fact row
yet is not counted. Both tables are Tiqora's own, so the JOIN stays inside one table family.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import ColumnElement, FromClause, and_, case, false, func

from tiqora.db.tiqora.models import TiqoraReportTicketFact
from tiqora.itsm.change.models import TiqoraItsmChange
from tiqora.itsm.change.phases import CHANGE_TYPES, FINAL_PHASES, OUTCOMES, PHASES
from tiqora.itsm.change.risk import RISK_LEVELS
from tiqora.reporting.semantic import BoolFn, CompileContext, ReportSource
from tiqora.reporting.sources._common import aliased, attribute_dim, col, dim, measure

__all__ = [
    "CHANGE_OUTCOMES",
    "CHANGE_STATUSES",
    "CHANGE_TYPES",
    "OPEN_EXCLUDED_PHASES",
    "PHASES",
    "RISK_LEVELS",
    "changes_source",
]

C = aliased(TiqoraItsmChange, "c")
F = aliased(TiqoraReportTicketFact, "f")

CHANGE_OUTCOMES: Final = OUTCOMES
#: the phases plus ``closed`` split by outcome (``closed`` itself stays for a filter on both)
CHANGE_STATUSES: Final = (*PHASES, *(f"closed_{o}" for o in CHANGE_OUTCOMES))
#: phases that are final; a rejected change can still be reworked, so it counts as open
OPEN_EXCLUDED_PHASES: Final = frozenset(FINAL_PHASES)


def _from(ctx: CompileContext) -> FromClause:
    return C.join(F, F.c.ticket_id == C.c.ticket_id)


def _status(ctx: CompileContext) -> ColumnElement[str]:
    whens = [
        (and_(C.c.phase == "closed", C.c.outcome == o), f"closed_{o}") for o in CHANGE_OUTCOMES
    ]
    return case(*whens, else_=C.c.phase)


def _risk(ctx: CompileContext) -> ColumnElement[str]:
    return func.coalesce(C.c.risk_override_level, C.c.risk_level)


def _outcome_is(*outcomes: str) -> BoolFn:
    def cond(ctx: CompileContext) -> ColumnElement[bool]:
        return C.c.outcome.in_(outcomes)

    return cond


def _has_outcome(ctx: CompileContext) -> ColumnElement[bool]:
    return C.c.outcome.is_not(None)


def _open(ctx: CompileContext) -> ColumnElement[bool]:
    return C.c.phase.not_in(sorted(OPEN_EXCLUDED_PHASES))


def _emergency(ctx: CompileContext) -> ColumnElement[bool]:
    return C.c.change_type == "emergency"


def _rejected(ctx: CompileContext) -> ColumnElement[bool]:
    return C.c.phase == "rejected"


def _late(ctx: CompileContext) -> ColumnElement[bool]:
    return and_(
        C.c.actual_end.is_not(None),
        C.c.planned_end.is_not(None),
        C.c.actual_end > C.c.planned_end,
    )


def changes_source() -> ReportSource:
    return ReportSource(
        key="changes",
        label_key="reports.sources.changes",
        from_clause=_from,
        scope_column=col(F, "queue_id"),
        availability="itsm_tiqora",
        dimensions=(
            dim(
                "phase",
                "enum",
                col(C, "phase"),
                values="reports.values.change_phase",
                enum=tuple(PHASES),
            ),
            dim(
                "outcome",
                "enum",
                col(C, "outcome"),
                values="reports.values.change_outcome",
                enum=CHANGE_OUTCOMES,
            ),
            dim(
                "change_status",
                "enum",
                _status,
                values="reports.values.change_status",
                enum=CHANGE_STATUSES,
            ),
            dim(
                "change_type",
                "enum",
                col(C, "change_type"),
                values="reports.values.change_type",
                enum=tuple(CHANGE_TYPES),
            ),
            dim(
                "risk_level",
                "enum",
                _risk,
                values="reports.values.risk_level",
                enum=tuple(RISK_LEVELS),
            ),
            attribute_dim("queue", col(F, "queue_id"), "queue"),
            attribute_dim("priority", col(F, "priority_id"), "priority"),
            attribute_dim("service", col(F, "service_id"), "service"),
            attribute_dim("sla", col(F, "sla_id"), "sla"),
            dim("owner", "id", col(F, "owner_id"), labels="user"),
            dim("created_by", "id", col(F, "created_by"), labels="user"),
            dim("customer", "string", col(F, "customer_id"), labels="customer_company"),
            dim("customer_user", "string", col(F, "customer_user"), labels="customer_user"),
            dim("created", "date", col(F, "created_at")),
            dim("closed", "date", col(F, "closed_at")),
            dim("planned_start", "date", col(C, "planned_start")),
            dim("actual_end", "date", col(C, "actual_end")),
        ),
        measures=(
            measure("changes", "count"),
            measure("open_changes", "count_if", condition=_open),
            measure("successful_changes", "count_if", condition=_outcome_is("successful")),
            measure("failed_changes", "count_if", condition=_outcome_is("failed", "backed_out")),
            measure(
                "change_success_rate",
                "rate",
                condition=_outcome_is("successful"),
                denominator=_has_outcome,
                unit="percent",
            ),
            measure("emergency_changes", "count_if", condition=_emergency),
            measure("rejected_changes", "count_if", condition=_rejected),
            measure("late_changes", "count_if", condition=_late),
        ),
        time_fields=("created", "planned_start", "actual_end", "closed"),
        default_time_field="created",
        requires_facts=True,
        archive_exclusion=lambda ctx: F.c.archived.is_(false()),
        common_fields={
            "queue": "queue",
            "priority": "priority",
            "service": "service",
            "sla": "sla",
            "owner": "owner",
            "customer": "customer",
            "customer_user": "customer_user",
        },
    )
