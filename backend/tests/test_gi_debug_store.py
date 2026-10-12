"""GI debug store on MariaDB and PostgreSQL (Tiqora tables + read-only Znuny debugger tables).

Seed band 51020-51059: webservices 51021/51022, tickets 51030/51031. Entry and content ids are
autoincrement: every row this module adds is deleted again via ``tests._row_cleanup`` marks.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._gi_fixtures import cleanup_webservices, insert_webservice, requester_config
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraGiDebugContent, TiqoraGiDebugEntry
from tiqora.gi.config import DebuggerConfig
from tiqora.gi.debug_store import (
    DbDebugSink,
    delete_debug_entries_for_tickets,
    get_entry,
    get_replay,
    purge_debug_entries,
    search_entries,
)
from tiqora.gi.debugger import GiDebugger
from tiqora.jobs.service import utc_now

pytestmark = pytest.mark.db

WS, OTHER_WS = 51021, 51022
TICKET, TICKET_OTHER = 51030, 51031
_TABLES = (
    "gi_debugger_entry_content",
    "gi_debugger_entry",
    "tiqora_gi_debug_content",
    "tiqora_gi_debug_entry",
)
_INVOKERS = {"SendTicket": {"Type": "Ticket::Generic", "Events": [{"Event": "TicketCreate"}]}}
BASE = datetime(2026, 6, 1, 12, 0, 0)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _setup(sync_url: str) -> dict[str, int]:
    engine = create_engine(sync_url)
    try:
        TiqoraBase.metadata.create_all(
            engine, tables=[TiqoraGiDebugEntry.__table__, TiqoraGiDebugContent.__table__]
        )
        with engine.begin() as conn:
            cleanup_webservices(conn, [WS, OTHER_WS])
    finally:
        engine.dispose()
    return snapshot_marks(sync_url, _TABLES)


def _seed_webservices(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            for ws, name in ((WS, "gi51-debug-a"), (OTHER_WS, "gi51-debug-b")):
                insert_webservice(
                    conn, ws_id=ws, name=name, config=requester_config(invokers=_INVOKERS)
                )
    finally:
        engine.dispose()


def _teardown(sync_url: str, marks: dict[str, int]) -> None:
    delete_above_marks(sync_url, marks)
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            cleanup_webservices(conn, [WS, OTHER_WS])
    finally:
        engine.dispose()


async def _add(sink: DbDebugSink, comm: str, level: str, summary: str, **kw) -> None:  # type: ignore[no-untyped-def]
    params = {
        "communication_id": comm,
        "communication_type": "Requester",
        "webservice_id": WS,
        "remote_ip": None,
        "name": "SendTicket",
        "ticket_id": None,
        "debug_level": level,
        "summary": summary,
        "content": f"content of {summary}",
    }
    params.update(kw)
    await sink.add(**params)


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_sink_writes_entry_once_and_contents_in_order(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    marks = _setup(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        sink = DbDebugSink(factory)
        replay = {"invoker": "SendTicket", "data": {"TicketID": TICKET}, "asynchronous": False}
        comm = "gi51-once".ljust(32, "0")
        await _add(sink, comm, "debug", "first", replay=replay)
        await _add(sink, comm, "error", "second", replay={"ignored": True}, ticket_id=TICKET)
        await _add(sink, comm, "notice", "third")
        async with factory() as session:
            entries = (
                (
                    await session.execute(
                        text("SELECT id FROM tiqora_gi_debug_entry WHERE communication_id = :c"),
                        {"c": comm},
                    )
                )
                .scalars()
                .all()
            )
            assert len(entries) == 1
            got = await get_entry(session, source="tiqora", entry_id=entries[0], webservice_id=WS)
            assert got is not None
            row, lines = got
            assert [(c.debug_level, c.subject, c.content) for c in lines] == [
                ("debug", "first", "content of first"),
                ("error", "second", "content of second"),
                ("notice", "third", "content of third"),
            ]
            assert row.max_level == "error" and row.ticket_id == TICKET
            assert row.can_resend is True and row.source == "tiqora"
            # replay is written only with the row that created the entry
            assert await get_replay(session, entry_id=row.id, webservice_id=WS) == replay
            assert (
                await get_entry(session, source="tiqora", entry_id=row.id, webservice_id=OTHER_WS)
                is None
            )
    finally:
        await engine.dispose()
        _teardown(sync_url, marks)


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_failed_call_below_threshold_keeps_replay_for_resend(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """threshold=error and the call fails: the entry has ``replay`` and can be resent."""
    sync_url: str = request.getfixturevalue(url_fixture)
    marks = _setup(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        replay = {"invoker": "SendTicket", "data": {"TicketID": TICKET}, "asynchronous": True}
        d = GiDebugger(
            sink=DbDebugSink(factory),
            config=DebuggerConfig("error", False),
            webservice_id=WS,
            communication_type="Requester",
            name="SendTicket",
            replay=replay,
        )
        await d.info("request sent")  # below the threshold: no row yet
        await d.error("transport failed", {"Password": "pw-1"})
        async with factory() as session:
            rows = await search_entries(session, webservice_id=WS, limit=10)
            assert len(rows) == 1 and rows[0].can_resend is True
            assert rows[0].max_level == "error"
            assert await get_replay(session, entry_id=rows[0].id, webservice_id=WS) == replay
            got = await get_entry(session, source="tiqora", entry_id=rows[0].id, webservice_id=WS)
            assert got is not None and "pw-1" not in (got[1][0].content or "")
    finally:
        await engine.dispose()
        _teardown(sync_url, marks)


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_sink_commits_independently_of_caller_rollback(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    marks = _setup(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        sink = DbDebugSink(factory)
        comm = "gi51-rollback".ljust(32, "0")
        async with factory() as caller:
            # The caller's own work (a debug row of another webservice stands in for a ticket).
            caller.add(
                TiqoraGiDebugEntry(
                    communication_id="gi51-caller".ljust(32, "0"),
                    communication_type="Provider",
                    webservice_id=OTHER_WS,
                    create_time=utc_now(),
                )
            )
            await caller.flush()
            await _add(sink, comm, "error", "kept")
            await caller.rollback()
        async with factory() as session:
            found = (
                (
                    await session.execute(
                        text(
                            "SELECT communication_id FROM tiqora_gi_debug_entry"
                            " WHERE communication_id IN (:a, :b)"
                        ),
                        {"a": comm, "b": "gi51-caller".ljust(32, "0")},
                    )
                )
                .scalars()
                .all()
            )
            assert list(found) == [comm]
            n = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM tiqora_gi_debug_content c WHERE c.entry_id IN"
                        " (SELECT id FROM tiqora_gi_debug_entry WHERE communication_id = :c)"
                    ),
                    {"c": comm},
                )
            ).scalar_one()
            assert n == 1
    finally:
        await engine.dispose()
        _teardown(sync_url, marks)


async def _seed_mixed(factory: async_sessionmaker[AsyncSession], dialect: str) -> None:
    """Tiqora entries at +1, +3, +5 min (+3 min also for Znuny: a tie) and Znuny at +2, +3, +4."""
    async with factory() as session:
        for minutes, comm, ctype, ip, level in (
            (1, "t1", "Requester", None, "debug"),
            (3, "t3", "Requester", "198.51.100.7", "error"),
            (5, "t5", "Provider", "198.51.100.8", "notice"),
        ):
            entry = TiqoraGiDebugEntry(
                communication_id=f"gi51-{comm}".ljust(32, "0"),
                communication_type=ctype,
                remote_ip=ip,
                webservice_id=WS,
                create_time=BASE + timedelta(minutes=minutes),
                name="SendTicket",
                ticket_id=TICKET,
                replay='{"invoker": "SendTicket"}' if ctype == "Requester" else None,
            )
            session.add(entry)
            await session.flush()
            session.add(
                TiqoraGiDebugContent(
                    entry_id=entry.id,
                    debug_level="debug",
                    subject="s",
                    content=b"x",
                    create_time=entry.create_time,
                )
            )
            session.add(
                TiqoraGiDebugContent(
                    entry_id=entry.id,
                    debug_level=level,
                    subject="s2",
                    content=b"y",
                    create_time=entry.create_time,
                )
            )
        for minutes, comm, ctype, ip, level in (
            (2, "z2", "Requester", None, "info"),
            (3, "z3", "Requester", "198.51.100.7", "notice"),
            (4, "z4", "Provider", "198.51.100.9", "error"),
        ):
            await session.execute(
                text(
                    "INSERT INTO gi_debugger_entry (communication_id, communication_type,"
                    " remote_ip, webservice_id, create_time)"
                    " VALUES (:c, :t, :ip, :ws, :ts)"
                ),
                {
                    "c": f"gi51-{comm}".ljust(32, "0"),
                    "t": ctype,
                    "ip": ip,
                    "ws": WS,
                    "ts": BASE + timedelta(minutes=minutes),
                },
            )
            zid = (
                await session.execute(
                    text("SELECT id FROM gi_debugger_entry WHERE communication_id = :c"),
                    {"c": f"gi51-{comm}".ljust(32, "0")},
                )
            ).scalar_one()
            await session.execute(
                text(
                    "INSERT INTO gi_debugger_entry_content (gi_debugger_entry_id, debug_level,"
                    " subject, content, create_time) VALUES (:e, :l, 'zs', :c, :ts)"
                ),
                {
                    "e": zid,
                    "l": level,
                    # Znuny: LONGBLOB on MariaDB, TEXT on PostgreSQL.
                    "c": (
                        f"Dumper {comm} ü"
                        if dialect == "postgres_znuny_url"
                        else f"Dumper {comm} ü".encode()
                    ),
                    "ts": BASE + timedelta(minutes=minutes),
                },
            )
        await session.commit()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_search_merges_tiqora_and_znuny_rows_by_time(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    marks = _setup(sync_url)
    _seed_webservices(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        await _seed_mixed(factory, url_fixture)
        async with factory() as session:

            def key(rows):  # type: ignore[no-untyped-def]
                return [(r.source, r.communication_id.rstrip("0")[5:]) for r in rows]

            page1 = await search_entries(session, webservice_id=WS, limit=3)
            # newest first: t5 (+5), z4 (+4), then the +3 tie: znuny sorts above tiqora
            assert key(page1) == [("tiqora", "t5"), ("znuny", "z4"), ("znuny", "z3")]
            last = page1[-1]
            page2 = await search_entries(
                session,
                webservice_id=WS,
                limit=3,
                before=(last.create_time, last.source, last.id),
            )
            assert key(page2) == [("tiqora", "t3"), ("znuny", "z2"), ("tiqora", "t1")]
            last = page2[-1]
            page3 = await search_entries(
                session,
                webservice_id=WS,
                limit=3,
                before=(last.create_time, last.source, last.id),
            )
            assert page3 == []
            every = key(page1) + key(page2)
            assert len(set(every)) == 6  # no duplicates, nothing lost

            # max level per entry, both sides
            levels = {r.communication_id.rstrip("0")[5:]: r.max_level for r in page1 + page2}
            assert levels == {
                "t1": "debug", "t3": "error", "t5": "notice",
                "z2": "info", "z3": "notice", "z4": "error",
            }  # fmt: skip
            by = {r.communication_id.rstrip("0")[5:]: r for r in page1 + page2}
            assert by["t3"].can_resend is True and by["t5"].can_resend is False  # Provider
            assert by["z2"].can_resend is False and by["z2"].name is None
            assert by["t3"].ticket_id == TICKET and by["z3"].ticket_id is None

            only_z = await search_entries(session, webservice_id=WS, limit=10, source="znuny")
            assert {r.source for r in only_z} == {"znuny"} and len(only_z) == 3
            only_req = await search_entries(
                session, webservice_id=WS, limit=10, communication_type="Requester"
            )
            assert len(only_req) == 4
            by_ip = await search_entries(
                session, webservice_id=WS, limit=10, remote_ip="198.51.100.7"
            )
            assert key(by_ip) == [("znuny", "z3"), ("tiqora", "t3")]
            window = await search_entries(
                session,
                webservice_id=WS,
                limit=10,
                created_after=BASE + timedelta(minutes=2),
                created_before=BASE + timedelta(minutes=4),
            )
            assert key(window) == [
                ("znuny", "z4"),
                ("znuny", "z3"),
                ("tiqora", "t3"),
                ("znuny", "z2"),
            ]
            assert await search_entries(session, webservice_id=OTHER_WS, limit=10) == []
    finally:
        await engine.dispose()
        _teardown(sync_url, marks)


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_get_entry_decodes_znuny_content(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    marks = _setup(sync_url)
    _seed_webservices(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        await _seed_mixed(factory, url_fixture)
        async with factory() as session:
            rows = await search_entries(session, webservice_id=WS, limit=10, source="znuny")
            z3 = next(r for r in rows if r.communication_id.startswith("gi51-z3"))
            got = await get_entry(session, source="znuny", entry_id=z3.id, webservice_id=WS)
            assert got is not None
            entry, lines = got
            assert entry.source == "znuny" and entry.remote_ip == "198.51.100.7"
            assert [(c.debug_level, c.subject, c.content) for c in lines] == [
                ("notice", "zs", "Dumper z3 ü")
            ]
            assert entry.can_resend is False
            assert (
                await get_entry(session, source="znuny", entry_id=z3.id, webservice_id=OTHER_WS)
                is None
            )
    finally:
        await engine.dispose()
        _teardown(sync_url, marks)


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_purge_and_gdpr_delete(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    marks = _setup(sync_url)
    _seed_webservices(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def counts(session: AsyncSession) -> tuple[int, int, int, int]:
        out = []
        for table, where in (
            ("tiqora_gi_debug_entry", "webservice_id = :ws"),
            (
                "tiqora_gi_debug_content",
                "entry_id IN (SELECT id FROM tiqora_gi_debug_entry WHERE webservice_id = :ws)",
            ),
            ("gi_debugger_entry", "webservice_id = :ws"),
            (
                "gi_debugger_entry_content",
                "gi_debugger_entry_id IN (SELECT id FROM"
                " gi_debugger_entry WHERE webservice_id = :ws)",
            ),
        ):
            out.append(
                (
                    await session.execute(
                        text(f"SELECT COUNT(*) FROM {table} WHERE {where}"), {"ws": WS}
                    )
                ).scalar_one()
            )
        return tuple(out)  # type: ignore[return-value]

    try:
        await _seed_mixed(factory, url_fixture)
        async with factory() as session:
            assert await counts(session) == (3, 6, 3, 3)
            # t1 (+1) and t3 (+3) are older than +4 min; Znuny rows are older too but untouched
            removed = await purge_debug_entries(session, older_than=BASE + timedelta(minutes=4))
            assert removed == 2
            assert await counts(session) == (1, 2, 3, 3)
        async with factory() as session:
            # t5 carries ticket 51030; a second entry for another ticket stays
            session.add(
                TiqoraGiDebugEntry(
                    communication_id="gi51-other".ljust(32, "0"),
                    communication_type="Requester",
                    webservice_id=WS,
                    create_time=BASE + timedelta(minutes=9),
                    ticket_id=TICKET_OTHER,
                )
            )
            await session.commit()
        async with factory() as session:
            assert await delete_debug_entries_for_tickets(session, [TICKET]) == 1
            await session.commit()
            assert await delete_debug_entries_for_tickets(session, []) == 0
        async with factory() as session:
            assert await counts(session) == (1, 0, 3, 3)
            left = (
                (
                    await session.execute(
                        text(
                            "SELECT ticket_id FROM tiqora_gi_debug_entry WHERE webservice_id = :ws"
                        ),
                        {"ws": WS},
                    )
                )
                .scalars()
                .all()
            )
            assert list(left) == [TICKET_OTHER]
    finally:
        await engine.dispose()
        _teardown(sync_url, marks)
