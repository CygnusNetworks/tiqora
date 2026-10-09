"""Per-customer AI pause: helper lookup (user wins over company, expiry) and the
customers API. Own seed block: tickets 9890-9891, customer keys ``cpause*``."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.ai.customer_pause import (
    active_pause_for_customer,
    active_pause_for_ticket,
    clear_pause,
    set_pause,
)
from tiqora.ai.models import CUSTOMER_PAUSE_SCOPE_COMPANY, CUSTOMER_PAUSE_SCOPE_USER
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)
ROOT = 1
COMPANY = "CPAUSE-CO"
LOGIN = "cpause.anna"
OTHER_LOGIN = "cpause.bob"
TICKET = 9890
OTHER_TICKET = 9891


def _async_url(sync_url: str) -> str:
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _cleanup(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(text("DELETE FROM tiqora_ai_customer_pause WHERE customer_key LIKE 'cpause%'"))
        conn.execute(
            text("DELETE FROM tiqora_ai_customer_pause WHERE customer_key = :c"), {"c": COMPANY}
        )
        conn.execute(
            text("DELETE FROM ticket WHERE id IN (:a, :b)"), {"a": TICKET, "b": OTHER_TICKET}
        )
        conn.execute(text("DELETE FROM customer_user WHERE login LIKE 'cpause%'"))
        conn.execute(text("DELETE FROM customer_company WHERE customer_id = :c"), {"c": COMPANY})
    engine.dispose()


@pytest.fixture
def seeded(mariadb_znuny_url: str) -> Iterator[str]:
    _cleanup(mariadb_znuny_url)
    engine = create_engine(mariadb_znuny_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO customer_company (customer_id, name, valid_id, create_time,"
                " create_by, change_time, change_by) VALUES (:c, 'Cpause Co', 1, :t, 1, :t, 1)"
            ),
            {"c": COMPANY, "t": NOW},
        )
        for login in (LOGIN, OTHER_LOGIN):
            conn.execute(
                text(
                    "INSERT INTO customer_user (login, email, customer_id, first_name,"
                    " last_name, valid_id, create_time, create_by, change_time, change_by)"
                    " VALUES (:l, :e, :c, 'C', 'Pause', 1, :t, 1, :t, 1)"
                ),
                {"l": login, "e": f"{login}@cpause.example", "c": COMPANY, "t": NOW},
            )
        for tid, login in ((TICKET, LOGIN), (OTHER_TICKET, OTHER_LOGIN)):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'Cpause', 1, 1, 1, 1, 1, 3, 4, :cid, :login,"
                    " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"20240601{tid}", "cid": COMPANY, "login": login, "t": NOW},
            )
    engine.dispose()
    yield mariadb_znuny_url
    _cleanup(mariadb_znuny_url)


def _factory(url: str) -> tuple[Any, Any]:
    engine = create_async_engine(_async_url(url))
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def test_user_pause_hits_only_that_customers_tickets(seeded: str) -> None:
    engine, factory = _factory(seeded)
    async with factory() as session:
        assert await active_pause_for_ticket(session, TICKET) is None
        await set_pause(
            session,
            scope=CUSTOMER_PAUSE_SCOPE_USER,
            customer_key=LOGIN,
            days=None,
            reason="Streit",
            user_id=ROOT,
        )
        await session.commit()
        assert await active_pause_for_ticket(session, TICKET) is not None
        assert await active_pause_for_ticket(session, OTHER_TICKET) is None
        assert await clear_pause(session, scope=CUSTOMER_PAUSE_SCOPE_USER, customer_key=LOGIN)
        await session.commit()
        assert await active_pause_for_ticket(session, TICKET) is None
    await engine.dispose()


async def test_company_pause_covers_every_contact_and_user_pause_wins(seeded: str) -> None:
    engine, factory = _factory(seeded)
    async with factory() as session:
        await set_pause(
            session,
            scope=CUSTOMER_PAUSE_SCOPE_COMPANY,
            customer_key=COMPANY,
            days=7,
            reason=None,
            user_id=ROOT,
        )
        await set_pause(
            session,
            scope=CUSTOMER_PAUSE_SCOPE_USER,
            customer_key=LOGIN,
            days=None,
            reason=None,
            user_id=ROOT,
        )
        await session.commit()
        assert await active_pause_for_ticket(session, OTHER_TICKET) is not None
        row = await active_pause_for_customer(session, customer_user_id=LOGIN, customer_id=COMPANY)
        assert row is not None and row.scope == CUSTOMER_PAUSE_SCOPE_USER
    await engine.dispose()


async def test_expired_pause_is_ignored(seeded: str) -> None:
    engine, factory = _factory(seeded)
    async with factory() as session:
        row = await set_pause(
            session,
            scope=CUSTOMER_PAUSE_SCOPE_USER,
            customer_key=LOGIN,
            days=1,
            reason=None,
            user_id=ROOT,
        )
        row.paused_until = datetime.now() - timedelta(hours=3)
        await session.commit()
        assert await active_pause_for_ticket(session, TICKET) is None
    await engine.dispose()


async def _client(url: str) -> tuple[Any, Any]:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    engine, factory = _factory(url)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    user = AuthenticatedUser(
        id=ROOT, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), engine


async def test_api_set_read_and_lift_for_user_and_company(seeded: str) -> None:
    client, engine = await _client(seeded)
    async with client:
        base = f"/api/v1/customers/{LOGIN}/ai-pause"
        assert (await client.get(base)).json()["active"] is False
        put = await client.put(base, json={"days": 7, "reason": "Beschwerde"})
        assert put.status_code == 200, put.text
        body = put.json()
        assert body["active"] is True and body["paused_until"] and body["reason"] == "Beschwerde"
        assert (await client.get(base)).json()["active"] is True
        assert (await client.delete(base)).status_code == 204
        assert (await client.get(base)).json()["active"] is False

        co = f"/api/v1/customers/companies/{COMPANY}/ai-pause"
        put = await client.put(co, json={})
        assert put.status_code == 200 and put.json()["paused_until"] is None
        assert (await client.delete(co)).status_code == 204

        assert (
            await client.put("/api/v1/customers/cpause.nobody/ai-pause", json={})
        ).status_code == 404
        assert (await client.put(base, json={"days": 0})).status_code == 422
    await engine.dispose()


async def test_auto_worker_skips_paused_customer(seeded: str) -> None:
    from types import SimpleNamespace

    from tiqora.ai.auto_worker import _cap_reason

    state = SimpleNamespace(ai_escalated_at=None, ai_paused_at=None)
    engine, factory = _factory(seeded)
    async with factory() as session:
        await set_pause(
            session,
            scope=CUSTOMER_PAUSE_SCOPE_USER,
            customer_key=LOGIN,
            days=3,
            reason=None,
            user_id=ROOT,
        )
        await session.commit()
        reason = await _cap_reason(
            session,
            ticket_id=TICKET,
            queue_id=1,
            policy=None,
            state=state,  # type: ignore[arg-type]
        )
        assert reason == "customer_ai_paused"
    await engine.dispose()
