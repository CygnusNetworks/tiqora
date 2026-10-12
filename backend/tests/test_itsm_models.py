"""ITSM core tables (migration ``20261010_0078``) on MariaDB and PostgreSQL.

The migration builds the tables; the tests compare them with the ORM metadata (columns, types,
nullability, server defaults, keys, indexes, foreign keys), exercise the constraints that matter
(unique matrix cell, cascade from major incident to its updates, SET NULL on the KB article)
and run a downgrade/upgrade round trip. Sync tests on purpose: Alembic's env.py runs its own
event loop. The GDPR erasure test lives in ``test_gdpr_erasure.py``
(``test_gdpr_erasure_removes_itsm_rows``). Leaves the schema at head.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime

import pytest
from alembic import command
from sqlalchemy import Boolean, Integer, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from tests._schema_reset import delete_migration_seed_rows, reset_tiqora_schema
from tiqora.cli.migrate import build_alembic_config
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

_PREVIOUS = "20261010_0077"
_NEW = "20261010_0078"
_NOW = datetime(2026, 10, 12, 8, 0, 0)
_TABLES = (
    "tiqora_itsm_priority_matrix",
    "tiqora_itsm_service_meta",
    "tiqora_itsm_problem",
    "tiqora_itsm_major_incident",
    "tiqora_itsm_major_incident_update",
    "tiqora_itsm_bulk_target",
)
_PARAMS = ["mariadb_znuny_url", "postgres_znuny_url"]


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
        return set(inspect(engine).get_table_names())
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


@pytest.fixture
def migrated(alembic_env: str) -> Iterator[str]:
    """A schema migrated from scratch to 0078 (so every table is migration-built)."""
    sync_url = alembic_env
    had_version_row = _had_version_row(sync_url)
    reset_tiqora_schema(sync_url)
    try:
        _migrate(_NEW)
        yield sync_url
    finally:
        _migrate("head")
        delete_migration_seed_rows(sync_url)
        if not had_version_row:
            _drop_version_row(sync_url)


def _kind(type_: object) -> str:
    """Coarse type family; MariaDB reflects BOOLEAN as TINYINT(1)."""
    affinity = type_._type_affinity  # type: ignore[attr-defined]
    if affinity in (Boolean, Integer):
        return "int-or-bool"
    return str(affinity.__name__)


def _assert_table_matches_orm(engine: Engine, name: str) -> None:
    insp = inspect(engine)
    table = TiqoraBase.metadata.tables[name]
    cols = {c["name"]: c for c in insp.get_columns(name)}
    assert set(cols) == {c.name for c in table.columns}, name
    for col in table.columns:
        got = cols[col.name]
        assert got["nullable"] == col.nullable, (name, col.name)
        assert _kind(got["type"]) == _kind(col.type.dialect_impl(engine.dialect)), (
            name,
            col.name,
        )
        if col.type.python_type is str and getattr(col.type, "length", None):
            assert got["type"].length == col.type.length, (name, col.name)
        # every server default the ORM relies on exists in the migrated table (and no other;
        # serial ids reflect a ``nextval`` default on PostgreSQL)
        if not col.primary_key:
            assert (got.get("default") is not None) == (col.server_default is not None), (
                name,
                col.name,
            )
    assert insp.get_pk_constraint(name)["constrained_columns"] == [
        c.name for c in table.primary_key.columns
    ], name
    got_unique = {
        (u["name"], tuple(u["column_names"])) for u in insp.get_unique_constraints(name)
    } | {(i["name"], tuple(i["column_names"])) for i in insp.get_indexes(name) if i.get("unique")}
    want_unique = {
        (c.name, tuple(col.name for col in c.columns))
        for c in table.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    }
    assert want_unique <= got_unique, name
    got_indexes = {
        (i["name"], tuple(i["column_names"])) for i in insp.get_indexes(name) if not i.get("unique")
    }
    # MariaDB adds an index for each foreign key; only the named ORM indexes must be there
    want_indexes = {(i.name, tuple(c.name for c in i.columns)) for i in table.indexes}
    assert want_indexes <= got_indexes, name
    got_fks = {
        (f["name"], tuple(f["constrained_columns"]), f["referred_table"]): (
            (f.get("options") or {}).get("ondelete")
        )
        for f in insp.get_foreign_keys(name)
    }
    want_fks = {
        (fk.name, (fk.parent.name,), fk.column.table.name): fk.ondelete for fk in table.foreign_keys
    }
    assert got_fks == want_fks, name


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_migration_creates_itsm_tables(migrated: str) -> None:
    sync_url = migrated
    assert _version(sync_url) == _NEW
    assert set(_TABLES) <= _tables(sync_url)
    engine = create_engine(sync_url)
    try:
        for name in _TABLES:
            _assert_table_matches_orm(engine, name)
        # a raw insert that leaves every defaulted column out: the DDL defaults apply
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tiqora_itsm_service_meta (service_id, change_time, change_by)"
                    " VALUES (940001, :t, 1)"
                ),
                {"t": _NOW},
            )
            conn.execute(
                text(
                    "INSERT INTO tiqora_itsm_problem (ticket_id, change_time, change_by)"
                    " VALUES (940002, :t, 1)"
                ),
                {"t": _NOW},
            )
            conn.execute(
                text(
                    "INSERT INTO tiqora_itsm_major_incident (ticket_id, declared_at, declared_by)"
                    " VALUES (940003, :t, 1)"
                ),
                {"t": _NOW},
            )
            conn.execute(
                text(
                    "INSERT INTO tiqora_itsm_major_incident_update (ticket_id, created_at,"
                    " created_by, mode, body) VALUES (940003, :t, 1, 'note', 'x')"
                ),
                {"t": _NOW},
            )
            meta = conn.execute(
                text(
                    "SELECT portal_visible, service_type, min_minutes_between_incidents"
                    " FROM tiqora_itsm_service_meta WHERE service_id = 940001"
                )
            ).one()
            assert (bool(meta[0]), meta[1], meta[2]) == (True, None, None)
            problem = conn.execute(
                text(
                    "SELECT known_error, known_error_article_id, known_error_since"
                    " FROM tiqora_itsm_problem WHERE ticket_id = 940002"
                )
            ).one()
            assert (bool(problem[0]), problem[1], problem[2]) == (False, None, None)
            assert conn.execute(
                text(
                    "SELECT target_count, job_id FROM tiqora_itsm_major_incident_update"
                    " WHERE ticket_id = 940003"
                )
            ).one() == (0, None)
            for table in (
                "tiqora_itsm_major_incident_update",
                "tiqora_itsm_major_incident",
                "tiqora_itsm_problem",
                "tiqora_itsm_service_meta",
            ):
                key = "service_id" if table.endswith("service_meta") else "ticket_id"
                conn.execute(text(f"DELETE FROM {table} WHERE {key} IN (940001, 940002, 940003)"))
    finally:
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_priority_matrix_cell_unique(migrated: str) -> None:
    engine = create_engine(migrated)
    sql = text(
        "INSERT INTO tiqora_itsm_priority_matrix (criticality, impact, priority_id,"
        " change_time, change_by) VALUES (:c, :i, :p, :t, 1)"
    )
    try:
        with engine.begin() as conn:
            conn.execute(sql, {"c": "3 normal", "i": "3 medium", "p": 3, "t": _NOW})
            # same impact with another criticality and vice versa are different cells
            conn.execute(sql, {"c": "4 high", "i": "3 medium", "p": 2, "t": _NOW})
            conn.execute(sql, {"c": "3 normal", "i": "4 high", "p": 2, "t": _NOW})
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(sql, {"c": "3 normal", "i": "3 medium", "p": 5, "t": _NOW})
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tiqora_itsm_priority_matrix"))
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_major_update_cascades_on_major_delete(migrated: str) -> None:
    engine = create_engine(migrated)
    try:
        with engine.begin() as conn:
            for ticket_id in (940020, 940021):
                conn.execute(
                    text(
                        "INSERT INTO tiqora_itsm_major_incident (ticket_id, declared_at,"
                        " declared_by) VALUES (:t, :n, 1)"
                    ),
                    {"t": ticket_id, "n": _NOW},
                )
                for body in ("one", "two"):
                    conn.execute(
                        text(
                            "INSERT INTO tiqora_itsm_major_incident_update (ticket_id, created_at,"
                            " created_by, mode, body) VALUES (:t, :n, 1, 'internal', :b)"
                        ),
                        {"t": ticket_id, "n": _NOW, "b": body},
                    )
        # an update needs its major incident
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tiqora_itsm_major_incident_update (ticket_id, created_at,"
                    " created_by, mode, body) VALUES (940099, :n, 1, 'note', 'orphan')"
                ),
                {"n": _NOW},
            )
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tiqora_itsm_major_incident WHERE ticket_id = 940020"))
            remaining = dict(
                conn.execute(
                    text(
                        "SELECT ticket_id, COUNT(*) FROM tiqora_itsm_major_incident_update"
                        " WHERE ticket_id IN (940020, 940021) GROUP BY ticket_id"
                    )
                ).all()
            )
        assert remaining == {940021: 2}
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM tiqora_itsm_major_incident WHERE ticket_id IN (940020, 940021)")
            )
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_problem_kb_article_set_null_on_article_delete(migrated: str) -> None:
    engine = create_engine(migrated)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tiqora_kb_article (category_id, title, slug, language, state,"
                    " content_md, version, create_by, create_time, change_by, change_time)"
                    " VALUES (1, 'Known error', 'itsm94-known-error', 'de', 'draft', 'x', 1,"
                    " 1, :n, 1, :n)"
                ),
                {"n": _NOW},
            )
            article_id = int(
                conn.execute(
                    text("SELECT id FROM tiqora_kb_article WHERE slug = 'itsm94-known-error'")
                ).scalar_one()
            )
            conn.execute(
                text(
                    "INSERT INTO tiqora_itsm_problem (ticket_id, known_error,"
                    " known_error_article_id, known_error_since, change_time, change_by)"
                    " VALUES (940030, :ke, :a, :n, :n, 1)"
                ),
                {"ke": True, "a": article_id, "n": _NOW},
            )
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tiqora_kb_article WHERE id = :a"), {"a": article_id})
            row = conn.execute(
                text(
                    "SELECT known_error, known_error_article_id FROM tiqora_itsm_problem"
                    " WHERE ticket_id = 940030"
                )
            ).one()
        # the problem record survives, only the link to the deleted article goes
        assert (bool(row[0]), row[1]) == (True, None)
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tiqora_itsm_problem WHERE ticket_id = 940030"))
            conn.execute(text("DELETE FROM tiqora_kb_article WHERE slug = 'itsm94-known-error'"))
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_bulk_target_primary_key_is_job_and_ticket(migrated: str) -> None:
    engine = create_engine(migrated)
    sql = text(
        "INSERT INTO tiqora_itsm_bulk_target (job_id, ticket_id, status, created_at)"
        " VALUES (:j, :t, 'done', :n)"
    )
    try:
        with engine.begin() as conn:
            conn.execute(sql, {"j": 940040, "t": 940041, "n": _NOW})
            conn.execute(sql, {"j": 940040, "t": 940042, "n": _NOW})
            conn.execute(sql, {"j": 940043, "t": 940041, "n": _NOW})
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(sql, {"j": 940040, "t": 940041, "n": _NOW})
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tiqora_itsm_bulk_target WHERE job_id >= 940040"))
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_downgrade_drops_itsm_tables(migrated: str) -> None:
    sync_url = migrated
    assert set(_TABLES) <= _tables(sync_url)
    _migrate(_PREVIOUS, down=True)
    present = _tables(sync_url)
    assert not set(_TABLES) & present
    # step 0's table and the KB tables the problem table points to stay
    assert {"tiqora_itsm_link_type", "tiqora_kb_article"} <= present
    assert _version(sync_url) == _PREVIOUS

    _migrate(_NEW)
    assert set(_TABLES) <= _tables(sync_url)
    engine = create_engine(sync_url)
    try:
        for name in _TABLES:
            _assert_table_matches_orm(engine, name)
    finally:
        engine.dispose()
