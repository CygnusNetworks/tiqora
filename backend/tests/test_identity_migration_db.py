"""Identity migrations 20261009_0064 (providers, links, managed assignments) and
20261009_0065 (customer TOTP/passkeys) on real databases.

Renumbered after the roadmap head 20261009_0063 at the wave-1 merge. Upgrades from
the previous head, checks the tables (and that ``config`` / ``metadata_xml`` hold
large SAML metadata on MariaDB), downgrades back and upgrades again. Sync test on
purpose: Alembic's env.py runs its own event loop. Leaves the schema at head with
no rows of its own (the tables are shared with every other module).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from tests._schema_reset import reset_tiqora_schema
from tiqora.cli.migrate import build_alembic_config
from tiqora.config import get_settings

pytestmark = pytest.mark.db

_PREVIOUS = "20261009_0063"
_IDENTITY = "20261009_0064"
_TWO_FACTOR = "20261009_0065"
_TABLES = {
    "tiqora_identity_provider",
    "tiqora_identity_link",
    "tiqora_identity_managed_assignment",
    "tiqora_customer_totp",
    "tiqora_customer_passkey",
}
_SLUG = "idpmig-big"


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


def _delete_row(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        if "tiqora_identity_provider" in set(inspect(engine).get_table_names()):
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM tiqora_identity_provider WHERE slug = :s"), {"s": _SLUG}
                )
    finally:
        engine.dispose()


@pytest.mark.parametrize("alembic_env", ["mariadb_znuny_url", "postgres_znuny_url"], indirect=True)
def test_identity_migrations_upgrade_downgrade_and_hold_large_metadata(alembic_env: str) -> None:
    sync_url = alembic_env
    had_version_row = _had_version_row(sync_url)
    reset_tiqora_schema(sync_url)
    try:
        _migrate(_PREVIOUS)
        assert _tables(sync_url) == set()

        _migrate("head")
        assert _version(sync_url) == _TWO_FACTOR
        assert _tables(sync_url) == _TABLES

        engine = create_engine(sync_url)
        try:
            if "mysql" in sync_url:
                with engine.connect() as conn:
                    types = dict(
                        conn.execute(
                            text(
                                "SELECT column_name, data_type FROM information_schema.columns"
                                " WHERE table_schema = DATABASE()"
                                " AND table_name = 'tiqora_identity_provider'"
                                " AND column_name IN ('config', 'metadata_xml')"
                            )
                        ).all()
                    )
                assert {k.lower(): v.lower() for k, v in types.items()} == {
                    "config": "mediumtext",
                    "metadata_xml": "mediumtext",
                }
            # Pasted IdP metadata lives in ``config``: ~200 KB must round-trip (64 KiB
            # TEXT would raise in strict mode or truncate the JSON otherwise).
            big = '{"idp_metadata_xml": "' + "x" * 200_000 + '"}'
            now = datetime(2026, 10, 9, 12, 0, 0)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO tiqora_identity_provider (slug, kind, source, name, audience,"
                        " enabled, sort, config, match_by, require_sso_opt_in, attribute_map,"
                        " jit_enabled, jit_defaults, role_rules, sync_mode, create_by,"
                        " create_time, change_by, change_time) VALUES (:slug, 'saml', 'db',"
                        " 'Big', 'agent', :f, 0, :cfg, 'login', :f, '{}', :f, '{}', '[]',"
                        " 'managed', 1, :t, 1, :t)"
                    ),
                    {"slug": _SLUG, "cfg": big, "f": False, "t": now},
                )
            with engine.connect() as conn:
                stored = conn.execute(
                    text("SELECT config FROM tiqora_identity_provider WHERE slug = :s"),
                    {"s": _SLUG},
                ).scalar_one()
            assert stored == big
        finally:
            engine.dispose()
        _delete_row(sync_url)

        _migrate(_IDENTITY, down=True)
        assert _tables(sync_url) == _TABLES - {"tiqora_customer_totp", "tiqora_customer_passkey"}
        _migrate(_PREVIOUS, down=True)
        assert _tables(sync_url) == set()
        assert _version(sync_url) == _PREVIOUS

        _migrate("head")
        assert _tables(sync_url) == _TABLES
    finally:
        _migrate("head")
        _delete_row(sync_url)
        if not had_version_row:
            _drop_version_row(sync_url)
