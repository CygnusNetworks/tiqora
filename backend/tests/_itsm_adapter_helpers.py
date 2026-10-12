"""Shared pieces of the Mode A adapter tests: a rolled-back session on an ITSM database.

Nothing a test seeds is committed, so the strict leak check has nothing to find; the session sees
its own flushed rows.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests._itsm_db import to_async_url
from tiqora.itsm.znuny_adapter.profiles import PROFILES
from tiqora.itsm.znuny_adapter.reader import ZnunyItsmReader

_NOW = datetime(2026, 1, 1, 8, 0, 0)  # noqa: DTZ001 - Znuny stores naive UTC


@asynccontextmanager
async def itsm_session(
    request: pytest.FixtureRequest, dialect: str, profile: str
) -> AsyncIterator[tuple[AsyncSession, ZnunyItsmReader, AsyncEngine]]:
    url = request.getfixturevalue("itsm_db_url")(dialect, profile)
    engine = create_async_engine(to_async_url(url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            try:
                yield session, ZnunyItsmReader(PROFILES[profile]), engine
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


@contextmanager
def count_statements(engine: AsyncEngine) -> Iterator[list[str]]:
    seen: list[str] = []

    def before(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", before)
    try:
        yield seen
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", before)


async def seed_user(session: AsyncSession, user_id: int, login: str, first: str, last: str) -> None:
    await session.execute(
        text(
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
            " create_by, change_time, change_by) VALUES (:i, :l, 'x', :f, :n, 1, :t, 1, :t, 1)"
        ),
        {"i": user_id, "l": login, "f": first, "n": last, "t": _NOW},
    )


async def seed_customer(
    session: AsyncSession, login: str, first: str, last: str, company: str
) -> None:
    await session.execute(
        text(
            "INSERT INTO customer_user (login, email, customer_id, first_name, last_name, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:l, :l, :c, :f, :n, 1, :t, 1, :t, 1)"
        ),
        {"l": login, "f": first, "n": last, "c": company, "t": _NOW},
    )


async def seed_company(session: AsyncSession, customer_id: str, name: str) -> None:
    await session.execute(
        text(
            "INSERT INTO customer_company (customer_id, name, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:i, :n, 1, :t, 1, :t, 1)"
        ),
        {"i": customer_id, "n": name, "t": _NOW},
    )


async def seed_service(session: AsyncSession, service_id: int, name: str) -> None:
    await session.execute(
        text(
            "INSERT INTO service (id, name, valid_id, create_time, create_by, change_time,"
            " change_by) VALUES (:i, :n, 1, :t, 1, :t, 1)"
        ),
        {"i": service_id, "n": name, "t": _NOW},
    )
