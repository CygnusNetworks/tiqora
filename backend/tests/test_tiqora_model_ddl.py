"""DDL the ORM emits for ``tiqora_*`` tables must match what the migrations create.

Test and production databases are not built the same way: tests (and, as it
turned out, production) use ``TiqoraBase.metadata.create_all``, while the
Alembic chain builds tables from explicit ``op.create_table`` calls. Where the
two disagree, the disagreement only shows up in production.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.schema import CreateTable

from tiqora.db.tiqora.models import TiqoraUserAuthConfig


def _column_ddl(table: object, column: str, dialect: object) -> str:
    ddl = str(CreateTable(table).compile(dialect=dialect))  # type: ignore[arg-type]
    return next(line.strip() for line in ddl.splitlines() if line.strip().startswith(column))


def test_auth_config_user_id_does_not_generate_ids() -> None:
    """``tiqora_user_auth_config.user_id`` mirrors ``users.id`` — it must never
    invent one.

    SQLAlchemy defaults a single-column integer primary key to
    ``autoincrement="auto"``, which emitted ``AUTO_INCREMENT`` on MariaDB and
    ``SERIAL`` on PostgreSQL. An insert that omitted ``user_id`` would then
    silently create an auth-config row for a non-existent agent, and the
    table's counter tracked ``users`` closely enough to look like a second
    parked ID band during unrelated debugging.

    Migration ``20260721_0014`` already creates the column as a plain integer,
    so this only ever diverged for metadata-built databases.
    """
    table = TiqoraUserAuthConfig.__table__
    assert "AUTO_INCREMENT" not in _column_ddl(table, "user_id", mysql.dialect())
    assert "SERIAL" not in _column_ddl(table, "user_id", postgresql.dialect())


def test_avatar_data_fits_more_than_64_kib_on_mariadb() -> None:
    """``tiqora_avatar.data`` holds 256 px PNGs that can exceed plain
    ``BLOB``'s 64 KiB — MariaDB needs ``MEDIUMBLOB`` (same as migration
    ``20261007_0060``); PostgreSQL's ``BYTEA`` has no such limit."""
    from tiqora.db.tiqora.models import TiqoraAvatar

    table = TiqoraAvatar.__table__
    assert "MEDIUMBLOB" in _column_ddl(table, "data", mysql.dialect())
    assert "BYTEA" in _column_ddl(table, "data", postgresql.dialect())


def test_job_file_data_and_result_fit_large_payloads_on_mariadb() -> None:
    """Job results (up to 1 MB of JSON) need ``MEDIUMTEXT`` and file blobs
    (up to 200 MiB) ``LONGBLOB`` on MariaDB, same as the jobs migration."""
    from tiqora.db.tiqora.models import TiqoraJob, TiqoraJobFile

    assert "LONGBLOB" in _column_ddl(TiqoraJobFile.__table__, "data", mysql.dialect())
    assert "BYTEA" in _column_ddl(TiqoraJobFile.__table__, "data", postgresql.dialect())
    assert "MEDIUMTEXT" in _column_ddl(TiqoraJob.__table__, "result", mysql.dialect())
    assert "MEDIUMTEXT" in _column_ddl(TiqoraJob.__table__, "payload", mysql.dialect())
    assert "TEXT" in _column_ddl(TiqoraJob.__table__, "payload", postgresql.dialect())


def test_revision_config_fits_large_dumps_on_mariadb() -> None:
    """Process/ACL dumps exceed 64 KiB: ``config`` is ``MEDIUMTEXT`` on MariaDB, like the
    deployed-revisions migration; PostgreSQL ``TEXT`` is unbounded."""
    from tiqora.db.tiqora.models import TiqoraAclRevision, TiqoraProcessRevision

    for model in (TiqoraProcessRevision, TiqoraAclRevision):
        assert "MEDIUMTEXT" in _column_ddl(model.__table__, "config", mysql.dialect())
        assert (
            _column_ddl(model.__table__, "config", postgresql.dialect()) == "config TEXT NOT NULL,"
        )
        assert "VARCHAR(64) NOT NULL" in _column_ddl(model.__table__, "checksum", mysql.dialect())


