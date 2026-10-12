"""Migration 20261010_0077 (``tiqora_itsm_link_type``) on real databases.

Upgrades from 0073, compares the migrated table with the ORM metadata (columns, types,
nullability, unique constraint, no foreign key), runs ``ensure_default_link_types`` against the
*migration-built* table (a server default missing in the migration fails here with MariaDB 1364,
not only in production), downgrades, upgrades again. Sync test on purpose: Alembic's env.py runs
its own event loop. Leaves the schema at head.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import datetime

import pytest
from alembic import command
from sqlalchemy import Boolean, Integer, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._schema_reset import delete_migration_seed_rows, reset_tiqora_schema
from tiqora.cli.migrate import build_alembic_config
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.itsm.link_type_defaults import DEFAULT_LINK_TYPES, ensure_default_link_types

pytestmark = pytest.mark.db

_PREVIOUS = "20261010_0073"
_NEW = "20261010_0077"
_TABLE = "tiqora_itsm_link_type"


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


def _has_table(sync_url: str) -> bool:
    engine = create_engine(sync_url)
    try:
        return _TABLE in set(inspect(engine).get_table_names())
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
    table = TiqoraBase.metadata.tables[_TABLE]
    cols = {c["name"]: c for c in insp.get_columns(_TABLE)}
    assert set(cols) == {c.name for c in table.columns}
    for col in table.columns:
        got = cols[col.name]
        assert got["nullable"] == col.nullable, col.name
        assert _kind(got["type"]) == _kind(col.type.dialect_impl(engine.dialect)), col.name
        if col.type.python_type is str and getattr(col.type, "length", None):
            assert got["type"].length == col.type.length, col.name
        # every server default the ORM relies on exists in the migrated table (and no other;
        # the serial id reflects a ``nextval`` default on PostgreSQL)
        if not col.primary_key:
            assert (got.get("default") is not None) == (col.server_default is not None), col.name
    assert insp.get_pk_constraint(_TABLE)["constrained_columns"] == ["id"]
    got_unique = {
        (u["name"], tuple(u["column_names"])) for u in insp.get_unique_constraints(_TABLE)
    }
    got_unique |= {
        (i["name"], tuple(i["column_names"])) for i in insp.get_indexes(_TABLE) if i.get("unique")
    }
    assert ("uq_tiqora_itsm_link_type_name", ("name",)) in got_unique
    assert insp.get_foreign_keys(_TABLE) == []


def _seed_through_migrated_table(sync_url: str) -> tuple[int, int]:
    """Insert the defaults twice through the ORM into the migration-built table."""

    async def _run() -> tuple[int, int]:
        engine = create_async_engine(_async_url(sync_url))
        try:
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                first = await ensure_default_link_types(session, user_id=1)
                await session.commit()
            async with factory() as session:
                second = await ensure_default_link_types(session, user_id=1)
                await session.commit()
            return first, second
        finally:
            await engine.dispose()

    return asyncio.run(_run())


@pytest.mark.parametrize("alembic_env", ["mariadb_znuny_url", "postgres_znuny_url"], indirect=True)
def test_itsm_link_type_migration_matches_orm_and_round_trips(alembic_env: str) -> None:
    sync_url = alembic_env
    had_version_row = _had_version_row(sync_url)
    reset_tiqora_schema(sync_url)
    try:
        _migrate(_PREVIOUS)
        assert not _has_table(sync_url)

        _migrate(_NEW)
        assert _version(sync_url) == _NEW
        assert _has_table(sync_url)
        engine = create_engine(sync_url)
        try:
            _assert_matches_orm(engine)
            # raw insert that leaves every defaulted column out: the DDL defaults apply
            with engine.begin() as conn:
                now = datetime(2026, 10, 12, 8, 0, 0)
                conn.execute(
                    text(
                        "INSERT INTO tiqora_itsm_link_type (name, origin, create_time, create_by,"
                        " change_time, change_by) VALUES ('w4-mig-raw', 'tiqora', :t, 1, :t, 1)"
                    ),
                    {"t": now},
                )
                row = conn.execute(
                    text(
                        "SELECT pointed, propagation, offer_ticket_ticket, offer_ticket_ci,"
                        " offer_ci_ci, offer_ci_service, position, valid, source_label,"
                        " target_label FROM tiqora_itsm_link_type WHERE name = 'w4-mig-raw'"
                    )
                ).one()
                assert (
                    bool(row[0]),
                    row[1],
                    bool(row[2]),
                    bool(row[3]),
                    bool(row[4]),
                    bool(row[5]),
                    row[6],
                    bool(row[7]),
                    row[8],
                    row[9],
                ) == (False, "none", False, False, False, False, 0, True, None, None)
                conn.execute(text("DELETE FROM tiqora_itsm_link_type WHERE name = 'w4-mig-raw'"))
        finally:
            engine.dispose()

        assert _seed_through_migrated_table(sync_url) == (len(DEFAULT_LINK_TYPES), 0)
        engine = create_engine(sync_url)
        try:
            with engine.connect() as conn:
                names = set(conn.execute(text("SELECT name FROM tiqora_itsm_link_type")).scalars())
            assert names == {d.name for d in DEFAULT_LINK_TYPES}
        finally:
            engine.dispose()

        _migrate(_PREVIOUS, down=True)
        assert not _has_table(sync_url)
        assert _version(sync_url) == _PREVIOUS

        _migrate(_NEW)
        assert _has_table(sync_url)
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
