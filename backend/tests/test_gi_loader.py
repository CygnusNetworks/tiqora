"""``GiWebservices`` snapshot against real ``gi_webservice_config`` rows (both dialects).

Seed ids 51010-51019 (GI band); every row is deleted again after each test.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Generator
from datetime import datetime
from typing import Any

import pytest
import structlog
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._gi_fixtures import cleanup_webservices, insert_webservice, requester_config
from tiqora.gi.config import parse_requester
from tiqora.gi.loader import GiWebservices, load_gi_webservice
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

REQ_ID, PROVIDER_ID, DISABLED_ID, BROKEN_ID, HOST_ID = 51010, 51011, 51012, 51013, 51014
IDS = (REQ_ID, PROVIDER_ID, DISABLED_ID, BROKEN_ID, HOST_ID)
T1 = datetime(2026, 1, 2, 10, 0, 0)

INVOKERS: dict[str, Any] = {
    "SendTicket": {"Type": "Ticket::Generic", "Events": [{"Event": "TicketCreate"}]}
}


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def sync_url(request: pytest.FixtureRequest) -> Generator[str, None, None]:
    url: str = request.getfixturevalue(request.param)
    engine = create_engine(url)
    with engine.begin() as conn:
        cleanup_webservices(conn, IDS)
        insert_webservice(
            conn, ws_id=REQ_ID, name="GiLoaderReq", config=requester_config(invokers=INVOKERS)
        )
        insert_webservice(
            conn,
            ws_id=PROVIDER_ID,
            name="GiLoaderProvider",
            config={"Provider": {"Transport": {"Type": "HTTP::REST", "Config": {}}}},
        )
        insert_webservice(
            conn,
            ws_id=DISABLED_ID,
            name="GiLoaderDisabled",
            config=requester_config(invokers=INVOKERS),
            valid=False,
        )
    engine.dispose()
    yield url
    engine = create_engine(url)
    with engine.begin() as conn:
        cleanup_webservices(conn, IDS)
    engine.dispose()


async def _with_session(
    sync_url: str, fn: Callable[[AsyncSession], Coroutine[Any, Any, Any]]
) -> Any:
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return await fn(session)
    finally:
        await engine.dispose()


def _exec(sync_url: str, sql: str, **params: Any) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text(sql), params)
    engine.dispose()


async def test_all_returns_only_valid_requester_webservices(sync_url: str) -> None:
    snapshot = GiWebservices()

    async def run(session: AsyncSession) -> None:
        assert await snapshot.refresh(session, SysConfig(session)) is True
        ids = [ws.id for ws in snapshot.all()]
        assert REQ_ID in ids
        assert PROVIDER_ID not in ids and DISABLED_ID not in ids
        assert ids == sorted(ids)
        ws = snapshot.by_id(REQ_ID)
        assert ws is not None and snapshot.by_name("GiLoaderReq") is ws
        assert ws.requester.invokers["SendTicket"].supported
        assert ws.debugger.threshold == "debug"
        assert snapshot.by_id(PROVIDER_ID) is None

    await _with_session(sync_url, run)


async def test_refresh_follows_fingerprint(sync_url: str) -> None:
    snapshot = GiWebservices()

    async def first(session: AsyncSession) -> None:
        assert await snapshot.refresh(session, SysConfig(session)) is True
        assert await snapshot.refresh(session, SysConfig(session)) is False
        assert await snapshot.refresh(session, SysConfig(session), force=True) is True

    await _with_session(sync_url, first)
    _exec(
        sync_url,
        "UPDATE gi_webservice_config SET change_time = :t WHERE id = :id",
        t=T1,
        id=REQ_ID,
    )

    async def second(session: AsyncSession) -> None:
        old = snapshot.by_id(REQ_ID)
        assert await snapshot.refresh(session, SysConfig(session)) is True
        assert snapshot.by_id(REQ_ID) is not old
        assert await snapshot.refresh(session, SysConfig(session)) is False

    await _with_session(sync_url, second)


async def test_broken_yaml_does_not_hide_others(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        raw = "Requester: {a: [1, 2}"
        stored: bytes | str = raw if conn.dialect.name == "postgresql" else raw.encode()
        conn.execute(
            text(
                "INSERT INTO gi_webservice_config (id, name, config, valid_id, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (:id, 'GiLoaderBroken', :c, 1, :t, 1, :t, 1)"
            ),
            {"id": BROKEN_ID, "c": stored, "t": T1},
        )
    engine.dispose()
    snapshot = GiWebservices()

    async def run(session: AsyncSession) -> None:
        with structlog.testing.capture_logs() as logs:
            await snapshot.refresh(session, SysConfig(session))
        assert snapshot.by_id(BROKEN_ID) is None
        assert snapshot.by_id(REQ_ID) is not None
        assert any(e["event"] == "gi_webservice_invalid" and e["ws_id"] == BROKEN_ID for e in logs)

    await _with_session(sync_url, run)


async def test_unparsable_requester_logged_and_skipped(
    sync_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.api.compat.mapping import MappingError
    from tiqora.gi import loader

    real = parse_requester

    def flaky(config: dict[str, Any], *, huge_xml: bool) -> Any:
        if config["Requester"]["Transport"]["Config"]["Host"].endswith("bad.example.org"):
            raise MappingError("boom")
        return real(config, huge_xml=huge_xml)

    monkeypatch.setattr(loader, "parse_requester", flaky)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        insert_webservice(
            conn,
            ws_id=BROKEN_ID,
            name="GiLoaderBad",
            config=requester_config(host="https://bad.example.org", invokers=INVOKERS),
        )
    engine.dispose()
    snapshot = GiWebservices()

    async def run(session: AsyncSession) -> None:
        with structlog.testing.capture_logs() as logs:
            await snapshot.refresh(session, SysConfig(session))
        assert snapshot.by_id(BROKEN_ID) is None and snapshot.by_id(REQ_ID) is not None
        invalid = [e for e in logs if e["event"] == "gi_webservice_invalid"]
        assert len(invalid) == 1 and invalid[0]["ws_id"] == BROKEN_ID
        assert invalid[0]["name"] == "GiLoaderBad" and "boom" in invalid[0]["error"]

    await _with_session(sync_url, run)


async def test_fqdn_placeholder_replaced_from_sysconfig(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        insert_webservice(
            conn,
            ws_id=HOST_ID,
            name="GiLoaderHost",
            config=requester_config(host="https://<OTRS_CONFIG_FQDN>", invokers=INVOKERS),
        )
    engine.dispose()

    async def run(session: AsyncSession) -> None:
        sysconfig = SysConfig(session)
        fqdn = await sysconfig.get("FQDN")
        assert isinstance(fqdn, str) and fqdn
        snapshot = GiWebservices()
        await snapshot.refresh(session, sysconfig)
        ws = snapshot.by_id(HOST_ID)
        assert ws is not None and ws.requester.transport is not None
        assert ws.requester.transport.config["Host"] == f"https://{fqdn}"
        single = await load_gi_webservice(session, sysconfig, HOST_ID)
        assert single is not None and single.name == "GiLoaderHost"
        assert await load_gi_webservice(session, sysconfig, DISABLED_ID) is None
        assert await load_gi_webservice(session, sysconfig, PROVIDER_ID) is None
        assert await load_gi_webservice(session, sysconfig, 51019) is None

    await _with_session(sync_url, run)


async def test_unexpected_error_in_one_row_does_not_block_others(
    sync_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.gi import loader

    real = parse_requester

    def explode(config: dict[str, Any], *, huge_xml: bool) -> Any:
        if config["Requester"]["Transport"]["Config"]["Host"].endswith("bad.example.org"):
            raise RuntimeError("unexpected")
        return real(config, huge_xml=huge_xml)

    monkeypatch.setattr(loader, "parse_requester", explode)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        insert_webservice(
            conn,
            ws_id=BROKEN_ID,
            name="GiLoaderBoom",
            config=requester_config(host="https://bad.example.org", invokers=INVOKERS),
        )
    engine.dispose()
    snapshot = GiWebservices()

    async def run(session: AsyncSession) -> None:
        with structlog.testing.capture_logs() as logs:
            await snapshot.refresh(session, SysConfig(session))
        assert snapshot.by_id(BROKEN_ID) is None and snapshot.by_id(REQ_ID) is not None
        assert any(e["event"] == "gi_webservice_unexpected_error" for e in logs)

    await _with_session(sync_url, run)