def test_report_fact_columns_ddl() -> None:
    from tiqora.db.tiqora.models import (
        TiqoraReportDailySnapshot,
        TiqoraReportState,
        TiqoraReportTicketFact,
    )

    fact = TiqoraReportTicketFact.__table__
    assert "NUMERIC(12, 2)" in _column_ddl(fact, "accounted_time", postgresql.dialect())
    assert "NUMERIC(12, 2)" in _column_ddl(fact, "accounted_time", mysql.dialect())
    pk = [c.name for c in TiqoraReportDailySnapshot.__table__.primary_key.columns]
    assert pk == ["snapshot_date", "queue_id", "state_type", "priority_id"]
    for dialect in (mysql.dialect(), postgresql.dialect()):
        token = _column_ddl(TiqoraReportState.__table__, "backfill_run_token", dialect)
        assert "VARCHAR(36)" in token and "NOT NULL" not in token
    migration = (
        Path(__file__).parent.parent / "alembic/versions_tiqora/20261009_0063_reporting_facts.py"
    ).read_text()
    assert 'sa.Column("backfill_run_token", sa.String(36), nullable=True)' in migration
    assert "MEDIUMTEXT" in _column_ddl(TiqoraReportState.__table__, "gap_ids", mysql.dialect())
    assert "MEDIUMTEXT" not in _column_ddl(
        TiqoraReportState.__table__, "gap_ids", postgresql.dialect()
    )


def test_report_ticket_ids_do_not_generate_ids() -> None:
    """Fact and dirty ``ticket_id`` mirror ``ticket.id``; an insert that forgot it
    must fail instead of inventing an id (same trap as ``tiqora_user_auth_config``)."""
    from tiqora.db.tiqora.models import (
        TiqoraReportDailySnapshot,
        TiqoraReportDirtyTicket,
        TiqoraReportTicketFact,
    )

    for table, column in (
        (TiqoraReportTicketFact.__table__, "ticket_id"),
        (TiqoraReportDirtyTicket.__table__, "ticket_id"),
        (TiqoraReportDailySnapshot.__table__, "queue_id"),
        (TiqoraReportDailySnapshot.__table__, "priority_id"),
    ):
        assert "AUTO_INCREMENT" not in _column_ddl(table, column, mysql.dialect())
        for marker in ("SERIAL", "GENERATED"):
            assert marker not in _column_ddl(table, column, postgresql.dialect())


def test_report_dirty_queued_at_has_microseconds_on_mariadb() -> None:
    """``clear_dirty`` compares ``queued_at`` with a taken-at time; whole-second
    ``DATETIME`` would delete a mark made in the same second."""
    from tiqora.db.tiqora.models import TiqoraReportDirtyTicket

    table = TiqoraReportDirtyTicket.__table__
    assert "DATETIME(6)" in _column_ddl(table, "queued_at", mysql.dialect())
    assert "DATETIME(6)" not in _column_ddl(table, "queued_at", postgresql.dialect())


def test_identity_metadata_xml_fits_large_documents_on_mariadb() -> None:
    """SAML IdP metadata can exceed 64 KiB: ``MEDIUMTEXT`` on MariaDB, plain
    ``TEXT`` on PostgreSQL (same as migration ``20261009_0065``)."""
    from tiqora.db.tiqora.models import TiqoraIdentityProvider

    table = TiqoraIdentityProvider.__table__
    assert "MEDIUMTEXT" in _column_ddl(table, "metadata_xml", mysql.dialect())
    assert "MEDIUMTEXT" not in _column_ddl(table, "metadata_xml", postgresql.dialect())
    # ``config`` carries pasted ``idp_metadata_xml`` (parser accepts up to 5 MB).
    assert "MEDIUMTEXT" in _column_ddl(table, "config", mysql.dialect())
    assert "MEDIUMTEXT" not in _column_ddl(table, "config", postgresql.dialect())
    migration = (
        Path(__file__).parent.parent / "alembic/versions_tiqora/20261009_0065_identity_providers.py"
    ).read_text()
    assert migration.count('with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")') == 2


def test_saved_report_link_ids_do_not_generate_ids() -> None:
    """``report_id`` / ``saved_search_id`` mirror the parent row's id; being half of a
    composite key they must never turn into ``AUTO_INCREMENT`` / ``SERIAL``."""
    from tiqora.db.tiqora.models import (
        TiqoraReportGroup,
        TiqoraSavedSearchGroup,
        TiqoraSavedSearchPref,
    )

    for model, column in (
        (TiqoraReportGroup, "report_id"),
        (TiqoraSavedSearchGroup, "saved_search_id"),
        (TiqoraSavedSearchPref, "saved_search_id"),
    ):
        assert model.__table__.c[column].autoincrement is False
        assert "AUTO_INCREMENT" not in _column_ddl(model.__table__, column, mysql.dialect())
        for marker in ("SERIAL", "GENERATED"):
            assert marker not in _column_ddl(model.__table__, column, postgresql.dialect())


