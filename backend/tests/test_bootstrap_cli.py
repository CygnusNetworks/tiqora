"""End-to-end tests for ``tiqora bootstrap`` on empty MariaDB and Postgres.

Uses raw empty testcontainers (not the schema-loaded fixtures) because
bootstrap itself creates the base schema.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, text

from tiqora.cli.bootstrap import add_bootstrap_subparser, run_bootstrap
from tiqora.config import get_settings
from tiqora.znuny.password import verify_password

pytestmark = pytest.mark.db


def _bootstrap_args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_bootstrap_subparser(parser.add_subparsers(dest="command"))
    return parser.parse_args(["bootstrap", *argv])


def _sync_url(url: str) -> str:
    return (
        url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("mysql+aiomysql://", "mysql+pymysql://")
        .replace("postgresql://", "postgresql+psycopg2://")
    )


@pytest.fixture(scope="module")
def empty_mariadb_url() -> Generator[str, None, None]:
    """Empty MariaDB 10.11 (no Znuny schema)."""
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
def empty_postgres_url() -> Generator[str, None, None]:
    """Empty Postgres 16 (no Znuny schema)."""
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


def _assert_bootstrap_ok(sync_url: str, password: str, login: str = "root@localhost") -> None:
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            # Base schema present
            users = conn.execute(
                text("SELECT id, login, pw FROM users WHERE login = :l"),
                {"l": login},
            )
            row = users.mappings().one()
            assert row["login"] == login
            assert verify_password(password, row["pw"])

            # Admin group membership with rw
            membership = (
                conn.execute(
                    text(
                        "SELECT gu.permission_key FROM group_user gu "
                        "JOIN permission_groups g ON g.id = gu.group_id "
                        "WHERE gu.user_id = :uid AND g.name = 'admin'"
                    ),
                    {"uid": row["id"]},
                )
                .scalars()
                .all()
            )
            assert "rw" in membership

            # Tiqora migrations applied
            version = conn.execute(
                text("SELECT version_num FROM tiqora_alembic_version")
            ).scalar_one()
            assert version is not None

            # A representative tiqora_* table exists
            settings_count = conn.execute(text("SELECT COUNT(*) FROM tiqora_settings")).scalar_one()
            assert settings_count is not None
    finally:
        engine.dispose()


def _run_and_assert(
    url: str,
    *,
    password: str,
    seed: bool = False,
    customers: int = 2,
    tickets: int = 3,
    seed_value: int = 42,
) -> None:
    old_url = os.environ.get("DATABASE_URL")
    old_flag = os.environ.get("TIQORA_SCHEMA_OWNERSHIP")
    os.environ.pop("TIQORA_SCHEMA_OWNERSHIP", None)
    get_settings.cache_clear()
    try:
        argv = [
            "--database-url",
            url,
            "--admin-password",
            password,
        ]
        if seed:
            argv.extend(
                [
                    "--seed",
                    "--customers",
                    str(customers),
                    "--tickets",
                    str(tickets),
                    "--seed-value",
                    str(seed_value),
                ]
            )
        rc = run_bootstrap(_bootstrap_args(*argv))
        assert rc == 0
        _assert_bootstrap_ok(_sync_url(url), password)

        if seed:
            engine = create_engine(_sync_url(url))
            try:
                with engine.connect() as conn:
                    n_tickets = conn.execute(text("SELECT COUNT(*) FROM ticket")).scalar_one()
                    assert n_tickets >= tickets
            finally:
                engine.dispose()
    finally:
        if old_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_url
        if old_flag is not None:
            os.environ["TIQORA_SCHEMA_OWNERSHIP"] = old_flag
        get_settings.cache_clear()


@pytest.mark.db
def test_bootstrap_empty_mariadb(empty_mariadb_url: str) -> None:
    password = "BootstrapTestPass1!"
    _run_and_assert(empty_mariadb_url, password=password, seed=True)

    # Re-run is idempotent (skip schema, reset password, no error / no dupes)
    _run_and_assert(empty_mariadb_url, password="BootstrapTestPass2!", seed=False)

    # After re-run, only the new password works; still one root user
    engine = create_engine(_sync_url(empty_mariadb_url))
    try:
        with engine.connect() as conn:
            n_users = conn.execute(
                text("SELECT COUNT(*) FROM users WHERE login = 'root@localhost'")
            ).scalar_one()
            assert n_users == 1
            pw = conn.execute(
                text("SELECT pw FROM users WHERE login = 'root@localhost'")
            ).scalar_one()
            assert verify_password("BootstrapTestPass2!", pw)
            assert not verify_password(password, pw)
    finally:
        engine.dispose()


@pytest.mark.db
def test_bootstrap_empty_postgres(empty_postgres_url: str) -> None:
    password = "BootstrapPgPass1!"
    _run_and_assert(empty_postgres_url, password=password, seed=True)

    # Idempotent re-run
    _run_and_assert(empty_postgres_url, password="BootstrapPgPass2!", seed=False)

    engine = create_engine(_sync_url(empty_postgres_url))
    try:
        with engine.connect() as conn:
            n_users = conn.execute(
                text("SELECT COUNT(*) FROM users WHERE login = 'root@localhost'")
            ).scalar_one()
            assert n_users == 1
            pw = conn.execute(
                text("SELECT pw FROM users WHERE login = 'root@localhost'")
            ).scalar_one()
            assert verify_password("BootstrapPgPass2!", pw)
    finally:
        engine.dispose()


def _drop_tiqora_tables(sync_url: str) -> None:
    """Reset tiqora_* so migrate upgrade is deterministic on shared fixtures."""
    from sqlalchemy import inspect

    is_mysql = "mysql" in sync_url
    engine = create_engine(sync_url)
    tables = [n for n in inspect(engine).get_table_names() if n.startswith("tiqora_")]
    with engine.begin() as conn:
        if is_mysql:
            conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for tbl in tables:
            conn.execute(text(f"DROP TABLE IF EXISTS {tbl}{'' if is_mysql else ' CASCADE'}"))
        if is_mysql:
            conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    engine.dispose()


@pytest.mark.db
def test_bootstrap_skip_schema_on_populated_fixture(postgres_znuny_url: str) -> None:
    """When users already exist, bootstrap skips base schema and still migrates + sets pw.

    Shared session fixtures may already have ``tiqora_*`` tables from other
    tests (sometimes without a clean alembic version row), so drop them first.
    """
    password = "AlreadyPopulated1!"
    sync_url = _sync_url(postgres_znuny_url)
    _drop_tiqora_tables(sync_url)

    old_url = os.environ.get("DATABASE_URL")
    old_flag = os.environ.get("TIQORA_SCHEMA_OWNERSHIP")
    os.environ.pop("TIQORA_SCHEMA_OWNERSHIP", None)
    get_settings.cache_clear()
    try:
        rc = run_bootstrap(
            _bootstrap_args(
                "--database-url",
                postgres_znuny_url,
                "--admin-password",
                password,
            )
        )
        assert rc == 0
        _assert_bootstrap_ok(sync_url, password)
    finally:
        if old_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_url
        if old_flag is not None:
            os.environ["TIQORA_SCHEMA_OWNERSHIP"] = old_flag
        get_settings.cache_clear()


def test_detect_dialect_rejects_unknown() -> None:
    from tiqora.bootstrap.schema_loader import detect_dialect

    with pytest.raises(ValueError, match="Unsupported"):
        detect_dialect("sqlite:///tmp.db")


def test_schema_dir_has_six_sql_files() -> None:
    from tiqora.bootstrap.schema_loader import schema_dir

    root = schema_dir()
    names = {
        "schema.mysql.sql",
        "schema.postgresql.sql",
        "initial_insert.mysql.sql",
        "initial_insert.postgresql.sql",
        "schema-post.mysql.sql",
        "schema-post.postgresql.sql",
    }
    assert names.issubset({p.name for p in root.iterdir()})
