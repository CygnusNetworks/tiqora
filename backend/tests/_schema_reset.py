"""Reset Tiqora-created schema on a shared Znuny testcontainer.

Used by the migration/bootstrap tests, which each need to start an
``alembic upgrade`` from a known slate. Previously each of those three files
carried its own byte-equivalent copy of this, and the copies drifted: none of
them dropped the ``ix_owned_*`` indexes, which is why shuffling the module
order broke test_migrate_cli.
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text


def reset_tiqora_schema(sync_url: str) -> None:
    """Drop every schema object a Tiqora migration may have created.

    That is the ``tiqora_*`` tables *and* the ``ix_owned_*`` indexes the gated
    ``versions_owned`` chain adds to Znuny-owned tables (ticket,
    dynamic_field_value, ...).

    Both halves matter because the containers are session-scoped and
    conftest's snapshot/restore only covers *rows* -- DDL that another module
    ran survives it. Tables are introspected rather than hardcoded so this
    stays correct as new ones are added.
    """
    is_mysql = "mysql" in sync_url
    engine = create_engine(sync_url)
    try:
        insp = inspect(engine)
        table_names = insp.get_table_names()
        tiqora_tables = [name for name in table_names if name.startswith("tiqora_")]
        owned_indexes = [
            (ix["name"], table)
            for table in table_names
            for ix in insp.get_indexes(table)
            if ix["name"] and ix["name"].startswith("ix_owned_")
        ]
        views = [name for name in insp.get_view_names() if name.startswith("tiqora_")]
        with engine.begin() as conn:
            # a view blocks DROP TABLE (PostgreSQL) or dangles after it (MariaDB)
            for view in views:
                conn.execute(text(f"DROP VIEW IF EXISTS {view}"))
            if is_mysql:
                conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for table in tiqora_tables:
                cascade = "" if is_mysql else " CASCADE"
                conn.execute(text(f"DROP TABLE IF EXISTS {table}{cascade}"))
            for name, table in owned_indexes:
                # MySQL scopes DROP INDEX to a table; Postgres does not.
                stmt = (
                    f"DROP INDEX {name} ON {table}" if is_mysql else f"DROP INDEX IF EXISTS {name}"
                )
                conn.execute(text(stmt))
            if is_mysql:
                conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    finally:
        engine.dispose()


#: rows Tiqora migrations insert themselves (migration 20261009_0063: the report state row;
#: 20261010_0079: the CMDB general catalog)
_MIGRATION_SEED_TABLES = ("tiqora_report_state", "tiqora_cmdb_catalog")


def delete_migration_seed_rows(sync_url: str) -> None:
    """Delete the rows migrations seed, so a module that ran ``upgrade`` leaks nothing.

    Test databases are built with ``create_all`` (no seed rows); a migration test that
    upgrades the shared container must leave it the same way.
    """
    engine = create_engine(sync_url)
    try:
        tables = set(inspect(engine).get_table_names())
        with engine.begin() as conn:
            for table in _MIGRATION_SEED_TABLES:
                if table in tables:
                    conn.execute(text(f"DELETE FROM {table}"))
    finally:
        engine.dispose()
