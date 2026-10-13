"""Shared Alembic environment for plugin migrations (D6).

One run migrates ONE plugin: its own version table ``tqp_<short_id>_alembic_version``, its own
revision files, no autogenerate. Every statement goes through ``StatementGuard`` before it
reaches the database, and the set of tables is diffed again after every revision, inside that
revision's transaction.

Transactions: PostgreSQL runs the whole upgrade in one transaction (a failure rolls everything
back). MariaDB commits DDL implicitly anyway, so it runs one transaction per revision
(MARIADB-STAMP): a later failing revision cannot discard the version stamp of an earlier
revision that was fully applied.
"""

from __future__ import annotations

import asyncio
from typing import Any

from alembic import context
from sqlalchemy import event, inspect, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from tiqora.db.engine import _normalize_url, _utc_connect_args
from tiqora.plugins.migrations import PluginMigrationError, StatementGuard

config = context.config
_attrs: dict[str, Any] = config.attributes


def _do_run(connection: Connection) -> None:
    short_id = str(_attrs["short_id"])
    prefix = f"tqp_{short_id}_"
    names_before = set(inspect(connection).get_table_names())
    per_revision = connection.dialect.name == "mysql"
    if per_revision:
        # end the autobegun transaction: otherwise Alembic treats it as an external transaction
        # and never commits per revision
        connection.commit()

    def on_apply(ctx: Any, step: Any, heads: Any, run_args: Any) -> None:
        # runs inside the revision's transaction, after its stamp: raising rolls the stamp back
        stray = sorted(set(inspect(connection).get_table_names()) - names_before)
        stray = [name for name in stray if not name.startswith(prefix)]
        if stray:
            raise PluginMigrationError(
                f"Tabelle {stray[0]} hat nicht das Präfix {prefix} "
                "(Anweisung in Datenbank-Änderung der Erweiterung nicht erlaubt)"
            )
        down = step.down_revision_ids
        _attrs["last_applied"] = (
            step.up_revision_id if step.is_upgrade else (down[0] if down else None)
        )
        _attrs["steps_done"] = int(_attrs.get("steps_done", 0)) + 1

    context.configure(
        connection=connection,
        target_metadata=None,
        version_table=str(_attrs["version_table"]),
        transaction_per_migration=per_revision,
        on_version_apply=on_apply,
    )
    _attrs["current"] = context.get_context().get_current_revision()
    event.listen(connection, "before_cursor_execute", StatementGuard(short_id))
    with context.begin_transaction():
        context.run_migrations()
    connection.commit()  # reached only on success; any error above rolls back what is open


async def _run_async() -> None:
    url = _normalize_url(str(_attrs["database_url"]))
    engine = create_async_engine(url, poolclass=pool.NullPool, connect_args=_utc_connect_args(url))
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_do_run)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise PluginMigrationError("Erweiterungs-Migrationen laufen nicht im Offline-Modus")
asyncio.run(_run_async())
