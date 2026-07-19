"""Owned-chain migration tests (Phase 5, subtask 2).

With schema ownership enabled (env flag + DB marker), ``alembic upgrade
head`` must apply ``versions_tiqora`` *and* ``versions_owned`` cleanly on
both PostgreSQL and MariaDB, and the resulting indexes must be pure
additions (no data changes). Without ownership enabled, ``versions_owned``
stays entirely inert — covered by ``tests/test_ownership.py``'s gate-logic
unit tests and by :mod:`alembic.env`'s ``_script_locations``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from tiqora.config import get_settings

pytestmark = pytest.mark.db

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"


def _run_alembic_upgrade_head(database_url: str) -> None:
    """Invoke ``alembic upgrade head`` in-process against *database_url*.

    Mirrors how the ``tiqora`` CLI/ops would run migrations: sets
    ``DATABASE_URL`` so ``tiqora.config.get_settings()`` (consumed by
    ``alembic/env.py``) picks it up, clearing the ``lru_cache`` first since
    it is process-global.
    """
    from alembic import command
    from alembic.config import Config

    async_url = (
        database_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
        .replace("mysql+pymysql://", "mysql+aiomysql://")
    )
    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = async_url
    get_settings.cache_clear()
    try:
        cfg = Config(str(ALEMBIC_INI))
        cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
        # cwd for alembic/env.py's relative "alembic/versions_tiqora" paths
        old_cwd = os.getcwd()
        os.chdir(BACKEND_ROOT)
        try:
            command.upgrade(cfg, "head")
        finally:
            os.chdir(old_cwd)
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


def _mark_ownership_enabled(sync_url: str) -> None:
    key_col = "`key`" if "mysql" in sync_url else "key"
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO tiqora_settings ({key_col}, value) "
                "VALUES ('schema.ownership', 'enabled')"
            )
        )
    engine.dispose()


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
        # Phase 1: base tiqora_* chain only (creates tiqora_settings).
        _run_alembic_upgrade_head(postgres_znuny_url)

        # Phase 2: flip both gates and re-run — versions_owned becomes visible.
        os.environ["TIQORA_SCHEMA_OWNERSHIP"] = "1"
        _mark_ownership_enabled(_sync_url(postgres_znuny_url))
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
        _run_alembic_upgrade_head(mariadb_znuny_url)

        os.environ["TIQORA_SCHEMA_OWNERSHIP"] = "1"
        _mark_ownership_enabled(_sync_url(mariadb_znuny_url))
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
