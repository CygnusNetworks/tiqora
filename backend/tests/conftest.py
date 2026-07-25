"""Shared pytest fixtures for unit and DB integration tests."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine, inspect, text

# Single source of truth: shipped package data under tiqora.bootstrap.schema.
from tiqora.bootstrap.schema_loader import (
    load_sql_mysql as _load_sql_mysql,
)
from tiqora.bootstrap.schema_loader import (
    load_sql_postgres as _load_sql_postgres,
)
from tiqora.bootstrap.schema_loader import (
    schema_dir as _package_schema_dir,
)

FIXTURES = _package_schema_dir()


# ---------------------------------------------------------------------------
# Cross-module DB isolation
# ---------------------------------------------------------------------------
# The Znuny containers are session-scoped (loading the DDL takes seconds, so
# per-test containers are not affordable). Historically every DB test file
# hand-rolled its own FK-ordered cleanup scoped to a "private" id band. Nothing
# enforced those bands, so files silently collided and leaked rows into each
# other -- producing FK violations on the *other* file's DELETE, and polluted
# negative-control assertions.
#
# Instead we snapshot the pristine post-seed state once per container and
# restore it after every test module. The seed is tiny (~380 rows over ~120
# tables), so a full delete-and-reinsert is cheaper than trying to compute a
# diff, and unlike a PK-based diff it also reverts UPDATEs to seeded rows.
#
# Isolation is per *module*, not per test: within a file the author controls
# the ids, and per-test restores would cost ~600 round trips instead of ~90.

_SNAPSHOTS: dict[str, _Snapshot] = {}


@dataclass(frozen=True)
class _Snapshot:
    """Pristine post-seed contents of one container."""

    sync_url: str
    dialect: str  # "mysql" | "postgresql"
    rows: dict[str, list[dict[str, object]]]


def _set_fk_enforcement(conn: Connection, dialect: str, *, enabled: bool) -> None:
    """Toggle FK enforcement so the restore can write in any table order.

    Postgres' ``session_replication_role`` suppresses FK triggers and requires
    a superuser -- the testcontainers role owns the database, so it qualifies.
    Both settings are session-local and die with the connection.
    """
    if dialect == "mysql":
        conn.execute(text(f"SET FOREIGN_KEY_CHECKS={1 if enabled else 0}"))
    else:
        role = "origin" if enabled else "replica"
        conn.execute(text(f"SET session_replication_role = '{role}'"))


def _take_snapshot(sync_url: str, dialect: str) -> None:
    """Record every row of every table right after the Znuny seed load."""
    engine = create_engine(sync_url)
    try:
        rows: dict[str, list[dict[str, object]]] = {}
        insp = inspect(engine)
        with engine.connect() as conn:
            for table in insp.get_table_names():
                result = conn.execute(
                    text(
                        f'SELECT * FROM "{table}"'
                        if dialect != "mysql"
                        else f"SELECT * FROM `{table}`"
                    )
                )
                rows[table] = [dict(r) for r in result.mappings()]
        _SNAPSHOTS[dialect] = _Snapshot(sync_url=sync_url, dialect=dialect, rows=rows)
    finally:
        engine.dispose()


def _restore(snap: _Snapshot) -> None:
    """Return the container to its pristine post-seed state.

    Tables created after the snapshot (``tiqora_*`` from ``create_all`` or from
    an Alembic upgrade a test ran) are emptied but kept -- recreating them is
    the individual test's job.
    """
    engine = create_engine(snap.sync_url)
    quote = (lambda t: f"`{t}`") if snap.dialect == "mysql" else (lambda t: f'"{t}"')
    try:
        with engine.begin() as conn:
            _set_fk_enforcement(conn, snap.dialect, enabled=False)
            try:
                for table in inspect(engine).get_table_names():
                    conn.execute(text(f"DELETE FROM {quote(table)}"))
                for table, rows in snap.rows.items():
                    if not rows:
                        continue
                    cols = ", ".join(quote(c) for c in rows[0])
                    binds = ", ".join(f":{c}" for c in rows[0])
                    conn.execute(
                        text(f"INSERT INTO {quote(table)} ({cols}) VALUES ({binds})"), rows
                    )
            finally:
                _set_fk_enforcement(conn, snap.dialect, enabled=True)
    finally:
        engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _restore_db_between_modules() -> Generator[None, None, None]:
    """Undo whatever the module wrote, for every container that is running.

    Deliberately does *not* depend on the container fixtures -- requesting them
    here would start Docker for pure-unit modules too. It only acts on
    containers some earlier test already brought up.
    """
    yield
    for snap in _SNAPSHOTS.values():
        _restore(snap)


def docker_available() -> bool:
    """Return True if the Docker daemon is reachable."""
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "db: integration tests that require Docker (testcontainers MariaDB/Postgres)",
    )
    config.addinivalue_line(
        "markers",
        "search: integration tests that require Meilisearch (testcontainers)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip db/search-marked tests when Docker is not available (unless forced)."""
    if os.environ.get("TIQORA_FORCE_DB_TESTS") == "1":
        return
    if docker_available():
        return
    skip = pytest.mark.skip(reason="Docker not available for db/search-marked tests")
    for item in items:
        if "db" in item.keywords or "search" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def znuny_schema_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def mariadb_znuny_url(znuny_schema_dir: Path) -> Generator[str, None, None]:
    """Start MariaDB 10.11, load Znuny DDL in installer order, yield SQLAlchemy URL.

    Znuny installer order is required for circular FKs (users↔valid):

    1. ``schema.mysql.sql`` — tables without FK constraints
    2. ``initial_insert.mysql.sql`` — seed data (root user, valid rows, …)
    3. ``schema-post.mysql.sql`` — indexes and foreign keys

    Yields a ``mysql+pymysql://`` URL so sync SQLAlchemy engines use PyMySQL
    (not the default MySQLdb/mysqlclient driver).
    """
    if not docker_available():
        pytest.skip("Docker not available")

    from testcontainers.mysql import MySqlContainer

    try:
        import pymysql  # noqa: F401
    except ImportError:
        pytest.skip("pymysql not installed (needed to load MySQL DDL fixtures)")

    # dialect="pymysql" → mysql+pymysql:// (SQLAlchemy default mysql:// needs MySQLdb)
    with MySqlContainer("mariadb:10.11", dialect="pymysql") as mysql:
        url = mysql.get_connection_url()
        _load_sql_mysql(url, znuny_schema_dir / "schema.mysql.sql")
        _load_sql_mysql(url, znuny_schema_dir / "initial_insert.mysql.sql")
        _load_sql_mysql(url, znuny_schema_dir / "schema-post.mysql.sql")
        _take_snapshot(url, "mysql")
        yield url


@pytest.fixture(scope="session")
def postgres_znuny_url(znuny_schema_dir: Path) -> Generator[str, None, None]:
    """Start Postgres 16, load Znuny DDL in installer order, yield SQLAlchemy URL.

    Same order as MariaDB: schema → initial_insert → schema-post. Real Znuny
    installs apply ``schema-post`` *after* seed data so users↔valid FKs succeed.
    """
    if not docker_available():
        pytest.skip("Docker not available")

    from testcontainers.postgres import PostgresContainer

    try:
        import psycopg2  # noqa: F401
    except ImportError:
        pytest.skip("psycopg2 not installed (needed to load Postgres DDL fixtures)")

    with PostgresContainer("postgres:16") as pg:
        url = pg.get_connection_url()
        _load_sql_postgres(url, znuny_schema_dir / "schema.postgresql.sql")
        _load_sql_postgres(url, znuny_schema_dir / "initial_insert.postgresql.sql")
        _load_sql_postgres(url, znuny_schema_dir / "schema-post.postgresql.sql")
        _take_snapshot(url, "postgresql")
        yield url
