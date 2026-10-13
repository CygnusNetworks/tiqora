"""Migration 20261010_0081 (importer run/map tables) on real databases.

Upgrades from 0079, compares both tables with the ORM metadata, exercises the map's unique
key and the run table's defaults-free insert, checks that the CMDB add-on views expose
exactly the columns :mod:`tiqora.itsm.addon_views` pins (the importer creates no view),
downgrades (tables gone, views stay) and upgrades again. Sync on purpose: Alembic's env.py
runs its own event loop. Leaves the schema at head without seed rows.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests._schema_reset import delete_migration_seed_rows, reset_tiqora_schema
from tests.test_cmdb_migration_db import _assert_table_matches_orm
from tiqora.cli.migrate import build_alembic_config
from tiqora.config import get_settings
from tiqora.db.tiqora.models import TiqoraItsmImportMap, TiqoraItsmImportRun
from tiqora.itsm import addon_views

MIGRATION = next(Path(__file__).parents[1].glob("alembic/versions_tiqora/*_itsm_import.py"))
_PREVIOUS = "20261010_0079"
_NEW = "20261010_0081"
_TABLES = ("tiqora_itsm_import_run", "tiqora_itsm_import_map")
_NOW = "2026-10-13 08:00:00"


def _async_url(url: str) -> str:
    return (
        url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("mysql+pymysql://", "mysql+aiomysql://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


def _sync_url(url: str) -> str:
    return (
        url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("mysql+aiomysql://", "mysql+pymysql://")
        .replace("postgresql://", "postgresql+psycopg2://")
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
    (command.downgrade if down else command.upgrade)(cfg, target)


def _names(sync_url: str) -> tuple[set[str], set[str]]:
    engine = create_engine(sync_url)
    try:
        insp = inspect(engine)
        return set(insp.get_table_names()), set(insp.get_view_names())
    finally:
        engine.dispose()


def _scalar(sync_url: str, sql: str) -> object:
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql)).scalar()
    finally:
        engine.dispose()


def test_migration_creates_tables_only_and_chains_on_cmdb() -> None:
    spec = importlib.util.spec_from_file_location("itsm_import_mig", MIGRATION)
    assert spec is not None and spec.loader is not None
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    assert (mig.revision, mig.down_revision) == (_NEW, _PREVIOUS)
    source = MIGRATION.read_text()
    assert "CREATE VIEW" not in source
    assert source.count("op.create_table(") == 2


def test_view_column_contract_is_frozen() -> None:
    assert addon_views.VIEW_NAMES == ("tiqora_v1_itsm_ci", "tiqora_v1_itsm_ci_links")
    assert addon_views.VIEW_COLUMNS["tiqora_v1_itsm_ci_links"] == (
        "ci_id",
        "object_kind",
        "object_key",
        "link_type",
        "direction",
        "created",
    )
    assert addon_views.CI_COLUMNS[0] == "ci_id"
    assert addon_views.CI_COLUMNS[-1] == "class_portal_visible"
    assert len(set(addon_views.CI_COLUMNS)) == len(addon_views.CI_COLUMNS) == 13


@pytest.mark.db
@pytest.mark.parametrize("alembic_env", ["mariadb_znuny_url", "postgres_znuny_url"], indirect=True)
def test_import_tables_migrate_match_orm_and_enforce_the_map_key(alembic_env: str) -> None:
    sync_url = alembic_env
    engine0 = create_engine(sync_url)
    try:
        had_row = "tiqora_alembic_version" in set(inspect(engine0).get_table_names()) and bool(
            _scalar(sync_url, "SELECT COUNT(*) FROM tiqora_alembic_version")
        )
    finally:
        engine0.dispose()
    reset_tiqora_schema(sync_url)
    try:
        _migrate(_PREVIOUS)
        tables, _ = _names(sync_url)
        assert not set(_TABLES) & tables

        _migrate(_NEW)
        assert _scalar(sync_url, "SELECT version_num FROM tiqora_alembic_version") == _NEW
        tables, views = _names(sync_url)
        assert set(_TABLES) <= tables
        assert set(addon_views.VIEW_NAMES) <= views

        engine = create_engine(sync_url)
        try:
            for name in _TABLES:
                _assert_table_matches_orm(engine, name)
            assert TiqoraItsmImportRun.__tablename__ == "tiqora_itsm_import_run"
            assert TiqoraItsmImportMap.__tablename__ == "tiqora_itsm_import_map"

            # the CMDB views carry exactly the pinned columns, in order
            with engine.connect() as conn:
                for view, columns in addon_views.VIEW_COLUMNS.items():
                    keys = tuple(conn.execute(text(f"SELECT * FROM {view} LIMIT 0")).keys())
                    assert keys == columns, view

            ins = text(
                "INSERT INTO tiqora_itsm_import_map"
                " (source_kind, source_id, target_id, fingerprint, run_id, updated_at)"
                " VALUES (:k, :s, 1, 'f', 1, :t)"
            )
            with engine.begin() as conn:
                conn.execute(ins, {"k": "ci", "s": "56030", "t": _NOW})
                # same id under another kind is a different source record
                conn.execute(ins, {"k": "class", "s": "56030", "t": _NOW})
            with pytest.raises(IntegrityError), engine.begin() as conn:
                conn.execute(ins, {"k": "ci", "s": "56030", "t": _NOW})
            with engine.begin() as conn:
                n = conn.execute(
                    text("SELECT COUNT(*) FROM tiqora_itsm_import_map WHERE source_id = '56030'")
                ).scalar_one()
                assert n == 2
                conn.execute(text("DELETE FROM tiqora_itsm_import_map"))

            # the run table takes a minimal row; summary has no default
            run_sql = text(
                "INSERT INTO tiqora_itsm_import_run (kind, status, started_by, started_at, summary)"
                " VALUES ('dry_run', 'running', 1, :t, '{}')"
            )
            with engine.begin() as conn:
                conn.execute(run_sql, {"t": _NOW})
                row = conn.execute(
                    text("SELECT job_id, finished_at, profile, message FROM tiqora_itsm_import_run")
                ).one()
                assert tuple(row) == (None, None, None, None)
                conn.execute(text("DELETE FROM tiqora_itsm_import_run"))
            with pytest.raises(DBAPIError), engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO tiqora_itsm_import_run (kind, status, started_by, started_at)"
                        " VALUES ('import', 'running', 1, :t)"
                    ),
                    {"t": datetime(2026, 10, 13, 8, 0, 0)},
                )
        finally:
            engine.dispose()

        _migrate(_PREVIOUS, down=True)
        tables, views = _names(sync_url)
        assert not set(_TABLES) & tables
        assert set(addon_views.VIEW_NAMES) <= views  # the CMDB migration owns them
        assert _scalar(sync_url, "SELECT version_num FROM tiqora_alembic_version") == _PREVIOUS

        _migrate(_NEW)
        tables, _ = _names(sync_url)
        assert set(_TABLES) <= tables
    finally:
        _migrate("head")
        delete_migration_seed_rows(sync_url)
        if not had_row:
            engine = create_engine(sync_url)
            try:
                with engine.begin() as conn:
                    conn.execute(text("DELETE FROM tiqora_alembic_version"))
            finally:
                engine.dispose()
