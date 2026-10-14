"""create_ticket writes Znuny's initial Service/SLA/Type history (Ticket.pm::TicketCreate).

Order after NewTicket: ServiceUpdate, SLAUpdate, TypeUpdate (only when Ticket::Service /
Ticket::Type are on). Both dialects. Private id 8961.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.ticket_write_service import TicketIn, create_ticket
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

SVC_ID = 8961
SLA_ID = 8961
TYPE_ID = 8961


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def sync_url(request: pytest.FixtureRequest) -> str:
    return str(request.getfixturevalue(request.param))


def _async_url(url: str) -> str:
    for old, new in (
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("mysql+pymysql://", "mysql+aiomysql://"),
    ):
        if url.startswith(old):
            return url.replace(old, new, 1)
    return url


def _sysconfig(values: dict[str, Any]) -> SysConfig:
    async def _fetch(name: str) -> Any:
        return None

    cfg = SysConfig(fetch=_fetch)

    async def _get(name: str, default: Any = None) -> Any:
        return values.get(name, default)

    cfg.get = _get  # type: ignore[method-assign]
    return cfg


def _seed(url: str) -> int:
    """Insert the fixtures; return the counter high-water mark to clean up to."""
    engine = create_engine(url)
    with engine.begin() as conn:
        mark = int(
            conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM ticket_number_counter")).scalar()
            or 0
        )
        TiqoraBase.metadata.create_all(conn)
        conn.execute(
            text(
                "INSERT INTO service (id, name, valid_id, create_time, create_by, change_time,"
                " change_by) VALUES (:i, 'R3Service', 1, current_timestamp, 1,"
                " current_timestamp, 1)"
            ),
            {"i": SVC_ID},
        )
        conn.execute(
            text(
                "INSERT INTO sla (id, name, first_response_time, update_time, solution_time,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:i, 'R3SLA', 0, 0, 0, 1, current_timestamp, 1, current_timestamp, 1)"
            ),
            {"i": SLA_ID},
        )
        conn.execute(
            text(
                "INSERT INTO ticket_type (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:i, 'R3Type', 1, current_timestamp, 1,"
                " current_timestamp, 1)"
            ),
            {"i": TYPE_ID},
        )
    engine.dispose()
    return mark


def _cleanup(url: str, ticket_ids: list[int], counter_mark: int) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        for tid in ticket_ids:
            for table in ("ticket_history", "tiqora_event_outbox", "tiqora_cache_invalidation"):
                conn.execute(text(f"DELETE FROM {table} WHERE ticket_id = :t"), {"t": tid})
            conn.execute(text("DELETE FROM ticket WHERE id = :t"), {"t": tid})
        conn.execute(text("DELETE FROM ticket_number_counter WHERE id > :m"), {"m": counter_mark})
        conn.execute(text("DELETE FROM sla WHERE id = :i"), {"i": SLA_ID})
        conn.execute(text("DELETE FROM service WHERE id = :i"), {"i": SVC_ID})
        conn.execute(text("DELETE FROM ticket_type WHERE id = :i"), {"i": TYPE_ID})
    engine.dispose()


async def _create(
    factory: async_sessionmaker[AsyncSession], cfg: SysConfig, **kw: Any
) -> tuple[int, list[tuple[str, str, int]]]:
    async with factory() as session, session.begin():
        tid = await create_ticket(
            session,
            factory,
            cfg,
            params=TicketIn(title="r3", queue_id=1, state_id=1, priority_id=3, owner_id=1, **kw),
            user_id=1,
        )
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT ht.name, h.name, h.create_by FROM ticket_history h"
                    " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
                    " WHERE h.ticket_id = :t ORDER BY h.id"
                ),
                {"t": tid},
            )
        ).all()
    return tid, [(str(a), str(b), int(c)) for a, b, c in rows]


async def test_type_and_service_history_on(sync_url: str) -> None:
    mark = _seed(sync_url)
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids: list[int] = []
    try:
        cfg = _sysconfig({"Ticket::Type": "1", "Ticket::Service": "1"})
        tid, rows = await _create(factory, cfg, type_id=TYPE_ID, service_id=SVC_ID, sla_id=SLA_ID)
        ids.append(tid)
        assert [r[0] for r in rows] == ["NewTicket", "ServiceUpdate", "SLAUpdate", "TypeUpdate"]
        assert rows[1][1] == f"%%R3Service%%{SVC_ID}%%NULL%%"
        assert rows[2][1] == f"%%R3SLA%%{SLA_ID}%%NULL%%"
        assert rows[3][1] == f"%%R3Type%%{TYPE_ID}"
        assert all(r[2] == 1 for r in rows)

        # no service/SLA given: Znuny writes the 'NULL' / '' placeholders
        tid2, rows2 = await _create(factory, cfg, type_id=TYPE_ID)
        ids.append(tid2)
        assert rows2[1][1] == "%%NULL%%%%NULL%%"
        assert rows2[2][1] == "%%NULL%%%%NULL%%"
    finally:
        await engine.dispose()
        _cleanup(sync_url, ids, mark)


async def test_only_type_on(sync_url: str) -> None:
    mark = _seed(sync_url)
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids: list[int] = []
    try:
        tid, rows = await _create(
            factory, _sysconfig({"Ticket::Type": "1"}), type_id=TYPE_ID, service_id=SVC_ID
        )
        ids.append(tid)
        assert [r[0] for r in rows] == ["NewTicket", "TypeUpdate"]
    finally:
        await engine.dispose()
        _cleanup(sync_url, ids, mark)


async def test_default_off_writes_no_extra_history(sync_url: str) -> None:
    mark = _seed(sync_url)
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids: list[int] = []
    try:
        tid, rows = await _create(
            factory, _sysconfig({}), type_id=TYPE_ID, service_id=SVC_ID, sla_id=SLA_ID
        )
        ids.append(tid)
        assert [r[0] for r in rows] == ["NewTicket"]
    finally:
        await engine.dispose()
        _cleanup(sync_url, ids, mark)


async def test_missing_type_uses_ticket_type_default(sync_url: str) -> None:
    mark = _seed(sync_url)
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids: list[int] = []
    try:
        cfg = _sysconfig({"Ticket::Type": "1", "Ticket::Type::Default": "R3Type"})
        tid, rows = await _create(factory, cfg)
        ids.append(tid)
        assert rows[-1][:2] == ("TypeUpdate", f"%%R3Type%%{TYPE_ID}")
        async with factory() as session:
            stored = (
                await session.execute(text("SELECT type_id FROM ticket WHERE id = :t"), {"t": tid})
            ).scalar()
        assert stored == TYPE_ID
    finally:
        await engine.dispose()
        _cleanup(sync_url, ids, mark)


async def test_invalid_default_type_falls_back_to_type_1(sync_url: str) -> None:
    """Znuny offers only valid types (TypeList Valid => 1): an invalid default type is skipped."""
    mark = _seed(sync_url)
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids: list[int] = []
    try:
        sync_engine = create_engine(sync_url)
        with sync_engine.begin() as conn:
            conn.execute(text("UPDATE ticket_type SET valid_id = 2 WHERE id = :i"), {"i": TYPE_ID})
        sync_engine.dispose()
        cfg = _sysconfig({"Ticket::Type": "1", "Ticket::Type::Default": "R3Type"})
        tid, rows = await _create(factory, cfg)
        ids.append(tid)
        async with factory() as session:
            stored = (
                await session.execute(text("SELECT type_id FROM ticket WHERE id = :t"), {"t": tid})
            ).scalar()
        assert stored == 1
        assert rows[-1][0] == "TypeUpdate" and rows[-1][1].endswith("%%1")
    finally:
        await engine.dispose()
        _cleanup(sync_url, ids, mark)
