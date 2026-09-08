"""DDL the ORM emits for ``tiqora_*`` tables must match what the migrations create.

Test and production databases are not built the same way: tests (and, as it
turned out, production) use ``TiqoraBase.metadata.create_all``, while the
Alembic chain builds tables from explicit ``op.create_table`` calls. Where the
two disagree, the disagreement only shows up in production.
"""

from __future__ import annotations

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
