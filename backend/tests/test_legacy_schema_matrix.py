"""Layer A multi-version schema matrix (release anchors × MariaDB / Postgres).

Loads **real** upstream OTRS/Znuny DDL for each release profile, then proves
Tiqora can detect the profile, apply ``tiqora_*`` migrations, and perform a
minimal ticket write. Peer-app behavioural golden (Layer B) is separate.

Run with::

    uv run pytest -q -m schema_matrix

Requires Docker. Marked ``schema_matrix`` so PR CI can stay on the default
Znuny 6.5 suite while release tags / ``workflow_dispatch`` run the matrix.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.bootstrap.schema_loader import (
    legacy_schema_dir,
    load_legacy_schema,
)
from tiqora.config import get_settings
from tiqora.db.legacy.profile import (
    ALL_SCHEMA_PROFILES,
    RELEASE_SCHEMA_PROFILES,
    SchemaProfileId,
    detect_legacy_schema_profile_sync,
    ensure_legacy_schema_supported,
    reset_legacy_schema_profile,
)
from tiqora.domain.ticket_write_service import TicketIn, create_ticket
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.schema_matrix


def _fixture_profiles() -> tuple[str, ...]:
    if os.environ.get("SCHEMA_MATRIX_FULL") == "1":
        return tuple(p.value for p in ALL_SCHEMA_PROFILES)
    return tuple(p.value for p in RELEASE_SCHEMA_PROFILES)


def _expected_profile(profile_id: str) -> SchemaProfileId:
    # Fresh 6.4 DDL shares markers with 6.5 → detector reports znuny-6.5.
    if profile_id == "znuny-6.4":
        return SchemaProfileId.ZNUNY_6_5
    return SchemaProfileId(profile_id)


def _async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://")
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


def _wipe_mysql(url: str) -> None:
    """Drop and recreate the database so the next profile load is clean."""
    import pymysql

    parsed = urlparse(
        url.replace("mysql+pymysql://", "mysql://").replace("mysql+aiomysql://", "mysql://")
    )
    db = (parsed.path or "/test").lstrip("/") or "test"
    conn = pymysql.connect(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 3306,
        user=parsed.username or "root",
        password=parsed.password or "",
        autocommit=True,
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{db}`")
            cur.execute(
                f"CREATE DATABASE `{db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        conn.close()


def _wipe_postgres(url: str) -> None:
    """Drop public schema objects for a clean reload."""
    import psycopg2

    dsn = url
    for prefix in ("postgresql+psycopg2://", "postgresql+asyncpg://"):
        if dsn.startswith(prefix):
            dsn = "postgresql://" + dsn[len(prefix) :]
            break
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE")
            cur.execute("CREATE SCHEMA public")
            cur.execute("GRANT ALL ON SCHEMA public TO public")
    finally:
        conn.close()


def _run_tiqora_migrations(sync_url: str) -> None:
    """Apply the safe (non-owned) Alembic chain only."""
    from alembic import command

    from tiqora.cli.migrate import build_alembic_config

    async_url = _async_url(sync_url)
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = async_url
    get_settings.cache_clear()
    try:
        cfg = build_alembic_config(include_owned=False)
        command.upgrade(cfg, "head")
    finally:
        if old is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def matrix_mariadb_url() -> Generator[str, None, None]:
    from tests.conftest import docker_available

    if not docker_available():
        pytest.skip("Docker not available")
    from testcontainers.mysql import MySqlContainer

    try:
        import pymysql  # noqa: F401
    except ImportError:
        pytest.skip("pymysql not installed")

    with MySqlContainer("mariadb:10.11", dialect="pymysql") as mysql:
        yield mysql.get_connection_url()


@pytest.fixture(scope="module")
def matrix_postgres_url() -> Generator[str, None, None]:
    from tests.conftest import docker_available

    if not docker_available():
        pytest.skip("Docker not available")
    from testcontainers.postgres import PostgresContainer

    try:
        import psycopg2  # noqa: F401
    except ImportError:
        pytest.skip("psycopg2 not installed")

    with PostgresContainer("postgres:16") as pg:
        yield pg.get_connection_url()


@pytest.mark.parametrize("profile_id", _fixture_profiles())
def test_fixture_dir_has_six_sql_files(profile_id: str) -> None:
    d = legacy_schema_dir(profile_id)
    for name in (
        "schema.mysql.sql",
        "schema.postgresql.sql",
        "initial_insert.mysql.sql",
        "initial_insert.postgresql.sql",
        "schema-post.mysql.sql",
        "schema-post.postgresql.sql",
    ):
        path = d / name
        assert path.is_file(), path
        assert path.stat().st_size > 1000, f"{path} looks empty"


def _assert_profile_cell(sync_url: str, profile_id: str, dialect: str) -> None:
    reset_legacy_schema_profile()
    if dialect == "mysql":
        _wipe_mysql(sync_url)
    else:
        _wipe_postgres(sync_url)

    load_legacy_schema(sync_url, profile_id, dialect=dialect)  # type: ignore[arg-type]

    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            detected = detect_legacy_schema_profile_sync(conn)
    finally:
        engine.dispose()

    expected = _expected_profile(profile_id)
    assert detected.profile_id is expected, (
        f"expected {expected.value}, got {detected.profile_id.value} "
        f"(groups={detected.groups_table}, oauth={detected.mail_account_has_oauth}, "
        f"color={detected.state_priority_has_color})"
    )
    assert detected.is_supported
    assert detected.dialect == dialect

    # Gate + adapters
    import asyncio

    async_url = _async_url(sync_url)

    async def _gate() -> None:
        eng = create_async_engine(async_url)
        factory = async_sessionmaker(eng, expire_on_commit=False)
        try:
            async with factory() as session:
                profile = await ensure_legacy_schema_supported(
                    session,
                    dialect_hint=dialect,
                )
            assert profile.profile_id is expected
        finally:
            await eng.dispose()

    asyncio.run(_gate())

    # tiqora_* migrations (non-owned)
    _run_tiqora_migrations(sync_url)

    # Read smoke
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            n_users = conn.execute(text("SELECT COUNT(*) FROM users")).scalar_one()
            assert int(n_users) >= 1
            groups_table = detected.groups_table
            n_groups = conn.execute(
                text(f"SELECT COUNT(*) FROM `{groups_table}`")
                if dialect == "mysql"
                else text(f'SELECT COUNT(*) FROM "{groups_table}"')
            ).scalar_one()
            assert int(n_groups) >= 1
            # Peer tables must still exist after tiqora migrations
            assert conn.execute(text("SELECT COUNT(*) FROM ticket")).scalar_one() is not None
    finally:
        engine.dispose()

    # Write smoke via TicketWriteService (needs tiqora_* tables from migrations)
    async def _write() -> int:
        eng = create_async_engine(async_url)
        factory = async_sessionmaker(eng, expire_on_commit=False)

        async def _fetch(_name: str) -> None:
            return None

        sysconfig = SysConfig(fetch=_fetch)
        try:
            async with factory() as session, session.begin():
                return await create_ticket(
                    session,
                    factory,
                    sysconfig,
                    params=TicketIn(
                        title=f"matrix-{profile_id}",
                        queue_id=1,
                        state_id=1,
                        priority_id=3,
                        owner_id=1,
                    ),
                    user_id=1,
                )
        finally:
            await eng.dispose()

    ticket_id = asyncio.run(_write())
    assert ticket_id >= 1

    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT id, title FROM ticket WHERE id = :id"),
                {"id": ticket_id},
            ).first()
            assert row is not None
            hist = conn.execute(
                text("SELECT COUNT(*) FROM ticket_history WHERE ticket_id = :id"),
                {"id": ticket_id},
            ).scalar_one()
            assert int(hist) >= 1
    finally:
        engine.dispose()

    reset_legacy_schema_profile()


@pytest.mark.db
@pytest.mark.parametrize("profile_id", _fixture_profiles())
def test_matrix_mariadb(profile_id: str, matrix_mariadb_url: str) -> None:
    _assert_profile_cell(matrix_mariadb_url, profile_id, "mysql")


@pytest.mark.db
@pytest.mark.parametrize("profile_id", _fixture_profiles())
def test_matrix_postgres(profile_id: str, matrix_postgres_url: str) -> None:
    _assert_profile_cell(matrix_postgres_url, profile_id, "postgresql")
