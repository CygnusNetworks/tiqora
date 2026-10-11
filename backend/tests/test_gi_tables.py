"""GI debugger tables (``tiqora_gi_debug_entry`` / ``_content``) on MariaDB and PostgreSQL.

Seed values use band 51000-51019 (``webservice_id`` / ``ticket_id`` are plain numbers, no FK).
Entry and content ids are autoincrement: every row this module adds is deleted again via
``tests._row_cleanup`` marks.
"""

from __future__ import annotations

import json

import pytest
import yaml
from sqlalchemy import create_engine, delete, func, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._gi_fixtures import cleanup_webservices, insert_webservice, requester_config
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraGiDebugContent, TiqoraGiDebugEntry, TiqoraSettings
from tiqora.jobs.service import utc_now

pytestmark = pytest.mark.db

WS_ID = 51001
TICKET_ID = 51002
FIXTURE_WS_ID = 51003
_TABLES = ("tiqora_gi_debug_content", "tiqora_gi_debug_entry")


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _create_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        TiqoraBase.metadata.create_all(
            engine, tables=[TiqoraGiDebugEntry.__table__, TiqoraGiDebugContent.__table__]
        )
    finally:
        engine.dispose()


def _entry(communication_id: str) -> TiqoraGiDebugEntry:
    return TiqoraGiDebugEntry(
        communication_id=communication_id,
        communication_type="Requester",
        remote_ip=None,
        webservice_id=WS_ID,
        create_time=utc_now(),
        name="SendTicket",
        ticket_id=TICKET_ID,
        replay=json.dumps({"invoker": "SendTicket", "data": {"TicketID": TICKET_ID}}),
    )


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_entry_content_round_trip_and_cascade(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _create_tables(sync_url)
    marks = snapshot_marks(sync_url, _TABLES)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            entry = _entry("a" * 32)
            session.add(entry)
            await session.flush()
            for level in ("debug", "error"):
                session.add(
                    TiqoraGiDebugContent(
                        entry_id=entry.id,
                        debug_level=level,
                        subject=f"Subject {level}",
                        content="Grüße €".encode(),
                        create_time=utc_now(),
                    )
                )
            await session.commit()
            entry_id = entry.id

        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        select(TiqoraGiDebugContent)
                        .where(TiqoraGiDebugContent.entry_id == entry_id)
                        .order_by(TiqoraGiDebugContent.id)
                    )
                )
                .scalars()
                .all()
            )
            assert [r.debug_level for r in rows] == ["debug", "error"]
            assert [bytes(r.content or b"").decode() for r in rows] == ["Grüße €", "Grüße €"]
            stored = await session.get(TiqoraGiDebugEntry, entry_id)
            assert stored is not None
            assert stored.communication_id == "a" * 32
            assert stored.ticket_id == TICKET_ID
            assert json.loads(stored.replay or "{}")["invoker"] == "SendTicket"

        async with factory() as session:
            session.add(_entry("a" * 32))
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()

        async with factory() as session:
            await session.execute(
                delete(TiqoraGiDebugEntry).where(TiqoraGiDebugEntry.id == entry_id)
            )
            await session.commit()

        async with factory() as session:
            left = (
                await session.execute(
                    select(func.count())
                    .select_from(TiqoraGiDebugContent)
                    .where(TiqoraGiDebugContent.entry_id == entry_id)
                )
            ).scalar_one()
            assert left == 0
    finally:
        await engine.dispose()
        delete_above_marks(sync_url, marks)


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
def test_webservice_fixture_round_trip_and_cleanup(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    config = requester_config(
        invokers={"SendTicket": {"Type": "Ticket::Generic"}},
        error_handling={"Retry": {"Type": "RequestRetry"}},
        transport_extra={"Timeout": "5"},
    )
    req = config["Requester"]
    assert req["Transport"]["Config"]["InvokerControllerMapping"] == {
        "SendTicket": {"Controller": "/Ticket/:TicketID"}
    }
    assert req["Transport"]["Config"]["Timeout"] == "5"
    assert req["Transport"]["Config"]["Host"] == "https://crm.example.org"
    assert req["ErrorHandlingPriority"] == ["Retry"]
    assert config["Provider"] == {"Transport": {"Type": ""}}

    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine, tables=[TiqoraSettings.__table__])
    try:
        with engine.begin() as conn:
            insert_webservice(
                conn, ws_id=FIXTURE_WS_ID, name="gi51Fixture", config=config, valid=False
            )
            conn.execute(
                insert(TiqoraSettings).values(
                    key=f"gi.webservice.{FIXTURE_WS_ID}.egress", value="{}"
                )
            )
        with engine.connect() as conn:
            stored, valid_id = conn.execute(
                text("SELECT config, valid_id FROM gi_webservice_config WHERE id = :id"),
                {"id": FIXTURE_WS_ID},
            ).one()
        raw = bytes(stored).decode() if isinstance(stored, bytes | memoryview) else stored
        assert yaml.safe_load(raw) == config
        assert valid_id == 2
    finally:
        with engine.begin() as conn:
            cleanup_webservices(conn, [FIXTURE_WS_ID])
        with engine.connect() as conn:
            left_ws = conn.execute(
                text("SELECT COUNT(*) FROM gi_webservice_config WHERE id = :id"),
                {"id": FIXTURE_WS_ID},
            ).scalar_one()
            left_settings = conn.execute(
                select(func.count())
                .select_from(TiqoraSettings)
                .where(TiqoraSettings.key.like(f"gi.webservice.{FIXTURE_WS_ID}.%"))
            ).scalar_one()
        engine.dispose()
    assert left_ws == 0 and left_settings == 0
