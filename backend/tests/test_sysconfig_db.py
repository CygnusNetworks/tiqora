"""SysConfig integration against a seeded testcontainer DB."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.znuny.sysconfig import ZNUNY_SETTING_DEFAULTS, SysConfig, yaml_encode_effective

pytestmark = pytest.mark.db

NOW = datetime(2024, 1, 1, 12, 0, 0)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    if sync_url.startswith("mysql://"):
        return sync_url.replace("mysql://", "mysql+aiomysql://", 1)
    return sync_url


def _seed_sysconfig(sync_url: str) -> None:
    """Seed sysconfig rows on top of Znuny initial_insert.

    ``users`` id 1 (root@localhost) and ``valid`` id 1 already exist from
    initial_insert; create_by/change_by=1 satisfy schema-post FKs.
    """
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        # YAML text as str so both MySQL LONGBLOB and PG TEXT accept the bind.
        default_val = yaml_encode_effective("10").decode("utf-8")
        modified_val = yaml_encode_effective("42").decode("utf-8")
        hook_val = yaml_encode_effective("Ticket#").decode("utf-8")

        conn.execute(
            text(
                """
                INSERT INTO sysconfig_default (
                    id, name, description, navigation,
                    is_invisible, is_readonly, is_required, is_valid, has_configlevel,
                    user_modification_possible, user_modification_active,
                    xml_content_raw, xml_content_parsed, xml_filename, effective_value,
                    is_dirty, exclusive_lock_guid,
                    create_time, create_by, change_time, change_by
                ) VALUES (
                    1, 'SystemID', :desc, 'Core',
                    0, 0, 1, 1, 0,
                    0, 0,
                    :desc, :desc, 'Framework.xml', :eff,
                    0, '0',
                    :t, 1, :t, 1
                )
                """
            ),
            {"desc": "SystemID", "eff": default_val, "t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO sysconfig_default (
                    id, name, description, navigation,
                    is_invisible, is_readonly, is_required, is_valid, has_configlevel,
                    user_modification_possible, user_modification_active,
                    xml_content_raw, xml_content_parsed, xml_filename, effective_value,
                    is_dirty, exclusive_lock_guid,
                    create_time, create_by, change_time, change_by
                ) VALUES (
                    2, 'Ticket::Hook', :desc, 'Core::Ticket',
                    0, 0, 1, 1, 0,
                    0, 0,
                    :desc, :desc, 'Ticket.xml', :eff,
                    0, '0',
                    :t, 1, :t, 1
                )
                """
            ),
            {"desc": "Hook", "eff": hook_val, "t": NOW},
        )
        # System-wide modified override for SystemID
        conn.execute(
            text(
                """
                INSERT INTO sysconfig_modified (
                    id, sysconfig_default_id, name, user_id, is_valid,
                    user_modification_active, effective_value, is_dirty, reset_to_default,
                    create_time, create_by, change_time, change_by
                ) VALUES (
                    1, 1, 'SystemID', NULL, 1,
                    0, :eff, 0, 0,
                    :t, 1, :t, 1
                )
                """
            ),
            {"eff": modified_val, "t": NOW},
        )
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_sysconfig_modified_overrides_default(
    url_fixture: str,
    request: pytest.FixtureRequest,
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _seed_sysconfig(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        cfg = SysConfig(session, ttl_seconds=60)
        assert await cfg.system_id() == "42"
        assert await cfg.ticket_hook() == "Ticket#"
        # Missing keys fall back to code defaults
        assert await cfg.fqdn() == ZNUNY_SETTING_DEFAULTS["FQDN"]
    await engine.dispose()
