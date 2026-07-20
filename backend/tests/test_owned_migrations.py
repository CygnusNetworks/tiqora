"""Owned-chain migration tests (Phase 5, subtask 2).

With schema ownership enabled (env flag + DB marker), ``alembic upgrade
head`` must apply ``versions_tiqora`` *and* ``versions_owned`` cleanly on
both PostgreSQL and MariaDB, and the resulting indexes must be pure
additions (no data changes). Without ownership enabled, ``versions_owned``
stays entirely inert — covered by ``tests/test_ownership.py``'s gate-logic
unit tests, by :mod:`tests.test_migration_gate` (the config-level gate), and
by ``tiqora.cli.migrate.build_alembic_config``.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect, text

from tiqora.config import get_settings

pytestmark = pytest.mark.db


def _run_alembic_upgrade_head(database_url: str) -> None:
    """Invoke ``alembic upgrade head`` in-process against *database_url*.

    Mirrors how the ``tiqora`` CLI/ops would run migrations: sets
    ``DATABASE_URL`` so ``tiqora.config.get_settings()`` (consumed by
    ``alembic/env.py``) picks it up, clearing the ``lru_cache`` first since
    it is process-global.
    """
    from alembic import command

    from tiqora.cli.migrate import build_alembic_config

    async_url = (
        database_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
        .replace("mysql+pymysql://", "mysql+aiomysql://")
    )
    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = async_url
    get_settings.cache_clear()
    try:
        # This test module exercises the OWNED chain, so it builds the config
        # with the owned locations included — exactly what `tiqora migrate`
        # does once the ownership gate is active.
        cfg = build_alembic_config(include_owned=True)
        command.upgrade(cfg, "head")
    finally:
        if old_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_url
        get_settings.cache_clear()


def _drop_tiqora_tables(sync_url: str) -> None:
    """The testcontainer fixture is session-scoped and shared with other test
    modules (e.g. ``test_ownership.py`` seeds ``tiqora_settings`` via raw
    SQL, other suites exercise the KB/webhook/outbox models directly). Start
    each migration run from a clean slate — introspect and drop every
    ``tiqora_*`` table rather than hardcoding names, so this stays correct
    as new tiqora_* tables are added — so ``alembic upgrade`` from base
    doesn't collide with tables created outside Alembic's control.
    """
    is_mysql = "mysql" in sync_url
    engine = create_engine(sync_url)
    insp = inspect(engine)
    tables = [name for name in insp.get_table_names() if name.startswith("tiqora_")]
    with engine.begin() as conn:
        if is_mysql:
            conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in tables:
            cascade = "" if is_mysql else " CASCADE"
            conn.execute(text(f"DROP TABLE IF EXISTS {table}{cascade}"))
        if is_mysql:
            conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    engine.dispose()


def _sync_url(async_url: str) -> str:
    return (
        async_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("mysql+aiomysql://", "mysql+pymysql://")
        .replace("postgresql://", "postgresql+psycopg2://")
    )


def _owned_indexes(sync_url: str) -> dict[str, set[str]]:
    engine = create_engine(sync_url)
    insp = inspect(engine)
    ticket_idx = {ix["name"] for ix in insp.get_indexes("ticket")}
    dfv_idx = {ix["name"] for ix in insp.get_indexes("dynamic_field_value")}
    engine.dispose()
    return {"ticket": ticket_idx, "dynamic_field_value": dfv_idx}


@pytest.mark.db
def test_owned_chain_applies_cleanly_on_postgres(postgres_znuny_url: str) -> None:
    old_flag = os.environ.get("TIQORA_SCHEMA_OWNERSHIP")
    try:
        _drop_tiqora_tables(_sync_url(postgres_znuny_url))
        # Apply the full chain (tiqora + owned) — this module validates that
        # the owned DDL runs cleanly on real DBs. The gate that keeps owned
        # invisible without ownership is validated separately in
        # tests/test_migration_gate.py.
        os.environ["TIQORA_SCHEMA_OWNERSHIP"] = "1"
        _run_alembic_upgrade_head(postgres_znuny_url)

        indexes = _owned_indexes(_sync_url(postgres_znuny_url))
        assert "ix_owned_ticket_customer_archive" in indexes["ticket"]
        assert "ix_owned_ticket_queue_state" in indexes["ticket"]
        assert "ix_owned_dynamic_field_value_object_field" in indexes["dynamic_field_value"]
    finally:
        if old_flag is None:
            os.environ.pop("TIQORA_SCHEMA_OWNERSHIP", None)
        else:
            os.environ["TIQORA_SCHEMA_OWNERSHIP"] = old_flag
        get_settings.cache_clear()


@pytest.mark.db
def test_owned_chain_applies_cleanly_on_mariadb(mariadb_znuny_url: str) -> None:
    old_flag = os.environ.get("TIQORA_SCHEMA_OWNERSHIP")
    try:
        _drop_tiqora_tables(_sync_url(mariadb_znuny_url))
        os.environ["TIQORA_SCHEMA_OWNERSHIP"] = "1"
        _run_alembic_upgrade_head(mariadb_znuny_url)

        indexes = _owned_indexes(_sync_url(mariadb_znuny_url))
        assert "ix_owned_ticket_customer_archive" in indexes["ticket"]
        assert "ix_owned_ticket_queue_state" in indexes["ticket"]
        assert "ix_owned_dynamic_field_value_object_field" in indexes["dynamic_field_value"]
    finally:
        if old_flag is None:
            os.environ.pop("TIQORA_SCHEMA_OWNERSHIP", None)
        else:
            os.environ["TIQORA_SCHEMA_OWNERSHIP"] = old_flag
        get_settings.cache_clear()
