"""Znuny ``sessions`` maintenance: expiry purge, customer revocation, worker tick (both dialects).

Session ids use the prefix GI8961; every row is deleted again after the module.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.znuny.sessions import (
    delete_agent_sessions,
    delete_customer_sessions,
    purge_expired_sessions,
)
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

_SEEDED: list[str] = []
MAX_TIME, MAX_IDLE = 57600, 7200


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in set(_SEEDED):
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM sessions WHERE session_id LIKE 'GI8961%'"))
        engine.dispose()
    _SEEDED.clear()


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _put(conn: Any, sid: str, **data: str) -> None:
    for key, val in data.items():
        conn.execute(
            text(
                "INSERT INTO sessions (session_id, data_key, data_value, serialized)"
                " VALUES (:s, :k, :v, 0)"
            ),
            {"s": sid, "k": key, "v": val},
        )


def _seed(sync_url: str) -> int:
    _SEEDED.append(sync_url)
    now = int(time.time())
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM sessions WHERE session_id LIKE 'GI8961%'"))
        fresh = {"UserLastRequest": str(now - 60), "UserSessionStart": str(now - 60)}
        _put(conn, "GI8961FRESH", UserID="8961", UserType="User", **fresh)
        _put(
            conn,
            "GI8961IDLE",
            UserID="8961",
            UserType="User",
            UserLastRequest=str(now - MAX_IDLE - 5),
            UserSessionStart=str(now - MAX_IDLE - 5),
        )
        _put(
            conn,
            "GI8961OLD",
            UserID="8961",
            UserType="User",
            UserLastRequest=str(now - 5),
            UserSessionStart=str(now - MAX_TIME - 5),
        )
        _put(conn, "GI8961NOSTAMP", UserID="8961", UserType="User")
        _put(conn, "GI8961BADSTAMP", UserLastRequest="x", UserSessionStart="y")
        # Customer whose login equals an agent id string must not match the agent.
        _put(conn, "GI8961CUST", UserID="cust8961@example.test", UserType="Customer", **fresh)
        _put(conn, "GI8961CUST2", UserID="other8961@example.test", UserType="Customer", **fresh)
        _put(conn, "GI8961TRICK", UserID="cust8961@example.test", UserType="User", **fresh)
    engine.dispose()
    return now


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def sync_url(request: pytest.FixtureRequest) -> str:
    url: str = request.getfixturevalue(request.param)
    _seed(url)
    return url


async def _ids(sync_url: str) -> set[str]:
    engine = create_async_engine(_async_url(sync_url))
    try:
        async with async_sessionmaker(engine)() as s:
            rows = await s.execute(
                text("SELECT DISTINCT session_id FROM sessions WHERE session_id LIKE 'GI8961%'")
            )
            return {str(r[0]) for r in rows.all()}
    finally:
        await engine.dispose()


async def test_purge_deletes_only_expired_sessions(sync_url: str) -> None:
    engine = create_async_engine(_async_url(sync_url))
    try:
        async with async_sessionmaker(engine)() as s:
            deleted = await purge_expired_sessions(s, now=int(time.time()), sysconfig=SysConfig(s))
            await s.commit()
        assert deleted >= 3
    finally:
        await engine.dispose()
    left = await _ids(sync_url)
    # A row-set without any stamp is invisible to Znuny's query and stays.
    assert left == {"GI8961FRESH", "GI8961NOSTAMP", "GI8961CUST", "GI8961CUST2", "GI8961TRICK"}


async def test_purge_boundary_is_inclusive_like_znuny(sync_url: str) -> None:
    now = int(time.time())
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        _put(
            conn,
            "GI8961EDGE",
            UserLastRequest=str(now - MAX_IDLE),
            UserSessionStart=str(now - MAX_IDLE),
        )
    engine.dispose()
    aengine = create_async_engine(_async_url(sync_url))
    try:
        async with async_sessionmaker(aengine)() as s:
            await purge_expired_sessions(s, now=now, sysconfig=SysConfig(s))
            await s.commit()
    finally:
        await aengine.dispose()
    assert "GI8961EDGE" not in await _ids(sync_url)  # DB.pm: ValidIdleTime <= 0


async def test_customer_revocation_is_exact_and_type_scoped(sync_url: str) -> None:
    engine = create_async_engine(_async_url(sync_url))
    try:
        async with async_sessionmaker(engine)() as s:
            assert await delete_customer_sessions(s, "cust8961@example.test") == 1
            await s.commit()
    finally:
        await engine.dispose()
    left = await _ids(sync_url)
    assert "GI8961CUST" not in left
    assert {"GI8961CUST2", "GI8961TRICK", "GI8961FRESH"} <= left


async def test_agent_revocation_ignores_customers_with_same_id_string(sync_url: str) -> None:
    engine = create_async_engine(_async_url(sync_url))
    try:
        async with async_sessionmaker(engine)() as s:
            assert await delete_agent_sessions(s, 8961) >= 1
            await s.commit()
    finally:
        await engine.dispose()
    left = await _ids(sync_url)
    assert "GI8961FRESH" not in left
    assert {"GI8961CUST", "GI8961CUST2"} <= left


async def test_worker_tick_runs_purge(sync_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.worker import znuny_session_purge as tick_mod

    engine = create_async_engine(_async_url(sync_url))
    monkeypatch.setattr(
        tick_mod, "get_session_factory", lambda: async_sessionmaker(engine, expire_on_commit=False)
    )
    try:
        result = await tick_mod.run_znuny_session_purge_tick()
    finally:
        await engine.dispose()
    assert result["deleted"] >= 3
    assert "GI8961IDLE" not in await _ids(sync_url)
    assert "GI8961FRESH" in await _ids(sync_url)


def test_purge_is_registered_in_worker_and_catalog() -> None:
    import inspect

    from tiqora.worker import __main__ as worker_main
    from tiqora.worker.services import DAEMON_SERVICES_BY_SLUG

    svc = DAEMON_SERVICES_BY_SLUG["znuny_session_purge"]
    assert (svc.toggleable, svc.enabled_key, svc.default_enabled) == (False, None, True)
    assert 'interval_loop(\n            "znuny_session_purge"' in inspect.getsource(worker_main)


async def _purge(sync_url: str, now: int, chunk: int | None = None) -> tuple[int, int]:
    from tiqora.znuny import sessions as mod

    engine = create_async_engine(_async_url(sync_url))
    commits = 0
    old_chunk = mod._CHUNK
    if chunk:
        mod._CHUNK = chunk
    try:
        async with async_sessionmaker(engine)() as s:
            real_commit = s.commit

            async def counting() -> None:
                nonlocal commits
                commits += 1
                await real_commit()

            s.commit = counting  # type: ignore[method-assign]
            deleted = await purge_expired_sessions(s, now=now, sysconfig=SysConfig(s))
    finally:
        mod._CHUNK = old_chunk
        await engine.dispose()
    return deleted, commits


async def test_zero_or_empty_stamp_counts_as_now_not_expired(sync_url: str) -> None:
    now = int(time.time())
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        _put(conn, "GI8961ZERO", UserSessionStart="0", UserLastRequest=str(now - 60))
        _put(conn, "GI8961EMPTY", UserSessionStart="", UserLastRequest="")
        _put(conn, "GI8961ONLYIDLE", UserLastRequest=str(now - MAX_IDLE - 1))
    engine.dispose()
    await _purge(sync_url, now)
    left = await _ids(sync_url)
    assert {"GI8961ZERO", "GI8961EMPTY"} <= left
    assert "GI8961ONLYIDLE" not in left  # real idle stamp, start missing -> now


async def test_last_row_per_key_wins(sync_url: str) -> None:
    now = int(time.time())
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for sid, order in (
            ("GI8961DUPOK", (now - MAX_IDLE - 9, now - 5)),
            ("GI8961DUPOLD", (now - 5, now - MAX_IDLE - 9)),
        ):
            for val in order:
                _put(conn, sid, UserLastRequest=str(val))
    engine.dispose()
    await _purge(sync_url, now)
    left = await _ids(sync_url)
    assert "GI8961DUPOK" in left
    assert "GI8961DUPOLD" not in left


async def test_purge_commits_after_every_chunk(sync_url: str) -> None:
    now = int(time.time())
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for i in range(5):
            _put(conn, f"GI8961BULK{i}", UserLastRequest=str(now - MAX_IDLE - 1))
    engine.dispose()
    deleted, commits = await _purge(sync_url, now, chunk=2)
    assert deleted >= 5
    assert commits >= 1 + 3  # read transaction + one per chunk of 2 (5 -> 3 chunks)
    assert not {f"GI8961BULK{i}" for i in range(5)} & await _ids(sync_url)
