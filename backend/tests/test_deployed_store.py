"""Deployed revision store: record, load newest, cache, no implicit commit.

Revision ids are autoincrement, so the cleanup deletes the rows this module created
by id. Seed band 881500-881699 (rulings F8) is reserved for this module.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraAclRevision, TiqoraProcessRevision
from tiqora.deployed import store
from tiqora.deployed.store import (
    clear_revision_cache,
    latest_revision_info,
    load_active_revision,
    record_revision,
)
from tiqora.process.dump import dump_checksum, dump_entity_count

_CREATED: list[tuple[str, str, int]] = []  # (sync_url, table, id)


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


@pytest.fixture(scope="module", autouse=True)
def _delete_created_rows() -> Generator[None, None, None]:
    yield
    for sync_url in {u for u, _, _ in _CREATED}:
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            for url, table, rid in _CREATED:
                if url == sync_url:
                    conn.execute(text(f"DELETE FROM {table} WHERE id = :i"), {"i": rid})
        engine.dispose()
    _CREATED.clear()


class _Ctx:
    def __init__(self, session: AsyncSession, sync_url: str) -> None:
        self.session = session
        self.sync_url = sync_url

    async def record(self, kind: str, **kw: Any) -> store.RevisionInfo:
        info = await record_revision(self.session, kind, **kw)  # type: ignore[arg-type]
        _CREATED.append((self.sync_url, f"tiqora_{kind}_revision", info.id))
        return info


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def ctx(request: pytest.FixtureRequest) -> AsyncIterator[_Ctx]:
    sync_url: str = request.getfixturevalue(request.param)
    sync_engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(
        sync_engine,
        tables=[TiqoraProcessRevision.__table__, TiqoraAclRevision.__table__],  # type: ignore[list-item]
        checkfirst=True,
    )
    sync_engine.dispose()
    engine = create_async_engine(_to_async_url(sync_url))
    clear_revision_cache()
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as s:
        yield _Ctx(s, sync_url)
    clear_revision_cache()
    await engine.dispose()


async def test_record_and_load_newest(ctx: _Ctx) -> None:
    session = ctx.session
    base = await latest_revision_info(session, "acl")
    d1: dict[str, Any] = {"Process": {}}
    d2: dict[str, Any] = {"Process": {"P-881500": {"Name": "Ä example"}}, "State": {"x": "y"}}
    r1 = await ctx.record("process", dump=d1, source="initial", deployed_by=None)
    r2 = await ctx.record("process", dump=d2, source="znuny", deployed_by=7)
    assert r2.id > r1.id
    assert (r2.source, r2.deployed_by, r2.kind) == ("znuny", 7, "process")
    assert r2.checksum == dump_checksum(d2)
    assert r2.entity_count == dump_entity_count(d2) == 1
    assert r1.entity_count == 0 and r1.deployed_by is None
    await session.commit()

    loaded = await load_active_revision(session, "process")
    assert loaded is not None
    assert loaded.info == r2
    assert loaded.dump == d2
    assert loaded.info.deployed_at.tzinfo is None
    assert await latest_revision_info(session, "process") == r2
    # ACL table is separate: untouched by process revisions.
    assert await latest_revision_info(session, "acl") == base


async def test_acl_kind_is_stored_in_its_own_table(ctx: _Ctx) -> None:
    session = ctx.session
    dump = {"ACL": {"a-881500": {"ID": 881500}}}
    info = await ctx.record("acl", dump=dump, source="tiqora", deployed_by=3)
    await session.commit()
    loaded = await load_active_revision(session, "acl")
    assert loaded is not None and loaded.info.id == info.id
    assert loaded.info.kind == "acl" and loaded.info.entity_count == 1
    assert loaded.dump == dump


async def test_cache_is_keyed_by_revision_id(ctx: _Ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    session = ctx.session
    calls = 0
    real_loads = json.loads

    def counting_loads(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return real_loads(*args, **kwargs)

    monkeypatch.setattr(store.json, "loads", counting_loads)
    await ctx.record("process", dump={"Process": {"a": {}}}, source="znuny", deployed_by=None)
    await session.commit()

    first = await load_active_revision(session, "process")
    second = await load_active_revision(session, "process")
    assert calls == 1
    assert first is second

    new = await ctx.record("process", dump={"Process": {"b": {}}}, source="tiqora", deployed_by=1)
    await session.commit()
    third = await load_active_revision(session, "process")
    assert calls == 2
    assert third is not None and third.info.id == new.id
    assert third.dump == {"Process": {"b": {}}}


async def test_record_does_not_commit(ctx: _Ctx) -> None:
    session = ctx.session
    before = await latest_revision_info(session, "process")
    info = await record_revision(
        session, "process", dump={"Process": {"gone": {}}}, source="initial", deployed_by=None
    )
    assert info.id is not None
    assert (await latest_revision_info(session, "process")) == info  # visible in-transaction
    await session.rollback()
    assert await latest_revision_info(session, "process") == before
    assert await load_active_revision(session, "process") is not None or before is None
