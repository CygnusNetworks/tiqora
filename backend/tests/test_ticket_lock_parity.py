"""Znuny lock/owner parity (composer RequiredLock semantics).

Covers the module-level primitives added for the composer auto-lock feature:

- ``acquire_lock``: acquired / already_mine / locked_by_other / takeover /
  not_required, including the Lock-before-OwnerUpdate history order that
  matches Znuny's AgentTicketActionCommon (TicketLockSet, then TicketOwnerSet).
- ``lock_ticket`` / ``unlock_ticket`` same-state no-op guard (TicketLockSet
  "check if update is needed").
- Class-level ``change_state`` frontend close parity: closing locks+owns an
  unlocked ticket first (RequiredLock on the close screen) and unlocks after.
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.domain.ticket_write_service import (
    TicketIn,
    TicketWriteService,
    acquire_lock,
    create_ticket,
    lock_ticket,
    unlock_ticket,
)
from tiqora.znuny.sysconfig import SysConfig

_UID_ALICE = 91001
_UID_BOB = 91002


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


# Auto-increment tables this module writes to; rows above the setup-time MAX(id)
# are ours and get deleted on teardown (strict DB-leak CI: modules clean up
# what they commit).
_CLEANUP_TABLES = (
    "ticket_history",
    "tiqora_cache_invalidation",
    "tiqora_event_outbox",
    "ticket",
    "ticket_number_counter",
)


@pytest.fixture(autouse=True, scope="module")
def _module_cleanup(mariadb_znuny_url: str) -> Generator[None, None, None]:
    engine = create_engine(mariadb_znuny_url)
    marks: dict[str, int] = {}
    with engine.connect() as conn:
        for t in _CLEANUP_TABLES:
            # tiqora_* aux tables may not exist yet (created by _seed below).
            try:
                marks[t] = int(
                    conn.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {t}")).scalar() or 0
                )
            except Exception:  # noqa: BLE001 - missing table ⇒ everything is ours
                conn.rollback()
                marks[t] = 0
    yield
    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for t in _CLEANUP_TABLES:
            with contextlib.suppress(Exception):
                conn.execute(text(f"DELETE FROM {t} WHERE id > :m"), {"m": marks[t]})
        conn.execute(
            text("DELETE FROM group_user WHERE user_id IN (:a, :b)"),
            {"a": _UID_ALICE, "b": _UID_BOB},
        )
        conn.execute(
            text("DELETE FROM users WHERE id IN (:a, :b)"),
            {"a": _UID_ALICE, "b": _UID_BOB},
        )
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    engine.dispose()


def _make_sysconfig(overrides: dict[str, Any] | None = None) -> SysConfig:
    async def _fetch(name: str) -> Any:
        if overrides and name in overrides:
            return overrides[name]
        return None

    return SysConfig(fetch=_fetch)


async def _seed(session: AsyncSession) -> None:
    """Aux tables + two agents with rw on group 1 (queue 1's group)."""
    ddl = [
        """CREATE TABLE IF NOT EXISTS tiqora_cache_invalidation (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            ticket_id BIGINT NULL,
            cache_type VARCHAR(100) NULL,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS tiqora_event_outbox (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            event_type VARCHAR(100) NOT NULL,
            ticket_id BIGINT NOT NULL,
            payload TEXT,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed TINYINT(1) NOT NULL DEFAULT 0
        )""",
    ]
    for stmt in ddl:
        with contextlib.suppress(Exception):
            await session.execute(text(stmt))
    for uid, login in ((_UID_ALICE, "lockparity-alice"), (_UID_BOB, "lockparity-bob")):
        with contextlib.suppress(Exception):
            await session.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :login, 'x', 'Lock', :login, 1,"
                    " NOW(), 1, NOW(), 1)"
                ),
                {"id": uid, "login": login},
            )
        with contextlib.suppress(Exception):
            await session.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, 1, 'rw', NOW(), 1, NOW(), 1)"
                ),
                {"uid": uid},
            )
    await session.commit()


async def _make_ticket(
    factory: async_sessionmaker[AsyncSession], sysconfig: SysConfig, title: str
) -> int:
    async with factory() as session, session.begin():
        return await create_ticket(
            session,
            factory,
            sysconfig,
            params=TicketIn(title=title, queue_id=1, state_id=1, priority_id=3, owner_id=1),
            user_id=1,
        )


async def _ticket_row(factory: async_sessionmaker[AsyncSession], ticket_id: int) -> tuple[int, int]:
    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT ticket_lock_id, user_id FROM ticket WHERE id = :tid"),
                {"tid": ticket_id},
            )
        ).first()
        assert row is not None
        return int(row[0]), int(row[1])


async def _history_types(factory: async_sessionmaker[AsyncSession], ticket_id: int) -> list[str]:
    async with factory() as session:
        rows = await session.execute(
            text(
                "SELECT ht.name FROM ticket_history h"
                " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
                " WHERE h.ticket_id = :tid ORDER BY h.id"
            ),
            {"tid": ticket_id},
        )
        return [r[0] for r in rows]


