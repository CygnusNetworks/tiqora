"""Migration 20261010_0074 (report schedules and delivery runs) on real databases.

Upgrades from 0072, compares the migrated schema with the ORM metadata (columns, types,
nullability, indexes, unique constraints, foreign keys), checks that no foreign key leaves
the ``tiqora_*`` tables, downgrades, upgrades again and (MariaDB) stores a mail body over
64 KiB and a run file over 64 KiB. Sync test on purpose: Alembic's env.py runs its own event
loop. Leaves the schema at head; the rows it writes go with the downgrade.
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

_PREVIOUS = "20261010_0072"
_NEW = "20261010_0074"
_TABLES = {
    "tiqora_report_schedule",
    "tiqora_report_schedule_item",
    "tiqora_report_schedule_recipient",
    "tiqora_report_run",
    "tiqora_report_run_file",
}
#: owner id of the rows this test writes (wave-3 reporting band 52000-52399)
_OWNER = 52395


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
def test_report_schedule_migration_matches_orm_and_round_trips(alembic_env: str) -> None:
    sync_url = alembic_env
    had_version_row = _had_version_row(sync_url)
    reset_tiqora_schema(sync_url)
    try:
        _migrate(_PREVIOUS)
        assert _tables(sync_url) == set()

        _migrate(_NEW)
        assert _version(sync_url) == _NEW
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
                                " AND table_name IN ('tiqora_report_schedule',"
                                " 'tiqora_report_run_file') AND column_name IN ('body', 'data')"
                            )
                        ).all()
                    }
                assert types == {
                    ("tiqora_report_schedule", "body"): "mediumtext",
                    ("tiqora_report_run_file", "data"): "longblob",
                }
            big_text = "y" * 100_000
            big_blob = b"%PDF-1.7\n" + bytes(range(256)) * 400  # > 64 KiB
            now = datetime(2026, 10, 11, 8, 0, 0)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO tiqora_report_schedule (owner_user_id, name, recurrence,"
                        " time_zone, subject, body, created_at, changed_at)"
                        " VALUES (:o, 'rep52 mig', '{}', 'UTC', '', :b, :t, :t)"
                    ),
                    {"o": _OWNER, "b": big_text, "t": now},
                )
                sid = conn.execute(
                    text("SELECT id FROM tiqora_report_schedule WHERE owner_user_id = :o"),
                    {"o": _OWNER},
                ).scalar_one()
                conn.execute(
                    text(
                        "INSERT INTO tiqora_report_schedule_item (schedule_id, position,"
                        " report_key) VALUES (:s, 0, 'builtin.open_now')"
                    ),
                    {"s": sid},
                )
                conn.execute(
                    text(
                        "INSERT INTO tiqora_report_schedule_recipient (schedule_id, kind, ref_id)"
                        " VALUES (:s, 'user', :o)"
                    ),
                    {"s": sid, "o": _OWNER},
                )
                conn.execute(
                    text(
                        "INSERT INTO tiqora_report_run (schedule_id, schedule_name, owner_user_id,"
                        " run_kind, status, report_keys, created_at)"
                        " VALUES (:s, 'rep52 mig', :o, 'manual', 'succeeded', '[]', :t)"
                    ),
                    {"s": sid, "o": _OWNER, "t": now},
                )
                run_id = conn.execute(
                    text("SELECT id FROM tiqora_report_run WHERE owner_user_id = :o"),
                    {"o": _OWNER},
                ).scalar_one()
                conn.execute(
                    text(
                        "INSERT INTO tiqora_report_run_file (run_id, name, content_type,"
                        " size_bytes, data, created_at)"
                        " VALUES (:r, 'rep52.pdf', 'application/pdf', :n, :d, :t)"
                    ),
                    {"r": run_id, "n": len(big_blob), "d": big_blob, "t": now},
                )
            with engine.begin() as conn:
                body_back = conn.execute(
                    text("SELECT body FROM tiqora_report_schedule WHERE id = :s"), {"s": sid}
                ).scalar_one()
                data_back = conn.execute(
                    text("SELECT data FROM tiqora_report_run_file WHERE run_id = :r"),
                    {"r": run_id},
                ).scalar_one()
                assert body_back == big_text and bytes(data_back) == big_blob
                # server defaults of the migrated schedule table
                row = conn.execute(
                    text(
                        "SELECT format, language, only_if_nonempty, active, failure_count,"
                        " version FROM tiqora_report_schedule WHERE id = :s"
                    ),
                    {"s": sid},
                ).one()
                assert (row[0], row[1], bool(row[2]), bool(row[3]), row[4], row[5]) == (
                    "pdf",
                    "de",
                    False,
                    True,
                    0,
                    1,
                )
                # items and recipients cascade with the schedule; runs outlive it (no FK)
                conn.execute(text("DELETE FROM tiqora_report_schedule WHERE id = :s"), {"s": sid})
                for table in ("tiqora_report_schedule_item", "tiqora_report_schedule_recipient"):
                    left = conn.execute(
                        text(f"SELECT COUNT(*) FROM {table} WHERE schedule_id = :s"),  # noqa: S608
                        {"s": sid},
                    ).scalar_one()
                    assert left == 0, table
                # run files cascade with the run
                conn.execute(text("DELETE FROM tiqora_report_run WHERE id = :r"), {"r": run_id})
                left = conn.execute(
                    text("SELECT COUNT(*) FROM tiqora_report_run_file WHERE run_id = :r"),
                    {"r": run_id},
                ).scalar_one()
                assert left == 0
        finally:
            engine.dispose()

        _migrate(_PREVIOUS, down=True)
        assert _tables(sync_url) == set()
        assert _version(sync_url) == _PREVIOUS

        _migrate(_NEW)
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
