"""Migration 20261010_0068 (saved reports / saved searches) on real databases.

Upgrades from 0067, compares the migrated schema with the ORM metadata (columns, types,
nullability, indexes, unique constraints, foreign keys), checks that no foreign key leaves
the ``tiqora_*`` tables, downgrades, upgrades again and (MariaDB) stores payloads over 64 KiB.
Sync test on purpose: Alembic's env.py runs its own event loop. Leaves the schema at head.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime

import pytest
from alembic import command
from sqlalchemy import Boolean, Integer, create_engine, inspect, text
from sqlalchemy.engine import Engine

from tests._schema_reset import delete_migration_seed_rows, reset_tiqora_schema
from tiqora.cli.migrate import build_alembic_config
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

_PREVIOUS = "20261009_0067"
_SAVED = "20261010_0068"
_TABLES = {
    "tiqora_report",
    "tiqora_report_group",
    "tiqora_saved_search",
    "tiqora_saved_search_group",
    "tiqora_saved_search_pref",
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
    """Run alone, the DB was built by ``create_all`` (no version row): leave it that way."""
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


def _assert_matches_orm(engine: Engine) -> None:
    insp = inspect(engine)
    for name in sorted(_TABLES):
        table = TiqoraBase.metadata.tables[name]
        cols = {c["name"]: c for c in insp.get_columns(name)}
        assert set(cols) == {c.name for c in table.columns}, name
        for col in table.columns:
            got = cols[col.name]
            ctx = f"{name}.{col.name}"
            assert got["nullable"] == col.nullable, ctx
            assert _kind(got["type"]) == _kind(col.type.dialect_impl(engine.dialect)), ctx
            if col.type.python_type is str and hasattr(col.type, "length") and col.type.length:
                assert got["type"].length == col.type.length, ctx
        assert insp.get_pk_constraint(name)["constrained_columns"] == [
            c.name for c in table.primary_key.columns
        ], name

        orm_indexes = {(i.name, tuple(c.name for c in i.columns)) for i in table.indexes}
        got_indexes = {
            (i["name"], tuple(i["column_names"]))
            for i in insp.get_indexes(name)
            if not i.get("unique")
        }
        # MariaDB also lists the index it creates for each FK; ignore those extras.
        assert orm_indexes <= got_indexes, name
        assert {n for n, _ in got_indexes - orm_indexes} <= {
            fk["name"] for fk in insp.get_foreign_keys(name)
        }, name

        orm_unique = {
            (u.name, tuple(c.name for c in u.columns))
            for u in table.constraints
            if u.__class__.__name__ == "UniqueConstraint"
        }
        got_unique = {
            (u["name"], tuple(u["column_names"])) for u in insp.get_unique_constraints(name)
        }
        got_unique |= {
            (i["name"], tuple(i["column_names"])) for i in insp.get_indexes(name) if i.get("unique")
        }
        assert orm_unique <= got_unique, name

        orm_fks = {
            (
                fk.name,
                tuple(e.parent.name for e in fk.elements),
                fk.referred_table.name,
                tuple(e.column.name for e in fk.elements),
                fk.ondelete,
            )
            for fk in table.foreign_key_constraints
        }
        got_fks = {
            (
                fk["name"],
                tuple(fk["constrained_columns"]),
                fk["referred_table"],
                tuple(fk["referred_columns"]),
                (fk.get("options") or {}).get("ondelete"),
            )
            for fk in insp.get_foreign_keys(name)
        }
        assert got_fks == orm_fks, name
        # No foreign key may leave the tiqora_* tables (Znuny ids are mirrored without FKs).
        assert all(fk[2].startswith("tiqora_") for fk in got_fks), name


@pytest.mark.parametrize("alembic_env", ["mariadb_znuny_url", "postgres_znuny_url"], indirect=True)
def test_saved_migration_matches_orm_and_round_trips(alembic_env: str) -> None:
    sync_url = alembic_env
    had_version_row = _had_version_row(sync_url)
    reset_tiqora_schema(sync_url)
    try:
        _migrate(_PREVIOUS)
        assert _tables(sync_url) == set()

        _migrate(_SAVED)
        assert _version(sync_url) == _SAVED
        assert _tables(sync_url) == _TABLES

        engine = create_engine(sync_url)
        try:
            _assert_matches_orm(engine)
            if "mysql" in sync_url:
                with engine.connect() as conn:
                    types = {
                        (t.lower(), c.lower()): d.lower()
                        for t, c, d in conn.execute(
                            text(
                                "SELECT table_name, column_name, data_type"
                                " FROM information_schema.columns WHERE table_schema = DATABASE()"
                                " AND table_name IN ('tiqora_report', 'tiqora_saved_search')"
                                " AND column_name IN ('definition', 'criteria')"
                            )
                        ).all()
                    }
                assert types == {
                    ("tiqora_report", "definition"): "mediumtext",
                    ("tiqora_saved_search", "criteria"): "mediumtext",
                }
            big = '{"x": "' + "y" * 100_000 + '"}'
            now = datetime(2026, 10, 10, 8, 0, 0)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO tiqora_report (name, owner_user_id, definition,"
                        " schema_version, created_at, created_by, changed_at, changed_by)"
                        " VALUES ('rep48 mig', 48005, :d, 1, :t, 48005, :t, 48005)"
                    ),
                    {"d": big, "t": now},
                )
                conn.execute(
                    text(
                        "INSERT INTO tiqora_saved_search (owner_user_id, name, criteria,"
                        " created_at, changed_at) VALUES (48005, 'rep48 mig', :d, :t, :t)"
                    ),
                    {"d": big, "t": now},
                )
            with engine.begin() as conn:
                d = conn.execute(
                    text("SELECT definition FROM tiqora_report WHERE owner_user_id = 48005")
                ).scalar_one()
                c = conn.execute(
                    text("SELECT criteria FROM tiqora_saved_search WHERE owner_user_id = 48005")
                ).scalar_one()
                assert d == big and c == big
                conn.execute(text("DELETE FROM tiqora_saved_search WHERE owner_user_id = 48005"))
                conn.execute(text("DELETE FROM tiqora_report WHERE owner_user_id = 48005"))
        finally:
            engine.dispose()

        _migrate(_PREVIOUS, down=True)
        assert _tables(sync_url) == set()
        assert _version(sync_url) == _PREVIOUS

        _migrate(_SAVED)
        assert _tables(sync_url) == _TABLES
        engine = create_engine(sync_url)
        try:
            _assert_matches_orm(engine)
        finally:
            engine.dispose()
    finally:
        _migrate("head")
        delete_migration_seed_rows(sync_url)
        if not had_version_row:
            _drop_version_row(sync_url)
