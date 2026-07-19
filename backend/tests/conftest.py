"""Shared pytest fixtures for unit and DB integration tests."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Generator
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "znuny-schema"


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


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip db-marked tests when Docker is not available (unless forced)."""
    if os.environ.get("TIQORA_FORCE_DB_TESTS") == "1":
        return
    if docker_available():
        return
    skip = pytest.mark.skip(reason="Docker not available for db-marked tests")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def znuny_schema_dir() -> Path:
    return FIXTURES


def _split_sql_statements(sql: str, *, mysql_backslash_escapes: bool = False) -> list[str]:
    """Split SQL on semicolons outside quotes / dollar-quotes.

    Znuny seed data embeds multi-line string literals that contain both
    semicolons (``text/plain; charset=utf-8``), MySQL ``\\;`` URL escapes, and
    ``--`` comment-like lines (signature ASCII art). Naive split/comment-stripping
    corrupts those inserts.

    Parameters
    ----------
    mysql_backslash_escapes:
        When True, treat ``\\X`` inside single-quoted strings as an escaped pair
        (MySQL/MariaDB default). PostgreSQL with standard_conforming_strings keeps
        backslash as a literal, so leave this False for PG.
    """
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    in_single = False
    in_double = False
    in_dollar = False
    dollar_tag = ""
    while i < n:
        if not in_single and not in_double and not in_dollar:
            m = re.match(r"\$([A-Za-z0-9_]*)\$", sql[i:])
            if m:
                in_dollar = True
                dollar_tag = m.group(0)
                buf.append(dollar_tag)
                i += len(dollar_tag)
                continue
        if in_dollar:
            if sql.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                in_dollar = False
                dollar_tag = ""
                continue
            buf.append(sql[i])
            i += 1
            continue
        ch = sql[i]
        # MySQL backslash escapes inside single-quoted strings (\' \; \\ …)
        if in_single and mysql_backslash_escapes and ch == "\\" and i + 1 < n:
            buf.append(ch)
            buf.append(sql[i + 1])
            i += 2
            continue
        if not in_double and ch == "'":
            # SQL standard escaped quote: ''
            if in_single and i + 1 < n and sql[i + 1] == "'":
                buf.append("''")
                i += 2
                continue
            in_single = not in_single
            buf.append(ch)
            i += 1
            continue
        if not in_single and ch == '"':
            in_double = not in_double
            buf.append(ch)
            i += 1
            continue
        if not in_single and not in_double and ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _load_sql_mysql(container_url: str, sql_path: Path) -> None:
    """Execute a SQL file against a MySQL/MariaDB testcontainer."""
    from urllib.parse import urlparse

    import pymysql
    from pymysql.constants import CLIENT

    url = container_url
    for prefix in ("mysql+pymysql://", "mysql+aiomysql://", "mariadb+pymysql://"):
        if url.startswith(prefix):
            url = "mysql://" + url[len(prefix) :]
            break
    parsed = urlparse(url)
    conn = pymysql.connect(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 3306,
        user=parsed.username or "root",
        password=parsed.password or "",
        database=(parsed.path or "/test").lstrip("/") or "test",
        autocommit=True,
        charset="utf8mb4",
        client_flag=CLIENT.MULTI_STATEMENTS,
    )
    try:
        # Do not strip ``--`` lines: Znuny seed strings contain signature art with ``--``.
        # MySQL/MariaDB understand ``#`` / ``--`` comments outside of string literals.
        sql = sql_path.read_text(encoding="utf-8", errors="replace")
        with conn.cursor() as cur:
            for stmt in _split_sql_statements(sql, mysql_backslash_escapes=True):
                if not stmt.strip():
                    continue
                try:
                    cur.execute(stmt)
                    while cur.nextset():
                        pass
                except pymysql.err.Error as exc:
                    # Ignore "already exists" / "unknown key" style noise on re-runs
                    if getattr(exc, "args", None) and exc.args[0] in {1050, 1061, 1091}:
                        continue
                    raise
    finally:
        conn.close()


def _load_sql_postgres(dsn: str, sql_path: Path) -> None:
    import psycopg2

    url = dsn
    for prefix in ("postgresql+psycopg2://", "postgresql+asyncpg://"):
        if url.startswith(prefix):
            url = "postgresql://" + url[len(prefix) :]
            break
    conn = psycopg2.connect(url)
    conn.autocommit = True
    try:
        sql = sql_path.read_text(encoding="utf-8", errors="replace")
        with conn.cursor() as cur:
            for stmt in _split_sql_statements(sql, mysql_backslash_escapes=False):
                if not stmt.strip():
                    continue
                try:
                    cur.execute(stmt)
                except psycopg2.Error as exc:
                    # duplicate_table / duplicate_object
                    if exc.pgcode in {"42P07", "42710"}:
                        conn.rollback()
                        continue
                    raise
    finally:
        conn.close()


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
