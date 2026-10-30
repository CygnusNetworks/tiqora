"""Source ``config_items``: one row per configuration item of the Tiqora CMDB (Mode B)."""

from __future__ import annotations

from typing import Final

from sqlalchemy import ColumnElement, FromClause

from tiqora.db.tiqora.models import TiqoraCmdbCatalog, TiqoraCmdbCi
from tiqora.reporting.semantic import BoolFn, CompileContext, ReportSource
from tiqora.reporting.sources._common import aliased, col, dim, measure

CI = aliased(TiqoraCmdbCi, "ci")
DS = aliased(TiqoraCmdbCatalog, "ds")
IS_ = aliased(TiqoraCmdbCatalog, "cis")

#: ``functionality`` values of the deployment / incident state catalogs (``tiqora.cmdb.seed``)
DEPLOYMENT_CATEGORIES: Final = ("preproductive", "productive", "postproductive")
INCIDENT_CATEGORIES: Final = ("operational", "warning", "incident")


def _from(ctx: CompileContext) -> FromClause:
    return CI.join(DS, DS.c.id == CI.c.deployment_state_id).join(
        IS_, IS_.c.id == CI.c.cur_incident_state_id
    )


def _is_incident_category(category: str) -> BoolFn:
    def cond(ctx: CompileContext) -> ColumnElement[bool]:
        return IS_.c.functionality == category

    return cond


def _is_productive(ctx: CompileContext) -> ColumnElement[bool]:
    return DS.c.functionality == "productive"


def config_items_source() -> ReportSource:
    return ReportSource(
        key="config_items",
        label_key="reports.sources.config_items",
        from_clause=_from,
        scope_column=col(CI, "class_id"),
        scope_kind="cmdb_class",
        availability="itsm_tiqora",
        dimensions=(
            dim("class", "id", col(CI, "class_id"), labels="cmdb_class", options="cmdb_class"),
            dim(
                "deployment_state",
                "id",
                col(CI, "deployment_state_id"),
                labels="cmdb_catalog",
                options="cmdb_deployment_state",
            ),
            dim(
                "incident_state",
                "id",
                col(CI, "cur_incident_state_id"),
                labels="cmdb_catalog",
                options="cmdb_incident_state",
            ),
            dim(
                "deployment_category",
                "enum",
                col(DS, "functionality"),
                values="reports.values.deployment_category",
                enum=DEPLOYMENT_CATEGORIES,
            ),
            dim(
                "incident_category",
                "enum",
                col(IS_, "functionality"),
                values="reports.values.incident_category",
                enum=INCIDENT_CATEGORIES,
            ),
            dim("created", "date", col(CI, "created_at")),
            dim("changed", "date", col(CI, "changed_at")),
            dim("created_by", "id", col(CI, "created_by"), labels="user"),
        ),
        measures=(
            measure("config_items", "count"),
            measure("cis_in_incident", "count_if", condition=_is_incident_category("incident")),
            measure("cis_in_warning", "count_if", condition=_is_incident_category("warning")),
            measure("productive_cis", "count_if", condition=_is_productive),
            measure("versions_avg", "avg", expr=col(CI, "version_count"), unit="number"),
        ),
        time_fields=("created", "changed"),
        default_time_field=None,
        requires_facts=False,
        common_fields={},
    )
