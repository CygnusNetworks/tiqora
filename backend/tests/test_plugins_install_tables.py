"""ORM tables of the runtime plugin install (both dialects).

Seed ids live in this plan's band 62000-62399; plugin ids / names contain ``plg62``.
Rows are deleted again by the module fixture.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugins_state_helpers import to_async_url
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraPlugin,
    TiqoraPluginData,
    TiqoraPluginPackage,
    TiqoraPluginRepository,
    TiqoraProcess,
    TiqoraRestart,
)
from tiqora.jobs.service import utc_now

pytestmark = pytest.mark.db

_PLUGIN = "org.example.plg62a"
_SHA = "a" * 64
_REASON = "plg62 test"


async def _wipe(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await s.execute(delete(TiqoraPlugin).where(TiqoraPlugin.id.like("org.example.plg62%")))
        await s.execute(
            delete(TiqoraPluginPackage).where(
                TiqoraPluginPackage.plugin_id.like("org.example.plg62%")
            )
        )
        await s.execute(
            delete(TiqoraPluginRepository).where(TiqoraPluginRepository.name.like("plg62%"))
        )
        await s.execute(delete(TiqoraProcess).where(TiqoraProcess.instance_id.like("plg62%")))
        await s.execute(delete(TiqoraRestart).where(TiqoraRestart.reason == _REASON))
        await s.execute(delete(TiqoraPluginData).where(TiqoraPluginData.short_id.like("plg62%")))
        await s.commit()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def factory(
    request: pytest.FixtureRequest,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(to_async_url(request.getfixturevalue(request.param)))
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: TiqoraBase.metadata.create_all(c, checkfirst=True))
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _wipe(sm)
    try:
        yield sm
    finally:
        await _wipe(sm)
        await engine.dispose()


def _package(**over: object) -> TiqoraPluginPackage:
    fields: dict[str, object] = {
        "sha256": _SHA,
        "plugin_id": _PLUGIN,
        "version": "1.0.0",
        "size_bytes": 3_000_000,
        "data": b"\x00\x01\x02" * 1_000_000,
        "manifest": "id = 'org.example.plg62a'",
        "report": "{}",
        "signature_status": "unsigned",
        "source": "upload",
        "uploaded_at": utc_now(),
    }
    fields.update(over)
    return TiqoraPluginPackage(**fields)


def _repo(name: str, url_key: str) -> TiqoraPluginRepository:
    now = utc_now()
    return TiqoraPluginRepository(
        name=name,
        url="https://plugins.example.org/index.json",
        url_key=url_key,
        enabled=True,
        origin="admin",
        created_at=now,
        updated_at=now,
    )


async def test_package_blob_roundtrip_and_deferred(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    blob = b"\x00\x01\x02" * 1_000_000  # 3 MB > 64 KiB proves LONGBLOB/BYTEA
    async with factory() as s:
        s.add(_package(data=blob))
        await s.commit()
    async with factory() as s:
        row = (await s.execute(select(TiqoraPluginPackage))).scalars().one()
        assert "data" in sa_inspect(row).unloaded
        assert row.installed_at is None and row.key_id is None
        data = (
            await s.execute(
                select(TiqoraPluginPackage.data).where(TiqoraPluginPackage.sha256 == _SHA)
            )
        ).scalar_one()
        assert data == blob


async def test_package_primary_key_is_the_hash(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as s:
        s.add(_package())
        await s.commit()
    async with factory() as s:
        s.add(_package())
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_plugin_new_columns_nullable(factory: async_sessionmaker[AsyncSession]) -> None:
    now = utc_now()
    async with factory() as s:
        s.add(
            TiqoraPlugin(
                id=_PLUGIN,
                name="Hello",
                version="1.0.0",
                enabled=True,
                source="image",
                installed_at=now,
                updated_at=now,
            )
        )
        await s.commit()
    async with factory() as s:
        row = await s.get(TiqoraPlugin, _PLUGIN)
        assert row is not None
        assert row.signature_status == "unsigned"
        for col in (
            "short_id",
            "package_sha256",
            "repository_id",
            "key_id",
            "signer",
            "installed_by",
            "migrated_version",
            "migrated_head",
            "migrated_at",
            "migration_error",
        ):
            assert getattr(row, col) is None, col


async def test_repository_url_key_unique(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        s.add(_repo("plg62 one", "b" * 64))
        await s.commit()
        row = (await s.execute(select(TiqoraPluginRepository))).scalars().first()
        assert row is not None and row.index_verified is False and row.index_json is None
    async with factory() as s:
        s.add(_repo("plg62 two", "b" * 64))
        with pytest.raises(IntegrityError):
            await s.commit()
    async with factory() as s:
        s.add(_repo("plg62 three", "c" * 64))
        await s.commit()


async def test_restart_generation_autoincrements(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    now = utc_now()
    async with factory() as s:
        first = TiqoraRestart(requested_at=now, reason=_REASON, roles="api,worker")
        s.add(first)
        await s.commit()
        second = TiqoraRestart(requested_at=now, reason=_REASON, roles="api")
        s.add(second)
        await s.commit()
        assert 0 < first.generation < second.generation
        assert first.finished_at is None and first.failed_message is None


async def test_process_row_roundtrip(factory: async_sessionmaker[AsyncSession]) -> None:
    now = utc_now()
    iid = "plg62:host.test:42:abcd1234"
    async with factory() as s:
        s.add(
            TiqoraProcess(
                instance_id=iid,
                role="ai-worker",
                hostname="host.test",
                pid=42,
                boot_generation=0,
                started_at=now,
                heartbeat_at=now,
                loaded_packages='["org.example.plg62a@' + _SHA + '"]',
            )
        )
        await s.commit()
    async with factory() as s:
        row = await s.get(TiqoraProcess, iid)
        assert row is not None
        assert (row.role, row.pid, row.boot_generation, row.exiting_at) == (
            "ai-worker",
            42,
            0,
            None,
        )
        assert row.loaded_packages is not None and _SHA in row.loaded_packages


async def test_plugin_data_owner_is_unique_per_short_id(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    now = utc_now()
    async with factory() as s:
        s.add(TiqoraPluginData(short_id="plg62x", plugin_id=_PLUGIN, kept_at=now))
        await s.commit()
    async with factory() as s:
        s.add(TiqoraPluginData(short_id="plg62x", plugin_id="org.example.plg62b", kept_at=now))
        with pytest.raises(IntegrityError):
            await s.commit()
