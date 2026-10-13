"""Plugin migration 20261010_0070 on real databases (both dialects).

upgrade -> downgrade -> upgrade, then the migrated tables are compared with the tables the
ORM metadata creates (``create_all``): columns, types, nullability, defaults, primary keys,
indexes, unique constraints and foreign keys. Sync test on purpose: Alembic's env.py runs
its own event loop. Leaves the schema at head (metadata-built plugin tables, no rows).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from tests._schema_reset import delete_migration_seed_rows, reset_tiqora_schema
from tiqora.cli.migrate import build_alembic_config
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

_PREVIOUS = "20261010_0068"
_PLUGINS = "20261010_0070"
_TABLES = ("tiqora_plugin", "tiqora_plugin_runtime", "tiqora_plugin_hook")


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
    (command.downgrade if down else command.upgrade)(cfg, target)


def _present(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names()) & set(_TABLES)


def _shape(engine: Engine) -> dict[str, dict[str, Any]]:
    """Dialect-level description of the three tables, comparable between two builds."""
    insp = inspect(engine)
    out: dict[str, dict[str, Any]] = {}
    for table in _TABLES:
        cols = {
            c["name"]: (
                c["type"].compile(dialect=engine.dialect),
                bool(c["nullable"]),
                None if c["default"] is None else str(c["default"]),
            )
            for c in insp.get_columns(table)
        }
        out[table] = {
            "columns": cols,
            "pk": tuple(insp.get_pk_constraint(table)["constrained_columns"]),
            "indexes": sorted(
                (i["name"], tuple(i["column_names"]), bool(i["unique"]))
                for i in insp.get_indexes(table)
            ),
            "uniques": sorted(
                (u["name"], tuple(u["column_names"])) for u in insp.get_unique_constraints(table)
            ),
            "fks": sorted(
                (tuple(f["constrained_columns"]), f["referred_table"], tuple(f["referred_columns"]))
                for f in insp.get_foreign_keys(table)
            ),
        }
    return out


def _drop_plugin_tables(engine: Engine) -> None:
    with engine.begin() as conn:
        for table in reversed(_TABLES):
            conn.execute(text(f"DROP TABLE IF EXISTS {table}"))


@pytest.mark.parametrize("alembic_env", ["mariadb_znuny_url", "postgres_znuny_url"], indirect=True)
def test_plugins_migration_up_down_up_matches_the_models(alembic_env: str) -> None:
    sync_url = alembic_env
    engine = create_engine(sync_url)
    had_version_row = False
    if "tiqora_alembic_version" in inspect(engine).get_table_names():
        with engine.connect() as conn:
            had_version_row = bool(
                conn.execute(text("SELECT COUNT(*) FROM tiqora_alembic_version")).scalar()
            )
    reset_tiqora_schema(sync_url)
    try:
        _migrate(_PREVIOUS)
        assert _present(engine) == set()

        _migrate(_PLUGINS)
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM tiqora_alembic_version")).scalar()
                == _PLUGINS
            )
        assert _present(engine) == set(_TABLES)
        # 20261010_0082 widens tiqora_plugin: compare the shape at head with the ORM
        _migrate("head")
        migrated = _shape(engine)

        _migrate(_PREVIOUS, down=True)
        assert _present(engine) == set()

        _migrate("head")
        assert _present(engine) == set(_TABLES)
        assert _shape(engine) == migrated  # upgrade is repeatable

        # the same tables built from the ORM metadata
        _drop_plugin_tables(engine)
        TiqoraBase.metadata.create_all(
            engine, tables=[TiqoraBase.metadata.tables[t] for t in _TABLES]
        )
        assert _shape(engine) == migrated

        # spot checks of the design (P5), independent of the comparison above
        assert migrated["tiqora_plugin_runtime"]["pk"] == ("plugin_id", "role")
        assert ("ix_tiqora_plugin_hook_plugin_id", ("plugin_id",), False) in migrated[
            "tiqora_plugin_hook"
        ]["indexes"]
        plugin_cols = migrated["tiqora_plugin"]["columns"]
        assert plugin_cols["enabled"][1] is False and plugin_cols["changed_by"][1] is True
        assert "unsigned" in (plugin_cols["signature_status"][2] or "")
        for table in _TABLES:  # every foreign key points to a tiqora_ table
            for _cols, referred, _ in migrated[table]["fks"]:
                assert referred.startswith("tiqora_")
    finally:
        _migrate("head")
        delete_migration_seed_rows(sync_url)
        if not had_version_row:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM tiqora_alembic_version"))
        engine.dispose()
