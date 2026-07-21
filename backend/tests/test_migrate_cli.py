"""End-to-end tests for the `tiqora migrate` CLI path.

These go through ``run_migrate`` (not just ``build_alembic_config`` +
``command.upgrade``) so they exercise the real entrypoint the Docker image
runs. This catches the nested-``asyncio.run()`` crash that the config-level
tests missed: ``run_migrate`` resolves the async ownership gate AND then calls
Alembic, whose ``env.py`` opens its own event loop.
"""

from __future__ import annotations

import argparse
import os

import pytest
from sqlalchemy import create_engine, inspect, text

from tiqora.cli.migrate import add_migrate_subparser, run_migrate
from tiqora.config import get_settings

pytestmark = pytest.mark.db


def _migrate_args(*argv: str) -> argparse.Namespace:
    """Build the args Namespace exactly as `tiqora migrate ...` would (so
    `set_defaults(func=...)` on the subparser is applied)."""
    parser = argparse.ArgumentParser()
    add_migrate_subparser(parser.add_subparsers(dest="command"))
    return parser.parse_args(["migrate", *argv])


def _drop_tiqora_tables(sync_url: str) -> None:
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


@pytest.mark.db
def test_migrate_cli_upgrade_stays_at_tiqora_head(postgres_znuny_url: str) -> None:
    """`tiqora migrate upgrade` (ownership OFF) must complete without the
    nested-event-loop crash and stop at the tiqora head, applying no owned
    indexes to Znuny tables."""
    sync_url = _sync_url(postgres_znuny_url)
    _drop_tiqora_tables(sync_url)

    old_url = os.environ.get("DATABASE_URL")
    old_flag = os.environ.get("TIQORA_SCHEMA_OWNERSHIP")
    os.environ["DATABASE_URL"] = _async_url(postgres_znuny_url)
    os.environ.pop("TIQORA_SCHEMA_OWNERSHIP", None)  # ownership OFF
    get_settings.cache_clear()
    try:
        rc = run_migrate(_migrate_args("upgrade", "head"))
        assert rc == 0

        engine = create_engine(sync_url)
        version = (
            engine.connect()
            .execute(text("SELECT version_num FROM tiqora_alembic_version"))
            .scalar_one()
        )
        ticket_idx = {ix["name"] for ix in inspect(engine).get_indexes("ticket")}
        engine.dispose()

        assert version == "20260720_0011"
        assert not any(name and name.startswith("ix_owned_") for name in ticket_idx)
    finally:
        if old_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_url
        if old_flag is not None:
            os.environ["TIQORA_SCHEMA_OWNERSHIP"] = old_flag
        get_settings.cache_clear()