@pytest.mark.db
async def test_acquire_lock_flow_mariadb(mariadb_znuny_url: str) -> None:
    """acquire → locked+owned (Lock before OwnerUpdate); re-acquire → already_mine;
    other agent → locked_by_other (no write); takeover → owner moves, lock stays."""
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _seed(session)
    ticket_id = await _make_ticket(factory, sysconfig, "Acquire Flow")

    # Alice opens the reply composer on the unlocked ticket.
    async with factory() as session, session.begin():
        res = await acquire_lock(
            session,
            ticket_id=ticket_id,
            user_id=_UID_ALICE,
            sysconfig=sysconfig,
            action="compose",
        )
    assert res.result == "acquired"
    lock_id, owner_id = await _ticket_row(factory, ticket_id)
    assert (lock_id, owner_id) == (2, _UID_ALICE)
    history = await _history_types(factory, ticket_id)
    # Znuny AgentTicketActionCommon: TicketLockSet first, then TicketOwnerSet.
    assert history.index("Lock") < history.index("OwnerUpdate")

    # Reopening her own composer is a no-op.
    before = await _history_types(factory, ticket_id)
    async with factory() as session, session.begin():
        res = await acquire_lock(
            session,
            ticket_id=ticket_id,
            user_id=_UID_ALICE,
            sysconfig=sysconfig,
            action="compose",
        )
    assert res.result == "already_mine"
    assert await _history_types(factory, ticket_id) == before

    # Bob hits the foreign lock — reported, nothing written.
    async with factory() as session, session.begin():
        res = await acquire_lock(
            session,
            ticket_id=ticket_id,
            user_id=_UID_BOB,
            sysconfig=sysconfig,
            action="compose",
        )
    assert res.result == "locked_by_other"
    assert res.locked_by_id == _UID_ALICE
    assert res.locked_by_name == "Lock lockparity-alice"
    assert await _history_types(factory, ticket_id) == before
    assert (await _ticket_row(factory, ticket_id))[1] == _UID_ALICE

    # Bob takes over: owner moves to him, the ticket stays locked.
    async with factory() as session, session.begin():
        res = await acquire_lock(
            session,
            ticket_id=ticket_id,
            user_id=_UID_BOB,
            sysconfig=sysconfig,
            action="compose",
            takeover=True,
        )
    assert res.result == "taken_over"
    assert await _ticket_row(factory, ticket_id) == (2, _UID_BOB)
    history = await _history_types(factory, ticket_id)
    assert history.count("OwnerUpdate") == 2
    assert history.count("Lock") == 1  # no second lock row — it stayed locked

    await engine.dispose()


@pytest.mark.db
async def test_acquire_lock_respects_sysconfig_off_mariadb(mariadb_znuny_url: str) -> None:
    """RequiredLock=0 in sysconfig ⇒ not_required, ticket untouched."""
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig({"Ticket::Frontend::AgentTicketCompose###RequiredLock": "0"})

    async with factory() as session:
        await _seed(session)
    ticket_id = await _make_ticket(factory, sysconfig, "RequiredLock Off")

    async with factory() as session, session.begin():
        res = await acquire_lock(
            session,
            ticket_id=ticket_id,
            user_id=_UID_ALICE,
            sysconfig=sysconfig,
            action="compose",
        )
    assert res.result == "not_required"
    assert await _ticket_row(factory, ticket_id) == (1, 1)

    await engine.dispose()


@pytest.mark.db
async def test_lock_set_same_state_noop_mariadb(mariadb_znuny_url: str) -> None:
    """TicketLockSet parity: same-state lock/unlock writes nothing."""
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _seed(session)
    ticket_id = await _make_ticket(factory, sysconfig, "Lock Noop")

    # Unlocking an unlocked ticket: no history row.
    before = await _history_types(factory, ticket_id)
    async with factory() as session, session.begin():
        await unlock_ticket(session, ticket_id=ticket_id, user_id=_UID_ALICE, sysconfig=sysconfig)
    assert await _history_types(factory, ticket_id) == before

    # Locking twice: exactly one Lock row.
    for _ in range(2):
        async with factory() as session, session.begin():
            await lock_ticket(session, ticket_id=ticket_id, user_id=_UID_ALICE, sysconfig=sysconfig)
    assert (await _history_types(factory, ticket_id)).count("Lock") == 1

    await engine.dispose()


@pytest.mark.db
async def test_close_unlocks_and_owns_mariadb(mariadb_znuny_url: str) -> None:
    """Class change_state close parity: closing an unlocked foreign ticket makes
    the closer owner (RequiredLock on the close screen) and unlocks afterwards."""
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _seed(session)
    ticket_id = await _make_ticket(factory, sysconfig, "Close Parity")

    async with factory() as session:
        svc = TicketWriteService(session, factory, sysconfig)
        async with session.begin():
            # State 2 = "closed successful" (type closed) in the Znuny seed.
            await svc.change_state(_UID_ALICE, ticket_id, 2)

    lock_id, owner_id = await _ticket_row(factory, ticket_id)
    assert (lock_id, owner_id) == (1, _UID_ALICE)
    history = await _history_types(factory, ticket_id)
    # Lock+own on "screen open", state change, unlock after: Znuny row order.
    assert history.index("Lock") < history.index("OwnerUpdate")
    assert history.index("OwnerUpdate") < history.index("StateUpdate")
    assert history.index("StateUpdate") < history.index("Unlock")

    await engine.dispose()
