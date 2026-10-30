"""Source ``ci_tickets``: CI-ticket links of the Tiqora CMDB (Mode B), one row per stored link.

``tiqora_itsm_link`` rows with a CI end and a ticket end, in both directions (a pointed link
type keeps the orientation it was added with, an unpointed one has the CI first), joined with the
CI, its incident state and the ticket's report fact; the change record of a change ticket is
joined on. Link keys are free text (customers, companies ...), so the numeric ids are cast only
behind a ``CASE`` that tests the kind and the digits. Every table is Tiqora's own.

Two scopes apply: the ticket's current queue (``ro``, like every ticket report) and the CI's
class (``tiqora.cmdb.access.readable_class_ids``). A pair linked through two link types is one
ticket / one CI in every measure (``count_distinct``).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    BigInteger,
    ColumnElement,
    FromClause,
    and_,
    case,
    cast,
    false,
    null,
    select,
    union_all,
)
from sqlalchemy.sql.selectable import Subquery

from tiqora.db.tiqora.models import (
    TiqoraCmdbCatalog,
    TiqoraCmdbCi,
    TiqoraItsmLink,
    TiqoraReportTicketFact,
)
from tiqora.itsm.change.models import TiqoraItsmChange
from tiqora.itsm.change.phases import PHASES
from tiqora.reporting.itsm_roles import INCIDENT_ROLES, itsm_role_dim, role_type_ids
from tiqora.reporting.semantic import CompileContext, ReportSource
from tiqora.reporting.sources._common import (
    OPEN_TYPES,
    STATE_TYPES,
    aliased,
    attribute_dim,
    col,
    dim,
    flag,
    measure,
)
from tiqora.reporting.sources.config_items import INCIDENT_CATEGORIES
from tiqora.reporting.sql import digits_only

__all__ = ["ci_tickets_source"]

LK = aliased(TiqoraItsmLink, "lk")
CI = aliased(TiqoraCmdbCi, "ci")
IS_ = aliased(TiqoraCmdbCatalog, "cis")
F = aliased(TiqoraReportTicketFact, "f")
C = aliased(TiqoraItsmChange, "c")


def _key_int(kind_col: ColumnElement[Any], key_col: ColumnElement[Any], kind: str) -> Any:
    """``BIGINT`` of the key when it is of ``kind`` and a plain number, else ``NULL``.

    Never a bare ``CAST(key AS BIGINT)``: PostgreSQL raises on the first non-numeric key, and a
    ``WHERE kind = ...`` does not protect the cast (the planner may evaluate it first).
    """
    return case((kind_col == kind, case((digits_only(key_col), cast(key_col, BigInteger)))))


def _links() -> Subquery:
    lk = LK.c

    def arm(ci_side: str, ticket_side: str) -> Any:
        return select(
            _key_int(lk[f"{ci_side}_kind"], lk[f"{ci_side}_key"], "ci").label("ci_id"),
            _key_int(lk[f"{ticket_side}_kind"], lk[f"{ticket_side}_key"], "ticket").label(
                "ticket_id"
            ),
            lk.link_type.label("link_type"),
        ).where(lk[f"{ci_side}_kind"] == "ci", lk[f"{ticket_side}_kind"] == "ticket")

    return union_all(arm("source", "target"), arm("target", "source")).subquery("l")


L = _links()


def _from(ctx: CompileContext) -> FromClause:
    return (
        L.join(CI, CI.c.id == L.c.ci_id)
        .join(F, F.c.ticket_id == L.c.ticket_id)
        .join(IS_, IS_.c.id == CI.c.cur_incident_state_id)
        .outerjoin(C, C.c.ticket_id == F.c.ticket_id)
    )


def _is_change(ctx: CompileContext) -> ColumnElement[bool]:
    return C.c.ticket_id.is_not(None)


def _ticket_if(*conditions: ColumnElement[bool]) -> ColumnElement[Any]:
    return case((and_(*conditions), F.c.ticket_id))


def _open_tickets(ctx: CompileContext) -> ColumnElement[Any]:
    return _ticket_if(F.c.state_type.in_(OPEN_TYPES))


def _changes(ctx: CompileContext) -> ColumnElement[Any]:
    return _ticket_if(_is_change(ctx))


def _incidents(ctx: CompileContext) -> ColumnElement[Any]:
    ids = role_type_ids(ctx, INCIDENT_ROLES)
    if not ids:
        return cast(null(), BigInteger)
    return _ticket_if(F.c.type_id.in_(ids))


def _open_incidents(ctx: CompileContext) -> ColumnElement[Any]:
    ids = role_type_ids(ctx, INCIDENT_ROLES)
    if not ids:
        return cast(null(), BigInteger)
    return _ticket_if(F.c.type_id.in_(ids), F.c.state_type.in_(OPEN_TYPES))


def ci_tickets_source() -> ReportSource:
    return ReportSource(
        key="ci_tickets",
        label_key="reports.sources.ci_tickets",
        from_clause=_from,
        scope_column=col(F, "queue_id"),
        extra_scopes=(("cmdb_class", col(CI, "class_id")),),
        availability="itsm_tiqora",
        dimensions=(
            dim("ci", "id", col(CI, "id"), labels="cmdb_ci"),
            dim(
                "ci_class",
                "id",
                col(CI, "class_id"),
                labels="cmdb_class",
                options="cmdb_class",
            ),
            dim(
                "ci_deployment_state",
                "id",
                col(CI, "deployment_state_id"),
                labels="cmdb_catalog",
                options="cmdb_deployment_state",
            ),
            dim(
                "ci_incident_state",
                "id",
                col(CI, "cur_incident_state_id"),
                labels="cmdb_catalog",
                options="cmdb_incident_state",
            ),
            dim(
                "ci_incident_category",
                "enum",
                col(IS_, "functionality"),
                values="reports.values.incident_category",
                enum=INCIDENT_CATEGORIES,
            ),
            dim("link_type", "string", lambda ctx: L.c.link_type),
            attribute_dim("ticket_type", col(F, "type_id"), "type"),
            itsm_role_dim("itsm_role", col(F, "type_id")),
            dim(
                "state_type",
                "enum",
                col(F, "state_type"),
                values="reports.values.state_type",
                enum=STATE_TYPES,
            ),
            attribute_dim("queue", col(F, "queue_id"), "queue"),
            attribute_dim("priority", col(F, "priority_id"), "priority"),
            attribute_dim("service", col(F, "service_id"), "service"),
            dim(
                "change_phase",
                "enum",
                col(C, "phase"),
                values="reports.values.change_phase",
                enum=tuple(PHASES),
            ),
            dim("created", "date", col(F, "created_at")),
            dim("closed", "date", col(F, "closed_at")),
        ),
        measures=(
            measure("tickets", "count_distinct", expr=col(F, "ticket_id")),
            measure("cis", "count_distinct", expr=col(CI, "id")),
            measure("open_tickets", "count_distinct", expr=_open_tickets),
            measure("changes", "count_distinct", expr=_changes),
            measure("incidents", "count_distinct", expr=_incidents),
            measure("open_incidents", "count_distinct", expr=_open_incidents),
        ),
        filter_fields=(flag("is_change", _is_change),),
        time_fields=("created", "closed"),
        default_time_field="created",
        requires_facts=True,
        archive_exclusion=lambda ctx: F.c.archived.is_(false()),
        common_fields={"queue": "queue", "priority": "priority", "service": "service"},
    )