def test_saved_report_json_columns_fit_large_documents_on_mariadb() -> None:
    from tiqora.db.tiqora.models import TiqoraReport, TiqoraSavedSearch

    assert "MEDIUMTEXT" in _column_ddl(TiqoraReport.__table__, "definition", mysql.dialect())
    assert "MEDIUMTEXT" in _column_ddl(TiqoraSavedSearch.__table__, "criteria", mysql.dialect())
    assert "MEDIUMTEXT" not in _column_ddl(
        TiqoraReport.__table__, "definition", postgresql.dialect()
    )


def test_webchat_ddl() -> None:
    from tiqora.db.tiqora.models import (
        TiqoraChatAgentState,
        TiqoraChatMessage,
        TiqoraChatSession,
    )

    assert "AUTO_INCREMENT" not in _column_ddl(
        TiqoraChatAgentState.__table__, "user_id", mysql.dialect()
    )
    assert "VARCHAR(32)" in _column_ddl(TiqoraChatSession.__table__, "id", mysql.dialect())
    uniques = {
        tuple(c.name for c in ix.columns) for ix in TiqoraChatMessage.__table__.indexes if ix.unique
    }
    assert ("session_id", "seq") in uniques and ("session_id", "client_id") in uniques


def test_report_schedule_ddl() -> None:
    """Report schedules (migration 0074): run files and mail bodies exceed 64 KiB on MariaDB;
    the item's composite key mirrors the schedule id and must never generate ids."""
    from tiqora.db.tiqora.models import (
        TiqoraReportRun,
        TiqoraReportRunFile,
        TiqoraReportSchedule,
        TiqoraReportScheduleItem,
        TiqoraReportScheduleRecipient,
    )

    run_file = TiqoraReportRunFile.__table__
    assert "LONGBLOB" in _column_ddl(run_file, "data", mysql.dialect())
    assert "BYTEA" in _column_ddl(run_file, "data", postgresql.dialect())
    schedule = TiqoraReportSchedule.__table__
    assert "MEDIUMTEXT" in _column_ddl(schedule, "body", mysql.dialect())
    assert "MEDIUMTEXT" not in _column_ddl(schedule, "body", postgresql.dialect())
    item = TiqoraReportScheduleItem.__table__
    for column in ("schedule_id", "position"):
        assert item.c[column].autoincrement is False
        assert "AUTO_INCREMENT" not in _column_ddl(item, column, mysql.dialect())
        for marker in ("SERIAL", "GENERATED"):
            assert marker not in _column_ddl(item, column, postgresql.dialect())
    # the run outlives its schedule (no FK); items, recipients and files cascade
    assert not TiqoraReportRun.__table__.c["schedule_id"].foreign_keys
    for table in (item, TiqoraReportScheduleRecipient.__table__, run_file):
        assert [fk.ondelete for fk in table.foreign_keys] == ["CASCADE"]


def test_gi_debug_ddl() -> None:
    """``communication_id`` is Znuny's 32-char id and unique; content rows go with their entry
    (``ON DELETE CASCADE``), same as migration ``20261010_0076``."""
    from tiqora.db.tiqora.models import TiqoraGiDebugContent, TiqoraGiDebugEntry

    assert "VARCHAR(32)" in _column_ddl(
        TiqoraGiDebugEntry.__table__, "communication_id", mysql.dialect()
    )
    uniques = {
        tuple(c.name for c in ix.columns)
        for ix in TiqoraGiDebugEntry.__table__.indexes
        if ix.unique
    }
    uniques |= {
        tuple(c.name for c in uc.columns)
        for uc in TiqoraGiDebugEntry.__table__.constraints
        if isinstance(uc, UniqueConstraint)
    }
    assert ("communication_id",) in uniques
    fk = next(iter(TiqoraGiDebugContent.__table__.c.entry_id.foreign_keys))
    assert fk.ondelete == "CASCADE"
    assert "LONGBLOB" in _column_ddl(TiqoraGiDebugContent.__table__, "content", mysql.dialect())
    assert "BYTEA" in _column_ddl(TiqoraGiDebugContent.__table__, "content", postgresql.dialect())
    assert "MEDIUMTEXT" in _column_ddl(TiqoraGiDebugEntry.__table__, "replay", mysql.dialect())
