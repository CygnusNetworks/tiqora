"""Package store in the database (both dialects).

Band 62040-62059: plugin ids ``org.example.plg6204x``/``plg6205x``, hashes start ``62040``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugins_state_helpers import T0, to_async_url
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraPlugin, TiqoraPluginPackage
from tiqora.plugins.inspect import PackageReport
from tiqora.plugins.packages_store import (
    delete_package,
    get_package,
    get_package_data,
    installed_runtime_rows,
    keep_shas,
    mark_package_installed,
    purge_stale_packages,
    store_package,
    unmark_packages,
)
from tiqora.plugins.signing import TrustLevel

pytestmark = pytest.mark.db

PID = "org.example.plg62040"
PID2 = "org.example.plg62041"
LIKES = ("org.example.plg6204%", "org.example.plg6205%")


def sha(n: int) -> str:
    return f"62040{n:059d}"


def report(n: int, plugin_id: str = PID, version: str = "1.0.0", **kw: object) -> PackageReport:
    base: dict[str, object] = dict(
        sha256=sha(n),
        size_bytes=3,
        plugin_id=plugin_id,
        short_id="plgsix",
        name="Test",
        version=version,
        publisher="Example GmbH",
        publisher_id="org.example",
        description="",
        license=None,
        homepage=None,
        support=None,
        categories=[],
        changelog=None,
        trust=TrustLevel.UNSIGNED,
        key_id=None,
        signer=None,
        capabilities=[],
        problems=[],
        warnings=[],
        installed_version=None,
        action="install",
        installable=True,
    )
    base.update(kw)
    return PackageReport(**base)  # type: ignore[arg-type]


async def _clean(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        for like in LIKES:
            await s.execute(
                delete(TiqoraPluginPackage).where(TiqoraPluginPackage.plugin_id.like(like))
            )
            await s.execute(delete(TiqoraPlugin).where(TiqoraPlugin.id.like(like)))
        await s.commit()


@pytest.fixture(name="factory", params=["mariadb_znuny_url", "postgres_znuny_url"])
async def factory_fixture(
    request: pytest.FixtureRequest,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(to_async_url(request.getfixturevalue(request.param)))
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: TiqoraBase.metadata.create_all(c, checkfirst=True))
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _clean(sm)
    try:
        yield sm
    finally:
        await _clean(sm)
        await engine.dispose()


async def _put(
    s: AsyncSession, n: int, plugin_id: str = PID, *, at: datetime = T0, version: str = "1.0.0"
) -> TiqoraPluginPackage:
    return await store_package(
        s,
        data=b"abc",
        report=report(n, plugin_id, version),
        manifest_text="m",
        source="upload",
        repository_id=None,
        uploaded_by=7,
        now=at,
    )


def _plugin(plugin_id: str, package_sha: str | None, source: str = "upload") -> TiqoraPlugin:
    return TiqoraPlugin(
        id=plugin_id,
        name=plugin_id,
        version="1.0.0",
        enabled=True,
        source=source,
        package_sha256=package_sha,
        installed_at=T0,
        updated_at=T0,
    )


async def test_store_is_idempotent_by_hash(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        first = await _put(s, 1)
        assert first.size_bytes == 3 and first.uploaded_by == 7 and first.installed_at is None
        again = await store_package(
            s,
            data=b"abc",
            report=report(1, trust=TrustLevel.LOCAL, key_id="k1"),
            manifest_text="m",
            source="upload",
            repository_id=None,
            uploaded_by=9,
            now=T0 + timedelta(days=1),
        )
        assert again is first
        assert again.signature_status == "local" and again.key_id == "k1"
        assert '"trust":"local"' in again.report
        assert again.uploaded_by == 7 and again.uploaded_at == T0  # original upload kept
        await s.commit()
    async with factory() as s:
        rows = (
            (
                await s.execute(
                    select(TiqoraPluginPackage).where(TiqoraPluginPackage.plugin_id == PID)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert await get_package_data(s, sha(1)) == b"abc"
        assert await get_package_data(s, sha(99)) is None


async def test_get_package_does_not_load_blob(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await _put(s, 2)
        await s.commit()
    async with factory() as s:
        row = await get_package(s, sha(2))
        assert row is not None and row.version == "1.0.0"
        assert "data" in sa_inspect(row).unloaded
        assert await get_package(s, sha(98)) is None


async def test_delete_refuses_installed(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await _put(s, 3)
        await _put(s, 4, version="1.1.0")
        s.add(_plugin(PID, sha(3)))
        await s.flush()
        assert await delete_package(s, sha(3)) is False
        assert await delete_package(s, sha(4)) is True
        assert await delete_package(s, sha(4)) is False  # already gone
        await s.commit()
    async with factory() as s:
        assert await get_package(s, sha(3)) is not None
        assert await get_package(s, sha(4)) is None


async def test_purge_stale_only_never_installed_and_old(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    cutoff = T0 + timedelta(days=10)
    old = T0
    async with factory() as s:
        await _put(s, 5, at=old)  # old, never installed: purged
        await _put(s, 6, at=old, version="1.1.0")  # old, installed before: kept
        await mark_package_installed(s, sha(6), now=old)
        await _put(s, 7, at=cutoff + timedelta(days=1), version="1.2.0")  # new: kept
        await _put(s, 8, PID2, at=old)  # old, never installed but referenced: kept
        s.add(_plugin(PID2, sha(8)))
        await s.flush()
        assert await purge_stale_packages(s, older_than=cutoff) == 1
        assert await purge_stale_packages(s, older_than=cutoff) == 0
        await s.commit()
    async with factory() as s:
        assert await get_package(s, sha(5)) is None
        for n in (6, 7, 8):
            assert await get_package(s, sha(n)) is not None


async def test_installed_runtime_rows_and_marks(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await _put(s, 9)
        s.add(_plugin(PID, sha(9)))
        s.add(_plugin(PID2, None, source="image"))
        await s.flush()
        rows = [
            r.id for r in await installed_runtime_rows(s) if r.id.startswith("org.example.plg6204")
        ]
        assert rows == [PID]
        await mark_package_installed(s, sha(9), now=T0)
        await s.flush()
        s.expire_all()
        row = await get_package(s, sha(9))
        assert row is not None and row.installed_at == T0
        await unmark_packages(s, PID)
        s.expire_all()
        row = await get_package(s, sha(9))
        assert row is not None and row.installed_at is None
        await s.commit()


async def test_keep_shas_current_and_previous(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        for n, day in ((10, 1), (11, 2), (12, 3)):
            await _put(s, n, at=T0, version=f"1.{n}.0")
            await mark_package_installed(s, sha(n), now=T0 + timedelta(days=day))
        await _put(s, 13, version="1.13.0")  # uploaded, never installed: not kept
        await _put(s, 14, PID2)
        await mark_package_installed(s, sha(14), now=T0)
        s.add(_plugin(PID, sha(12)))
        s.add(_plugin(PID2, sha(14)))
        await s.flush()
        keep = await keep_shas(s)
        assert keep[PID] == [sha(12), sha(11)]  # current + newest previous, not sha(10)
        assert keep[PID2] == [sha(14)]
        await s.commit()
