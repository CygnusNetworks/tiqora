"""Migration 20261010_0079 (CMDB tables, catalog seeds, add-on views) on real databases.

Upgrades from 0078, compares every migrated table with the ORM metadata (columns, types,
nullability, server defaults, indexes, unique constraints, foreign keys), checks the catalog
seeds and both views, downgrades (tables and views gone), upgrades again. Sync test on purpose:
Alembic's env.py runs its own event loop. Leaves the schema at head without seed rows.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import Boolean, Integer, create_engine, inspect, text
from sqlalchemy.engine import Engine

from tests._schema_reset import delete_migration_seed_rows, reset_tiqora_schema
from tiqora.cli.migrate import build_alembic_config
from tiqora.cmdb.seed import DEFAULT_CATALOG_ROWS
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

_PREVIOUS = "20261010_0078"
_NEW = "20261010_0079"
_TABLES = (
    "tiqora_cmdb_catalog",
    "tiqora_cmdb_class",
    "tiqora_cmdb_class_definition",
    "tiqora_cmdb_ci",
    "tiqora_cmdb_ci_version",
    "tiqora_cmdb_ci_version_archive",
    "tiqora_cmdb_ci_attr",
    "tiqora_cmdb_ci_history",
    "tiqora_cmdb_attachment",
    "tiqora_itsm_link",
    "tiqora_cmdb_import_template",
    "tiqora_cmdb_saved_search",
)
_VIEWS = ("tiqora_v1_itsm_ci", "tiqora_v1_itsm_ci_links")


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


def _names(sync_url: str) -> tuple[set[str], set[str]]:
    engine = create_engine(sync_url)
    try:
        insp = inspect(engine)
        return set(insp.get_table_names()), set(insp.get_view_names())
    finally:
        engine.dispose()


def _version(sync_url: str) -> str:
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            return str(
                conn.execute(text("SELECT version_num FROM tiqora_alembic_version")).scalar()
            )
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


def _drop_version_row(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tiqora_alembic_version"))
    finally:
        engine.dispose()


def _kind(type_: object) -> str:
    """Coarse type family; MariaDB reflects BOOLEAN as TINYINT(1)."""
    affinity = type_._type_affinity  # type: ignore[attr-defined]
    if affinity in (Boolean, Integer):
        return "int-or-bool"
    return str(affinity.__name__)


def _on_delete(rule: str | None) -> str | None:
    """MariaDB reflects RESTRICT as no rule at all; PostgreSQL as NO ACTION."""
    return rule if rule in ("CASCADE", "SET NULL") else None


def _assert_table_matches_orm(engine: Engine, name: str) -> None:
    insp = inspect(engine)
    table = TiqoraBase.metadata.tables[name]
    cols = {c["name"]: c for c in insp.get_columns(name)}
    assert set(cols) == {c.name for c in table.columns}, name
    for col in table.columns:
        got = cols[col.name]
        assert got["nullable"] == col.nullable, (name, col.name)
        assert _kind(got["type"]) == _kind(col.type.dialect_impl(engine.dialect)), (name, col.name)
        if col.type.python_type is str and getattr(col.type, "length", None):
            assert got["type"].length == col.type.length, (name, col.name)
        if not col.primary_key:
            # every server default the ORM relies on exists in the migrated table (and no other)
            assert (got.get("default") is not None) == (col.server_default is not None), (
                name,
                col.name,
            )
    assert insp.get_pk_constraint(name)["constrained_columns"] == [
        c.name for c in table.primary_key.columns
    ]
    got_indexes = {
        (i["name"], tuple(i["column_names"])) for i in insp.get_indexes(name) if not i.get("unique")
    }
    want_indexes = {(i.name, tuple(c.name for c in i.columns)) for i in table.indexes}
    # MariaDB adds an index per foreign key; only the declared ones must exist
    assert want_indexes <= got_indexes, (name, want_indexes - got_indexes)
    got_unique = {
        (u["name"], tuple(u["column_names"])) for u in insp.get_unique_constraints(name)
    } | {(i["name"], tuple(i["column_names"])) for i in insp.get_indexes(name) if i.get("unique")}
    want_unique = {
        (c.name, tuple(col.name for col in c.columns))
        for c in table.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    }
    assert want_unique <= got_unique, (name, want_unique - got_unique)
    got_fks = {
        (
            tuple(fk["constrained_columns"]),
            fk["referred_table"],
            tuple(fk["referred_columns"]),
            _on_delete((fk.get("options") or {}).get("ondelete")),
        )
        for fk in insp.get_foreign_keys(name)
    }
    want_fks = {
        (
            tuple(c.name for c in fk.columns),
            fk.elements[0].column.table.name,
            tuple(e.column.name for e in fk.elements),
            _on_delete(fk.ondelete),
        )
        for fk in table.foreign_key_constraints
    }
    assert got_fks == want_fks, (name, got_fks ^ want_fks)


def _assert_schema(sync_url: str) -> None:
    tables, views = _names(sync_url)
    assert set(_TABLES) <= tables
    assert set(_VIEWS) <= views
    engine = create_engine(sync_url)
    try:
        for name in _TABLES:
            _assert_table_matches_orm(engine, name)
        with engine.connect() as conn:
            seeded = {
                tuple(r)
                for r in conn.execute(
                    text(
                        "SELECT catalog_class, item_key, name, functionality, position"
                        " FROM tiqora_cmdb_catalog"
                    )
                ).all()
            }
            assert seeded == {tuple(r) for r in DEFAULT_CATALOG_ROWS}
            for view in _VIEWS:  # the views select without error on the migrated schema
                assert conn.execute(text(f"SELECT COUNT(*) FROM {view}")).scalar_one() == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize("alembic_env", ["mariadb_znuny_url", "postgres_znuny_url"], indirect=True)
def test_cmdb_migration_matches_orm_and_round_trips(alembic_env: str) -> None:
    sync_url = alembic_env
    had_version_row = _had_version_row(sync_url)
    reset_tiqora_schema(sync_url)
    try:
        _migrate(_PREVIOUS)
        tables, views = _names(sync_url)
        assert not set(_TABLES) & tables
        assert not set(_VIEWS) & views

        _migrate(_NEW)
        assert _version(sync_url) == _NEW
        _assert_schema(sync_url)

        # raw insert that leaves every defaulted column out: the DDL defaults apply
        engine = create_engine(sync_url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO tiqora_cmdb_class (class_key, name, number_prefix, group_id,"
                        " created_by, created_at, changed_by, changed_at)"
                        " VALUES ('cmdb4mig', 'cmdb4mig', 'MIG', 1, 1, :t, 1, :t)"
                    ),
                    {"t": "2026-10-12 08:00:00"},
                )
                row = conn.execute(
                    text(
                        "SELECT number_counter, portal_visible, position, valid, lock_version,"
                        " current_definition_id FROM tiqora_cmdb_class WHERE class_key='cmdb4mig'"
                    )
                ).one()
                assert (row[0], bool(row[1]), row[2], bool(row[3]), row[4], row[5]) == (
                    0,
                    False,
                    0,
                    True,
                    1,
                    None,
                )
                conn.execute(text("DELETE FROM tiqora_cmdb_class WHERE class_key = 'cmdb4mig'"))
        finally:
            engine.dispose()

        _migrate(_PREVIOUS, down=True)
        tables, views = _names(sync_url)
        assert not set(_TABLES) & tables
        assert not set(_VIEWS) & views
        assert _version(sync_url) == _PREVIOUS

        _migrate(_NEW)
        _assert_schema(sync_url)
    finally:
        _migrate("head")
        delete_migration_seed_rows(sync_url)
        if not had_version_row:
            _drop_version_row(sync_url)
