"""Shared pytest fixtures for unit and DB integration tests."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Generator
from pathlib import Path

import pytest

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
        yield url
