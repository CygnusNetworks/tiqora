"""Reporting migration 20261008_0062 up and down on real databases (both dialects).

Upgrades from the previous revision, checks the five report tables and the single
``tiqora_report_state`` row the migration creates (so first concurrent requests never
race to insert it), downgrades back and upgrades to head again. Sync test on purpose:
Alembic's env.py runs its own event loop. Leaves the schema at head with no rows of
its own (the state row is deleted again; the tables are shared with every module).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from tests._schema_reset import delete_migration_seed_rows, reset_tiqora_schema
from tiqora.cli.migrate import build_alembic_config
from tiqora.config import get_settings
from tiqora.jobs.service import utc_now

pytestmark = pytest.mark.db

_PREVIOUS = "20261008_0061"
_REPORTING = "20261008_0062"
_TABLES = {
    "tiqora_report_ticket_fact",
    "tiqora_report_ticket_event",
    "tiqora_report_daily_snapshot",
    "tiqora_report_state",
    "tiqora_report_dirty_ticket",
}


def _sync_url(url: str) -> str:
    return (
        url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("mysql+aiomysql://", "mysql+pymysql://")
        .replace("postgresql://", "postgresql+psycopg2://")
    )


def _async_url(url: str) -> str:
    return (
        url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("mysql+pymysql://", "mysql+aiomysql://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


@pytest.fixture
def alembic_env(request: pytest.FixtureRequest) -> Iterator[str]:
    url = request.getfixturevalue(request.param)
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _async_url(url)
    get_settings.cache_clear()
    try:
        yield _sync_url(url)
    finally:
        if old is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old
        get_settings.cache_clear()


def _migrate(target: str, *, down: bool = False) -> None:
    cfg = build_alembic_config(include_owned=False)
    if down:
        command.downgrade(cfg, target)
    else:
        command.upgrade(cfg, target)


def _tables(sync_url: str) -> set[str]:
    engine = create_engine(sync_url)
    try:
        return set(inspect(engine).get_table_names()) & _TABLES
    finally:
        engine.dispose()


def _scalar(sync_url: str, sql: str) -> object:
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql)).scalar()
    finally:
        engine.dispose()


def _state_rows(sync_url: str) -> list[tuple[object, ...]]:
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            return [
                tuple(r)
                for r in conn.execute(
                    text(
                        "SELECT name, last_history_id, backfill_status, updated_at,"
                        " backfill_job_id, last_snapshot_date FROM tiqora_report_state"
                    )
                ).all()
            ]
    finally:
        engine.dispose()


def _had_version_row(sync_url: str) -> bool:
    engine = create_engine(sync_url)
    try:
        if "tiqora_alembic_version" not in set(inspect(engine).get_table_names()):
            return False
        with engine.connect() as conn:
            return bool(conn.execute(text("SELECT COUNT(*) FROM tiqora_alembic_version")).scalar())
    finally:
        engine.dispose()


def _delete(sync_url: str, *statements: str) -> None:
    engine = create_engine(sync_url)
    try:
        tables = set(inspect(engine).get_table_names())
        with engine.begin() as conn:
            for stmt in statements:
                if stmt.split()[2] in tables:  # "DELETE FROM <table>"
                    conn.execute(text(stmt))
    finally:
        engine.dispose()


@pytest.mark.parametrize("alembic_env", ["mariadb_znuny_url", "postgres_znuny_url"], indirect=True)
def test_reporting_migration_creates_the_state_row_and_downgrades(alembic_env: str) -> None:
    sync_url = alembic_env
    had_version_row = _had_version_row(sync_url)
    reset_tiqora_schema(sync_url)
    try:
        _migrate(_PREVIOUS)
        assert _tables(sync_url) == set()

        before = utc_now()
        _migrate(_REPORTING)
        assert _scalar(sync_url, "SELECT version_num FROM tiqora_alembic_version") == _REPORTING
        assert _tables(sync_url) == _TABLES
        ((name, last_id, status, updated_at, job_id, snapshot),) = _state_rows(sync_url)
        assert (name, last_id, status, job_id, snapshot) == ("facts", 0, "pending", None, None)
        # naive UTC like every application-written bookkeeping time (second precision)
        assert isinstance(updated_at, datetime) and updated_at.tzinfo is None
        assert before - timedelta(seconds=2) <= updated_at <= utc_now() + timedelta(seconds=2)

        _migrate(_PREVIOUS, down=True)
        assert _tables(sync_url) == set()
        assert _scalar(sync_url, "SELECT version_num FROM tiqora_alembic_version") == _PREVIOUS

        _migrate("head")
        assert _tables(sync_url) == _TABLES
        assert [r[0] for r in _state_rows(sync_url)] == ["facts"]
    finally:
        _migrate("head")
        delete_migration_seed_rows(sync_url)
        if not had_version_row:
            _delete(sync_url, "DELETE FROM tiqora_alembic_version")
