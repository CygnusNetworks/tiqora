"""Tiqora CMDB (Mode B): CI tables, ITSM links, general catalog seeds and add-on views.

Revision ID: 20261010_0079
Revises: 20261010_0077
Create Date: 2026-10-12

Creates the ``tiqora_cmdb_*`` tables and ``tiqora_itsm_link``. ``tiqora_itsm_link.link_type``
references ``tiqora_itsm_link_type.name`` (migration 20261010_0077, owned by the ITSM core);
this migration neither creates nor seeds link types (``ensure_default_link_types`` does).
Seeds the general-catalog rows (deployment states, incident states, yes/no) as literals and
creates the two add-on views ``tiqora_v1_itsm_ci`` / ``tiqora_v1_itsm_ci_links``. Each
``CREATE VIEW`` is tolerant of a missing privilege (MariaDB 1142, PostgreSQL 42501): it logs
``cmdb_views_skipped`` and carries on, ``POST /admin/cmdb/seed-defaults`` retries later.

Later migrations that change a column the views read must drop and recreate both views.
Timestamps are written by the application as naive UTC; the seed rows use a fixed time.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import DBAPIError

revision: str = "20261010_0079"
down_revision: str | None = "20261010_0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_NOW = datetime(2026, 10, 12)

_MEDIUM_TEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")
_LONG_BLOB = sa.LargeBinary().with_variant(mysql.LONGBLOB(), "mysql", "mariadb")

_DEPLOYMENT = "ITSM::ConfigItem::DeploymentState"
_INCIDENT = "ITSM::Core::IncidentState"
_YES_NO = "ITSM::ConfigItem::YesNo"

# (catalog_class, item_key, name, functionality, position) -- equal to
# tiqora.cmdb.seed.DEFAULT_CATALOG_ROWS (a test compares them)
_CATALOG_ROWS: tuple[tuple[str, str, str, str | None, int], ...] = (
    (_DEPLOYMENT, "expired", "Expired", "postproductive", 10),
    (_DEPLOYMENT, "inactive", "Inactive", "postproductive", 20),
    (_DEPLOYMENT, "maintenance", "Maintenance", "productive", 30),
    (_DEPLOYMENT, "pilot", "Pilot", "preproductive", 40),
    (_DEPLOYMENT, "planned", "Planned", "preproductive", 50),
    (_DEPLOYMENT, "production", "Production", "productive", 60),
    (_DEPLOYMENT, "repair", "Repair", "productive", 70),
    (_DEPLOYMENT, "retired", "Retired", "postproductive", 80),
    (_DEPLOYMENT, "review", "Review", "productive", 90),
    (_DEPLOYMENT, "test_qa", "Test/QA", "preproductive", 100),
    (_INCIDENT, "operational", "Operational", "operational", 10),
    (_INCIDENT, "warning", "Warning", "warning", 20),
    (_INCIDENT, "incident", "Incident", "incident", 30),
    (_YES_NO, "yes", "Yes", None, 10),
    (_YES_NO, "no", "No", None, 20),
)

# equal to tiqora.cmdb.views.VIEW_DDL (a test compares them); ``{INT}`` = dialect integer cast
_VIEWS: dict[str, str] = {
    "tiqora_v1_itsm_ci": (
        "CREATE VIEW tiqora_v1_itsm_ci AS "
        "SELECT ci.id AS ci_id, ci.number AS number, ci.name AS name, "
        "cl.id AS class_id, cl.class_key AS class_key, cl.name AS class_name, "
        "cl.group_id AS class_group_id, "
        "dep.name AS deployment_state, dep.functionality AS deployment_functionality, "
        "inc.name AS incident_state, inc.functionality AS incident_functionality, "
        "ci.changed_at AS changed, cl.portal_visible AS class_portal_visible "
        "FROM tiqora_cmdb_ci ci "
        "JOIN tiqora_cmdb_class cl ON cl.id = ci.class_id "
        "JOIN tiqora_cmdb_catalog dep ON dep.id = ci.deployment_state_id "
        "JOIN tiqora_cmdb_catalog inc ON inc.id = ci.cur_incident_state_id"
    ),
    "tiqora_v1_itsm_ci_links": (
        "CREATE VIEW tiqora_v1_itsm_ci_links AS "
        "SELECT CAST(l.source_key AS {INT}) AS ci_id, l.target_kind AS object_kind, "
        "l.target_key AS object_key, l.link_type AS link_type, 'source' AS direction, "
        "l.created_at AS created "
        "FROM tiqora_itsm_link l WHERE l.source_kind = 'ci' "
        "UNION ALL "
        "SELECT CAST(l.target_key AS {INT}), l.source_kind, l.source_key, l.link_type, "
        "'target', l.created_at "
        "FROM tiqora_itsm_link l WHERE l.target_kind = 'ci'"
    ),
}
_VIEW_ORDER = ("tiqora_v1_itsm_ci", "tiqora_v1_itsm_ci_links")


def _is_privilege_error(exc: BaseException) -> bool:
    orig = getattr(exc, "orig", exc)
    args = getattr(orig, "args", ())
    if args and isinstance(args[0], int) and args[0] in (1142, 1044, 1227):
        return True
    return any(getattr(orig, a, None) == "42501" for a in ("sqlstate", "pgcode"))


def _create_views() -> None:
    bind = op.get_bind()
    name = bind.dialect.name
    int_type = "SIGNED" if name in ("mysql", "mariadb") else "INTEGER"
    # MariaDB's implicit DDL commit destroys savepoints; only PostgreSQL needs (and has) one
    use_savepoint = name not in ("mysql", "mariadb")
    for view in _VIEW_ORDER:
        savepoint = bind.begin_nested() if use_savepoint else None
        try:
            bind.execute(sa.text(f"DROP VIEW IF EXISTS {view}"))
            bind.execute(sa.text(_VIEWS[view].replace("{INT}", int_type)))
        except DBAPIError as exc:
            if savepoint is not None:
                savepoint.rollback()
            if not _is_privilege_error(exc):
                raise
            logger.warning(
                "cmdb_views_skipped: CREATE VIEW-Recht fehlt, Sicht %s nicht angelegt", view
            )
            return
        else:
            if savepoint is not None:
                savepoint.commit()


def _ts(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(), nullable=False)


def upgrade() -> None:
    catalog = op.create_table(
        "tiqora_cmdb_catalog",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("catalog_class", sa.String(100), nullable=False),
        sa.Column("item_key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("functionality", sa.String(50), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("comment", sa.String(250), nullable=True),
        _ts("created_at"),
        _ts("changed_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("catalog_class", "item_key", name="uq_tiqora_cmdb_catalog_key"),
    )
    op.create_index(
        "ix_tiqora_cmdb_catalog_class", "tiqora_cmdb_catalog", ["catalog_class", "position"]
    )

    op.create_table(
        "tiqora_cmdb_class",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("class_key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("icon", sa.String(50), nullable=True),
        sa.Column("number_prefix", sa.String(20), nullable=False),
        sa.Column("number_counter", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("current_definition_id", sa.BigInteger(), nullable=True),
        sa.Column("portal_visible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Integer(), nullable=False),
        _ts("created_at"),
        sa.Column("changed_by", sa.Integer(), nullable=False),
        _ts("changed_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("class_key", name="uq_tiqora_cmdb_class_key"),
    )

    op.create_table(
        "tiqora_cmdb_class_definition",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("attributes", _MEDIUM_TEXT, nullable=False),
        sa.Column("comment", sa.String(500), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["class_id"],
            ["tiqora_cmdb_class.id"],
            name="fk_tiqora_cmdb_class_definition_class",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("class_id", "version", name="uq_tiqora_cmdb_class_definition_version"),
    )

    op.create_table(
        "tiqora_cmdb_ci",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("number", sa.String(50), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(250), nullable=False),
        sa.Column("deployment_state_id", sa.Integer(), nullable=False),
        sa.Column("incident_state_id", sa.Integer(), nullable=False),
        sa.Column("cur_incident_state_id", sa.Integer(), nullable=False),
        sa.Column("current_version_id", sa.BigInteger(), nullable=True),
        sa.Column("version_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Integer(), nullable=False),
        _ts("created_at"),
        sa.Column("changed_by", sa.Integer(), nullable=False),
        _ts("changed_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["class_id"], ["tiqora_cmdb_class.id"], name="fk_tiqora_cmdb_ci_class"
        ),
        sa.ForeignKeyConstraint(
            ["deployment_state_id"],
            ["tiqora_cmdb_catalog.id"],
            name="fk_tiqora_cmdb_ci_deployment_state",
        ),
        sa.ForeignKeyConstraint(
            ["incident_state_id"],
            ["tiqora_cmdb_catalog.id"],
            name="fk_tiqora_cmdb_ci_incident_state",
        ),
        sa.ForeignKeyConstraint(
            ["cur_incident_state_id"],
            ["tiqora_cmdb_catalog.id"],
            name="fk_tiqora_cmdb_ci_cur_incident_state",
        ),
        sa.UniqueConstraint("number", name="uq_tiqora_cmdb_ci_number"),
    )
    op.create_index("ix_tiqora_cmdb_ci_class_name", "tiqora_cmdb_ci", ["class_id", "name"])
    op.create_index("ix_tiqora_cmdb_ci_name", "tiqora_cmdb_ci", ["name"])
    op.create_index("ix_tiqora_cmdb_ci_depl", "tiqora_cmdb_ci", ["deployment_state_id"])
    op.create_index("ix_tiqora_cmdb_ci_cur_inci", "tiqora_cmdb_ci", ["cur_incident_state_id"])
    op.create_index("ix_tiqora_cmdb_ci_changed", "tiqora_cmdb_ci", ["changed_at"])

    op.create_table(
        "tiqora_cmdb_ci_version",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ci_id", sa.BigInteger(), nullable=False),
        sa.Column("definition_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(250), nullable=False),
        sa.Column("deployment_state_id", sa.Integer(), nullable=False),
        sa.Column("incident_state_id", sa.Integer(), nullable=False),
        sa.Column("attributes", _MEDIUM_TEXT, nullable=False),
        sa.Column("change_note", sa.String(500), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["ci_id"],
            ["tiqora_cmdb_ci.id"],
            name="fk_tiqora_cmdb_ci_version_ci",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["tiqora_cmdb_class_definition.id"],
            name="fk_tiqora_cmdb_ci_version_definition",
        ),
    )
    op.create_index("ix_tiqora_cmdb_ci_version_ci", "tiqora_cmdb_ci_version", ["ci_id", "id"])

    op.create_table(
        "tiqora_cmdb_ci_version_archive",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("ci_id", sa.BigInteger(), nullable=False),
        sa.Column("definition_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(250), nullable=False),
        sa.Column("deployment_state_id", sa.Integer(), nullable=False),
        sa.Column("incident_state_id", sa.Integer(), nullable=False),
        sa.Column("attributes", _MEDIUM_TEXT, nullable=False),
        sa.Column("change_note", sa.String(500), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        _ts("created_at"),
        _ts("archived_at"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tiqora_cmdb_ci_version_archive_ci", "tiqora_cmdb_ci_version_archive", ["ci_id", "id"]
    )

    op.create_table(
        "tiqora_cmdb_ci_attr",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ci_id", sa.BigInteger(), nullable=False),
        sa.Column("attr_path", sa.String(200), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("value_text", sa.String(500), nullable=True),
        sa.Column("value_key", sa.String(191), nullable=True),
        sa.Column("value_num", sa.Numeric(30, 6), nullable=True),
        sa.Column("value_date", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["ci_id"], ["tiqora_cmdb_ci.id"], name="fk_tiqora_cmdb_ci_attr_ci", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_tiqora_cmdb_ci_attr_key", "tiqora_cmdb_ci_attr", ["attr_path", "value_key"])
    op.create_index("ix_tiqora_cmdb_ci_attr_num", "tiqora_cmdb_ci_attr", ["attr_path", "value_num"])
    op.create_index(
        "ix_tiqora_cmdb_ci_attr_date", "tiqora_cmdb_ci_attr", ["attr_path", "value_date"]
    )
    op.create_index("ix_tiqora_cmdb_ci_attr_ci", "tiqora_cmdb_ci_attr", ["ci_id"])
    op.create_index("ix_tiqora_cmdb_ci_attr_value", "tiqora_cmdb_ci_attr", ["value_key"])

    op.create_table(
        "tiqora_cmdb_ci_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ci_id", sa.BigInteger(), nullable=False),
        sa.Column("event", sa.String(50), nullable=False),
        sa.Column("detail", sa.String(1000), nullable=False),
        sa.Column("version_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tiqora_cmdb_ci_history_ci", "tiqora_cmdb_ci_history", ["ci_id", "id"])

    op.create_table(
        "tiqora_cmdb_attachment",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ci_id", sa.BigInteger(), nullable=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content", _LONG_BLOB, nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["ci_id"],
            ["tiqora_cmdb_ci.id"],
            name="fk_tiqora_cmdb_attachment_ci",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_tiqora_cmdb_attachment_ci", "tiqora_cmdb_attachment", ["ci_id"])

    op.create_table(
        "tiqora_itsm_link",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_kind", sa.String(20), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("target_kind", sa.String(20), nullable=False),
        sa.Column("target_key", sa.String(100), nullable=False),
        sa.Column("link_type", sa.String(50), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["link_type"],
            ["tiqora_itsm_link_type.name"],
            name="fk_tiqora_itsm_link_link_type",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "source_kind",
            "source_key",
            "target_kind",
            "target_key",
            "link_type",
            name="uq_tiqora_itsm_link",
        ),
    )
    op.create_index("ix_tiqora_itsm_link_source", "tiqora_itsm_link", ["source_kind", "source_key"])
    op.create_index("ix_tiqora_itsm_link_target", "tiqora_itsm_link", ["target_kind", "target_key"])

    op.create_table(
        "tiqora_cmdb_import_template",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("file_format", sa.String(10), nullable=False),
        sa.Column("options", _MEDIUM_TEXT, nullable=False),
        sa.Column("mapping", _MEDIUM_TEXT, nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        _ts("created_at"),
        _ts("changed_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["class_id"],
            ["tiqora_cmdb_class.id"],
            name="fk_tiqora_cmdb_import_template_class",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_tiqora_cmdb_import_template_owner", "tiqora_cmdb_import_template", ["owner_user_id"]
    )

    op.create_table(
        "tiqora_cmdb_saved_search",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("criteria", _MEDIUM_TEXT, nullable=False),
        sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.false()),
        _ts("created_at"),
        _ts("changed_at"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tiqora_cmdb_saved_search_owner", "tiqora_cmdb_saved_search", ["owner_user_id"]
    )

    op.bulk_insert(
        catalog,
        [
            {
                "catalog_class": cls,
                "item_key": key,
                "name": name,
                "functionality": functionality,
                "position": position,
                "valid": True,
                "comment": None,
                "created_at": _NOW,
                "changed_at": _NOW,
            }
            for cls, key, name, functionality, position in _CATALOG_ROWS
        ],
    )
    _create_views()


def downgrade() -> None:
    bind = op.get_bind()
    for view in reversed(_VIEW_ORDER):
        bind.execute(sa.text(f"DROP VIEW IF EXISTS {view}"))
    for table in (
        "tiqora_cmdb_saved_search",
        "tiqora_cmdb_import_template",
        "tiqora_itsm_link",
        "tiqora_cmdb_attachment",
        "tiqora_cmdb_ci_history",
        "tiqora_cmdb_ci_attr",
        "tiqora_cmdb_ci_version_archive",
        "tiqora_cmdb_ci_version",
        "tiqora_cmdb_ci",
        "tiqora_cmdb_class_definition",
        "tiqora_cmdb_class",
        "tiqora_cmdb_catalog",
    ):
        op.drop_table(table)
