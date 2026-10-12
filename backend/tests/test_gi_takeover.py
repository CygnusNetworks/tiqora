"""Takeover switch and Daemons notices (G12, both dialects).

Seed band 51310-51369 (shared with ``test_gi_follower``): webservice 51312, SysConfig rows
51315-51319. The module removes everything it seeded.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from tests._gi_fixtures import cleanup_webservices, insert_webservice, requester_config
from tests._sysconfig_rows import set_sysconfig_rows
from tests.test_gi_jobs import _async_url
from tiqora.api.v1.admin import daemons as admin_daemons
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.settings_store import KEY_GI_REQUESTER_ENABLED
from tiqora.gi.takeover import (
    NOTICE_DOUBLE_SEND,
    NOTICE_NOT_TRIGGERED,
    ZNUNY_GI_EVENT_HANDLER,
    daemon_notices,
    webservices_with_event_invokers,
    znuny_gi_handler_active,
)
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

WS = 51312
SYSCONFIG_BASE = 51315
INVOKER = {"Type": "Ticket::Generic", "Events": [{"Event": "TicketCreate", "Asynchronous": "1"}]}


def _clean(conn: Any) -> None:
    cleanup_webservices(conn, [WS])
    conn.execute(
        text(f"DELETE FROM sysconfig_modified WHERE id BETWEEN {SYSCONFIG_BASE} AND 51319")
    )
    conn.execute(text(f"DELETE FROM sysconfig_default WHERE id BETWEEN {SYSCONFIG_BASE} AND 51319"))
    conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key == KEY_GI_REQUESTER_ENABLED))


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def url_fixture(request: pytest.FixtureRequest) -> Generator[str, None, None]:
    url: str = request.getfixturevalue(request.param)
    engine = create_engine(url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        _clean(conn)
    engine.dispose()
    yield url
    engine = create_engine(url)
    with engine.begin() as conn:
        _clean(conn)
    engine.dispose()


async def _with_session(sync_url: str, fn: Any) -> Any:
    engine = create_async_engine(_async_url(sync_url))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await fn(session)
    finally:
        await engine.dispose()


def _znuny_handler(sync_url: str, state: str) -> None:
    """``state``: ``valid`` (default row only), ``off`` (modified override is_valid=0),
    ``default_off`` (default row is_valid=0), ``absent`` (no row at all)."""
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM sysconfig_modified WHERE id = {SYSCONFIG_BASE}"))
        conn.execute(text(f"DELETE FROM sysconfig_default WHERE id = {SYSCONFIG_BASE}"))
        if state != "absent":
            set_sysconfig_rows(conn, SYSCONFIG_BASE, {ZNUNY_GI_EVENT_HANDLER: {"Module": "x"}})
            conn.execute(text(f"DELETE FROM sysconfig_modified WHERE id = {SYSCONFIG_BASE}"))
        if state == "off":
            conn.execute(
                text(
                    "INSERT INTO sysconfig_modified (id, sysconfig_default_id, name, user_id,"
                    " is_valid, user_modification_active, effective_value, is_dirty,"
                    " reset_to_default, create_time, create_by, change_time, change_by)"
                    " SELECT :id, id, name, NULL, 0, 0, effective_value, 0, 0,"
                    " current_timestamp, 1, current_timestamp, 1 FROM sysconfig_default"
                    " WHERE id = :id"
                ),
                {"id": SYSCONFIG_BASE},
            )
        if state == "default_off":
            conn.execute(
                text("UPDATE sysconfig_default SET is_valid = 0 WHERE id = :id"),
                {"id": SYSCONFIG_BASE},
            )
    engine.dispose()


def _flag(sync_url: str, value: str | None) -> None:
    engine = create_engine(sync_url)
    with Session(engine) as session:
        session.execute(
            delete(TiqoraSettings).where(TiqoraSettings.key == KEY_GI_REQUESTER_ENABLED)
        )
        if value is not None:
            session.add(TiqoraSettings(key=KEY_GI_REQUESTER_ENABLED, value=value))
        session.commit()
    engine.dispose()


def _webservice(sync_url: str, invokers: dict[str, Any] | None) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        cleanup_webservices(conn, [WS])
        if invokers is not None:
            insert_webservice(
                conn, ws_id=WS, name="GI51Takeover", config=requester_config(invokers=invokers)
            )
    engine.dispose()


async def test_znuny_handler_detection(url_fixture: str) -> None:
    async def active(session: AsyncSession) -> bool:
        return await znuny_gi_handler_active(session)

    _znuny_handler(url_fixture, "valid")
    assert await _with_session(url_fixture, active) is True
    # an admin switched it off: the latest system-wide modified row wins
    _znuny_handler(url_fixture, "off")
    assert await _with_session(url_fixture, active) is False
    _znuny_handler(url_fixture, "default_off")
    assert await _with_session(url_fixture, active) is False
    # no row at all: no Znuny that could send
    _znuny_handler(url_fixture, "absent")
    assert await _with_session(url_fixture, active) is False


async def test_webservices_with_event_invokers(url_fixture: str) -> None:
    async def listed(session: AsyncSession) -> list[tuple[int, str]]:
        return await webservices_with_event_invokers(session, SysConfig(session))

    _webservice(url_fixture, None)
    assert [x for x in await _with_session(url_fixture, listed) if x[0] == WS] == []
    # an invoker without events, and one of an unsupported type, do not count
    _webservice(
        url_fixture,
        {
            "Silent": {"Type": "Ticket::Generic"},
            "Other": {"Type": "Test::Test", "Events": [{"Event": "TicketCreate"}]},
        },
    )
    assert [x for x in await _with_session(url_fixture, listed) if x[0] == WS] == []
    _webservice(url_fixture, {"Send": INVOKER})
    assert (WS, "GI51Takeover") in await _with_session(url_fixture, listed)


async def test_notices_both_directions(url_fixture: str) -> None:
    async def notices(session: AsyncSession) -> list[str]:
        return await daemon_notices(session, SysConfig(session))

    _webservice(url_fixture, {"Send": INVOKER})
    _znuny_handler(url_fixture, "valid")
    # flag off (never set): webservices exist, but Tiqora fires nothing
    assert await _with_session(url_fixture, notices) == [NOTICE_NOT_TRIGGERED]
    _flag(url_fixture, "0")
    assert await _with_session(url_fixture, notices) == [NOTICE_NOT_TRIGGERED]
    # flag on while Znuny still sends: everything goes out twice
    _flag(url_fixture, "1")
    assert await _with_session(url_fixture, notices) == [NOTICE_DOUBLE_SEND]
    # Znuny's handler switched off: consistent
    _znuny_handler(url_fixture, "off")
    assert await _with_session(url_fixture, notices) == []


async def test_daemons_api_lists_gi_services_with_notices(url_fixture: str) -> None:
    admin = AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )
    _webservice(url_fixture, {"Send": INVOKER})
    _znuny_handler(url_fixture, "valid")

    async def listing(session: AsyncSession) -> Any:
        return await admin_daemons.list_daemons(admin, session)

    out = await _with_session(url_fixture, listing)
    by_slug = {s.slug: s for s in out.services}
    requester = by_slug["gi_requester"]
    assert requester.enabled is False and requester.toggleable is True
    assert requester.interval_seconds == 5
    assert requester.notices == [NOTICE_NOT_TRIGGERED]
    cleanup = by_slug["gi_debug_cleanup"]
    assert cleanup.toggleable is False and cleanup.daily_at == "03:02"
    assert all(s.notices == [] for s in out.services if s.slug != "gi_requester")

    _flag(url_fixture, "1")
    out = await _with_session(url_fixture, listing)
    assert {s.slug: s for s in out.services}["gi_requester"].notices == [NOTICE_DOUBLE_SEND]
