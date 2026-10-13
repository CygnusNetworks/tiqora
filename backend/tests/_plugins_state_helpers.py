"""Shared fixtures and helpers for the plugin state/bootstrap DB tests (both dialects)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraPlugin, TiqoraPluginHook, TiqoraPluginRuntime
from tiqora.plugins.discovery import DiscoveredPlugin
from tiqora.plugins.manifest import parse_manifest

T0 = datetime(2026, 10, 10, 8, 0)
T1 = T0 + timedelta(hours=1)

#: ids this suite owns: org.example.plg49a ... plg49z plus the fixture plugins
_OWN_PREFIX = "org.example.plg49"
_FIXTURE_IDS = ("org.example.hello", "org.example.broken", "org.example.slow")
_FIXTURE_IDS_LIKE = tuple(f"{i}%" for i in _FIXTURE_IDS)


def to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


async def _delete_own_rows(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        for model, col in (
            (TiqoraPluginHook, TiqoraPluginHook.plugin_id),
            (TiqoraPluginRuntime, TiqoraPluginRuntime.plugin_id),
            (TiqoraPlugin, TiqoraPlugin.id),
        ):
            await s.execute(
                delete(model).where(col.like(f"{_OWN_PREFIX}%") | col.in_(_FIXTURE_IDS))
            )
        await s.commit()


@pytest.fixture(name="db_url", params=["mariadb_znuny_url", "postgres_znuny_url"])
def db_url_fixture(request: pytest.FixtureRequest) -> str:
    return to_async_url(request.getfixturevalue(request.param))


@pytest.fixture(name="factory")
async def factory_fixture(db_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = db_url
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: TiqoraBase.metadata.create_all(c, checkfirst=True))
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _delete_own_rows(sm)
    try:
        yield sm
    finally:
        await _delete_own_rows(sm)
        await engine.dispose()


@pytest.fixture(name="session")
async def session_fixture(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as s:
        yield s
        await s.rollback()


@pytest.fixture(name="settings")
def settings_fixture(db_url: str) -> Settings:
    return Settings(environment="test", database_url=db_url)


def discovered(plugin_id: str, version: str = "1.0.0") -> DiscoveredPlugin:
    toml = (
        "manifest_version = 1\n"
        f'id = "{plugin_id}"\n'
        'short_id = "plgfortytwo"\n'
        f'name = "Test {plugin_id}"\n'
        f'version = "{version}"\n'
        'publisher = "Example GmbH"\n'
        'entry = "mod_unused:plugin"\n'
        'sdk = ">=0.2,<0.3"\n'
    )
    return DiscoveredPlugin(
        plugin_id=plugin_id,
        entry="mod_unused:plugin",
        dist_name=plugin_id.replace(".", "-"),
        dist_version=version,
        package_dir=Path("/nonexistent"),
        manifest=parse_manifest(toml),
        web_manifest=None,
        locales=(),
        problem=None,
    )


async def seed_plugin_row(
    factory: async_sessionmaker[AsyncSession], plugin_id: str, *, enabled: bool
) -> None:
    async with factory() as s:
        s.add(
            TiqoraPlugin(
                id=plugin_id,
                name=plugin_id,
                version="1.0.0",
                enabled=enabled,
                source="image",
                installed_at=T0,
                updated_at=T0,
            )
        )
        await s.commit()


async def runtime_rows(
    factory: async_sessionmaker[AsyncSession], plugin_id: str, role: str | None = None
) -> list[TiqoraPluginRuntime]:
    async with factory() as s:
        stmt = select(TiqoraPluginRuntime).where(TiqoraPluginRuntime.plugin_id == plugin_id)
        if role is not None:
            stmt = stmt.where(TiqoraPluginRuntime.role == role)
        return list((await s.execute(stmt)).scalars())


async def hook_row(factory: async_sessionmaker[AsyncSession], key: str) -> TiqoraPluginHook:
    async with factory() as s:
        return (
            await s.execute(select(TiqoraPluginHook).where(TiqoraPluginHook.hook_key == key))
        ).scalar_one()
