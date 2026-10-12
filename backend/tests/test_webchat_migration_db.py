"""Web-chat migration 20261010_0072 up/down/up on both dialects, checked against the ORM.

Sync test on purpose (Alembic's env.py runs its own event loop). The migrated schema must
equal what ``TiqoraBase.metadata.create_all`` builds for the same four tables (columns,
types, nullability, server defaults, indexes and uniques), and no table may carry a
foreign key to a non-``tiqora_`` table. Leaves the schema at head.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from tests._schema_reset import delete_migration_seed_rows, reset_tiqora_schema
from tiqora.cli.migrate import build_alembic_config
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

_PREVIOUS = "20261010_0071"
_WEBCHAT = "20261010_0072"
#: 20261010_0073 (web chat phase 2) sits on top of the GI debugger migration since the merge
_WEBCHAT2_PARENT = "20261010_0076"
_TABLES = (
    "tiqora_chat_config",
    "tiqora_chat_session",
    "tiqora_chat_message",
    "tiqora_chat_agent_state",
)


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


def _present(sync_url: str) -> set[str]:
    engine = create_engine(sync_url)
    try:
        return set(inspect(engine).get_table_names()) & set(_TABLES)
    finally:
        engine.dispose()


def _version(sync_url: str) -> object:
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            return conn.execute(text("SELECT version_num FROM tiqora_alembic_version")).scalar()
    finally:
        engine.dispose()


def _reflect(sync_url: str) -> dict[str, dict[str, Any]]:
    """Dialect-reflected shape of the four tables, comparable between two builds."""
    engine = create_engine(sync_url)
    try:
        insp = inspect(engine)
        out: dict[str, dict[str, Any]] = {}
        for table in _TABLES:
            cols = {
                c["name"]: (
                    str(c["type"]),
                    bool(c["nullable"]),
                    None if c.get("default") is None else str(c["default"]).strip("'\""),
                    bool(c.get("autoincrement")),
                )
                for c in insp.get_columns(table)
            }
            indexes = {
                (ix["name"], tuple(ix["column_names"]), bool(ix["unique"]))
                for ix in insp.get_indexes(table)
            }
            fks = sorted(
                (
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    tuple(fk["referred_columns"]),
                )
                for fk in insp.get_foreign_keys(table)
            )
            out[table] = {
                "fks": fks,
                "columns": cols,
                "indexes": indexes,
                "pk": tuple(insp.get_pk_constraint(table)["constrained_columns"]),
            }
        return out
    finally:
        engine.dispose()


def _foreign_key_targets(sync_url: str) -> set[str]:
    engine = create_engine(sync_url)
    try:
        insp = inspect(engine)
        return {fk["referred_table"] for table in _TABLES for fk in insp.get_foreign_keys(table)}
    finally:
        engine.dispose()


def _drop_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            for table in reversed(_TABLES):
                conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
    finally:
        engine.dispose()


def _create_from_orm(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        wanted = [TiqoraBase.metadata.tables[t] for t in _TABLES]
        TiqoraBase.metadata.create_all(engine, tables=wanted)
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


@pytest.mark.parametrize("alembic_env", ["mariadb_znuny_url", "postgres_znuny_url"], indirect=True)
def test_webchat_migration_up_down_up_matches_orm(alembic_env: str) -> None:
    sync_url = alembic_env
    had_version_row = _had_version_row(sync_url)
    reset_tiqora_schema(sync_url)
    try:
        _migrate(_PREVIOUS)
        assert _present(sync_url) == set()

        _migrate(_WEBCHAT)
        assert _version(sync_url) == _WEBCHAT
        assert _present(sync_url) == set(_TABLES)
        first = _reflect(sync_url)
        assert {"ux_tiqora_chat_message_seq", "ux_tiqora_chat_message_client"} <= {
            name for name, _, _ in first["tiqora_chat_message"]["indexes"]
        }
        assert first["tiqora_chat_agent_state"]["pk"] == ("user_id",)
        assert ("ix_tiqora_chat_session_config", ("config_id",), False) in first[
            "tiqora_chat_session"
        ]["indexes"]
        if "mysql" in sync_url:
            columns = first["tiqora_chat_config"]["columns"]
            assert columns["prechat_fields"][0].lower() == "mediumtext"
            assert columns["topics"][0].lower() == "text"

        _migrate(_PREVIOUS, down=True)
        assert _present(sync_url) == set()
        assert _version(sync_url) == _PREVIOUS

        _migrate("head")
        assert _present(sync_url) == set(_TABLES)
        head_schema = _reflect(sync_url)
        # 20261010_0073 adds columns and one index on top of the phase-1 schema ...
        assert {"bot_name", "identity_secret", "show_on_portal"} <= set(
            head_schema["tiqora_chat_config"]["columns"]
        )
        assert {"last_visitor_seq", "bot_claim_owner", "handoff_reason"} <= set(
            head_schema["tiqora_chat_session"]["columns"]
        )
        assert ("ix_tiqora_chat_session_bot", ("status", "bot_claimed_at"), False) in head_schema[
            "tiqora_chat_session"
        ]["indexes"]
        # ... and its downgrade restores the phase-1 schema exactly
        _migrate(_WEBCHAT2_PARENT, down=True)
        assert _version(sync_url) == _WEBCHAT2_PARENT
        assert _reflect(sync_url) == first
        _migrate("head")
        assert _reflect(sync_url) == head_schema

        # every foreign key (there are none by design) targets a tiqora_ table
        assert all(t.startswith("tiqora_") for t in _foreign_key_targets(sync_url))

        # the ORM builds the very same schema
        _drop_tables(sync_url)
        _create_from_orm(sync_url)
        assert _reflect(sync_url) == head_schema
    finally:
        _migrate("head")
        delete_migration_seed_rows(sync_url)
        if not had_version_row:
            engine = create_engine(sync_url)
            try:
                with engine.begin() as conn:
                    conn.execute(text("DELETE FROM tiqora_alembic_version"))
            finally:
                engine.dispose()
