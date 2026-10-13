"""SCIM provisioning log: own-transaction writes, listing, retention purge (both dialects).

Seed band 63100-63119 (``provider_id`` of the event rows; the table has no FK to providers).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from structlog.testing import capture_logs

from tests.test_identity_store import _to_async_url
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraScimEvent, TiqoraSettings
from tiqora.domain.settings_store import KEY_SCIM_EVENTS_PURGE_ENABLED
from tiqora.identity.scim import audit
from tiqora.identity.scim.audit import (
    list_events,
    record_event,
    record_event_in,
    run_scim_events_purge_tick,
)
from tiqora.identity.types import utc_now
from tiqora.worker.services import DAEMON_SERVICES

pytestmark = pytest.mark.db

PID = 63100
BAND = "provider_id BETWEEN 63100 AND 63119"
_URLS: set[str] = set()


def _clean(url: str) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(text(f"DELETE FROM tiqora_scim_event WHERE {BAND}"))
            conn.execute(
                delete(TiqoraSettings).where(TiqoraSettings.key == KEY_SCIM_EVENTS_PURGE_ENABLED)
            )
    finally:
        engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()


@pytest_asyncio.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def maker(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _clean(url)
    engine = create_async_engine(_to_async_url(url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(audit, "get_session_factory", lambda: factory)
    yield factory
    await engine.dispose()
    _clean(url)


async def _record(
    maker: async_sessionmaker[AsyncSession], status: int, op: Any = "create", **kw: Any
) -> None:
    await record_event(
        maker,
        provider_id=kw.pop("provider_id", PID),
        token_id=None,
        method="POST",
        resource="User",
        operation=op,
        status=status,
        **kw,
    )


async def _rows(maker: async_sessionmaker[AsyncSession]) -> list[TiqoraScimEvent]:
    async with maker() as s:
        return list(
            (
                await s.scalars(
                    select(TiqoraScimEvent)
                    .where(TiqoraScimEvent.provider_id.between(63100, 63119))
                    .order_by(TiqoraScimEvent.id)
                )
            ).all()
        )


async def test_record_event_own_transaction(maker: async_sessionmaker[AsyncSession]) -> None:
    async with maker() as caller:
        await record_event_in(
            caller,
            provider_id=PID,
            token_id=None,
            method="ADMIN",
            resource="Token",
            operation="token_create",
            status=200,
        )  # flushed, caller transaction still open
        await _record(maker, 409, "conflict", scim_id="a" * 36, user_kind="agent", user_id=7)
        await caller.rollback()  # the caller's own work is gone ...
    rows = await _rows(maker)  # ... the separately recorded event persists
    assert len(rows) == 1
    row = rows[0]
    assert (row.operation, row.status, row.method, row.resource) == (
        "conflict",
        409,
        "POST",
        "User",
    )
    assert (row.scim_id, row.user_kind, row.user_id) == ("a" * 36, "agent", 7)


async def test_wrong_call_raises_and_is_not_swallowed(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    with capture_logs() as logs:
        with pytest.raises(TypeError):
            await record_event(  # type: ignore[call-arg]
                maker,
                provider_id=PID,
                token_id=None,
                method="ADMIN",
                resource="Token",
                operation="token_create",
                status=200,
                actor_id=5,
            )
        async with maker() as caller:
            with pytest.raises(TypeError):
                await record_event_in(  # type: ignore[call-arg]
                    caller, provider_id=PID, token_id=None, method="POST", resource="User"
                )
    assert not [e for e in logs if e["event"] == "scim_event_not_recorded"]
    assert await _rows(maker) == []


async def test_overlong_scim_id_is_clamped(maker: async_sessionmaker[AsyncSession]) -> None:
    await _record(maker, 404, "rejected", scim_id="b" * 80)
    [row] = await _rows(maker)
    assert row.scim_id == "b" * 36


async def test_record_event_in_joins_callers_transaction(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as caller:
        await record_event_in(
            caller,
            provider_id=PID,
            token_id=3,
            method="ADMIN",
            resource="Token",
            operation="token_revoke",
            status=200,
            actor_user_id=63101,
        )
        await caller.commit()
    rows = await _rows(maker)
    assert [(r.operation, r.token_id, r.actor_user_id) for r in rows] == [
        ("token_revoke", 3, 63101)
    ]


async def test_record_event_never_raises() -> None:
    class _BrokenSession:
        def add(self, _row: object) -> None:
            return None

        async def commit(self) -> None:
            raise RuntimeError("db down")

        async def __aenter__(self) -> _BrokenSession:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

    with capture_logs() as logs:
        await record_event(
            lambda: _BrokenSession(),  # type: ignore[arg-type]
            provider_id=PID,
            token_id=None,
            method="POST",
            resource="User",
            operation="create",
            status=201,
        )
    warnings = [e for e in logs if e["event"] == "scim_event_not_recorded"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["operation"] == "create"


async def test_detail_truncated(maker: async_sessionmaker[AsyncSession]) -> None:
    await _record(maker, 400, "rejected", detail="x" * 600)
    [row] = await _rows(maker)
    assert row.detail == "x" * 500


async def test_list_events_newest_first_and_failures_only(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    await _record(maker, 201, "create")
    await _record(maker, 409, "conflict")
    await _record(maker, 401, "auth_failed")
    await _record(maker, 200, "patch", provider_id=PID + 1)  # other provider never listed
    async with maker() as s:
        everything = await list_events(
            s, provider_id=PID, before_id=None, limit=50, failures_only=False
        )
        failures = await list_events(
            s, provider_id=PID, before_id=None, limit=50, failures_only=True
        )
        assert [e.status for e in everything] == [401, 409, 201]
        assert [e.status for e in failures] == [401, 409]
        page = await list_events(
            s, provider_id=PID, before_id=everything[0].id, limit=1, failures_only=False
        )
        assert [e.status for e in page] == [409]
        capped = await list_events(s, provider_id=PID, before_id=None, limit=0, failures_only=False)
        assert len(capped) == 1  # a non-positive limit never means "unbounded"


async def test_purge_respects_retention(maker: async_sessionmaker[AsyncSession]) -> None:
    now = utc_now()
    async with maker() as s:
        for age, status in ((8, 200), (3, 201)):
            s.add(
                TiqoraScimEvent(
                    provider_id=PID,
                    created=now - timedelta(days=age),
                    method="POST",
                    resource="User",
                    operation="create",
                    status=status,
                )
            )
        await s.commit()
    settings = Settings(environment="test", scim_event_retention_days=5)
    result = await run_scim_events_purge_tick(settings)
    assert result == {"deleted": 1}  # 5 is not the default: the setting is really read
    assert [r.status for r in await _rows(maker)] == [201]


async def test_purge_tick_honours_switch(maker: async_sessionmaker[AsyncSession]) -> None:
    async with maker() as s:
        s.add(
            TiqoraScimEvent(
                provider_id=PID,
                created=utc_now() - timedelta(days=400),
                method="POST",
                resource="User",
                operation="create",
                status=201,
            )
        )
        s.add(TiqoraSettings(key=KEY_SCIM_EVENTS_PURGE_ENABLED, value="false"))
        await s.commit()
    assert await run_scim_events_purge_tick() == {"skipped": 1}
    assert len(await _rows(maker)) == 1


def test_daemon_catalog_has_purge() -> None:
    svc = {s.slug: s for s in DAEMON_SERVICES}["scim_events_purge"]
    assert svc.schedule_kind == "daily" and svc.daily_at == "04:40"
    assert svc.enabled_key == KEY_SCIM_EVENTS_PURGE_ENABLED
    assert svc.default_enabled and svc.toggleable
