"""PluginContext.tickets / PluginContext.settings against a real DB (both dialects).

Seed band 49300-49349: ticket 49300 (queue 1), agents 49301 (ro on queue 1's group) and 49302
(no group at all). Settings rows ``plugins.org.example.hello.%`` are removed before and after.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.plugins.helpers import Installer
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions.core import register_core, set_registry_for_tests
from tiqora.extensions.registry import Registry
from tiqora.plugins.context import PluginContext, PluginHttp, PluginTickets, make_plugin_context
from tiqora.plugins.discovery import discover_plugins
from tiqora.plugins.loader import commit_registrar, import_and_register

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 10, 12, 0, 0)
TICKET = 49300
AGENT_RO = 49301
AGENT_NONE = 49302
ROOT = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)
SECTION = "/api/v1/admin/settings-sections/plugins.org.example.hello"


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _clean(conn: Any) -> None:
    conn.execute(text("DELETE FROM ticket WHERE id = :id"), {"id": TICKET})
    conn.execute(
        text("DELETE FROM group_user WHERE user_id IN (:a, :b)"), {"a": AGENT_RO, "b": AGENT_NONE}
    )
    conn.execute(text("DELETE FROM users WHERE id IN (:a, :b)"), {"a": AGENT_RO, "b": AGENT_NONE})
    conn.execute(
        delete(TiqoraSettings).where(TiqoraSettings.key.like("plugins.org.example.hello.%"))
    )


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn, checkfirst=True)
    with engine.begin() as conn:
        _clean(conn)
        group_id = int(conn.execute(text("SELECT group_id FROM queue WHERE id = 1")).scalar_one())
        for uid in (AGENT_RO, AGENT_NONE):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :login, 'x', 'Plg49', 'Agent', 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "login": f"plg49.agent.{uid}", "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :gid, 'ro', :t, 1, :t, 1)"
            ),
            {"uid": AGENT_RO, "gid": group_id, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id,"
                " timeout, until_time, escalation_time, escalation_update_time,"
                " escalation_response_time, escalation_solution_time, archive_flag,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'plg4949300', 'plg49 ticket', 1, 1, 1, 1, 1, 3, 1,"
                " 'plg49-cust', 'plg49.cust@example.org',"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {"id": TICKET, "t": NOW},
        )
    engine.dispose()


def _teardown(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        _clean(conn)
    engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def factory(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    fac = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("tiqora.plugins.context.get_session_factory", lambda: fac)
    yield fac
    await engine.dispose()
    _teardown(sync_url)


@pytest.fixture
def hello_ctx(fixture_plugins: Installer) -> Iterator[PluginContext]:
    fixture_plugins()
    (p,) = discover_plugins()
    ctx = make_plugin_context(p)
    reg = Registry()
    register_core(reg)
    commit_registrar(reg, import_and_register(p, ctx))
    reg.freeze()
    set_registry_for_tests(reg)
    yield ctx
    set_registry_for_tests(None)


@pytest.fixture
async def admin_client(
    factory: async_sessionmaker[AsyncSession], hello_ctx: PluginContext
) -> AsyncIterator[AsyncClient]:
    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_admin_user] = lambda: ROOT
    app.dependency_overrides[get_db] = _db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_ticket_summary(factory: Any, hello_ctx: PluginContext) -> None:
    t = await hello_ctx.tickets.get(TICKET)
    assert t is not None
    assert (t.id, t.tn, t.title, t.queue_id, t.owner_id) == (
        TICKET,
        "plg4949300",
        "plg49 ticket",
        1,
        1,
    )
    assert t.customer_user_id == "plg49.cust@example.org" and t.customer_id == "plg49-cust"
    assert t.state_type in {"new", "open"}
    assert await hello_ctx.tickets.get(49349) is None


async def test_ticket_get_with_explicit_session(factory: Any, hello_ctx: PluginContext) -> None:
    async with factory() as session:
        t = await hello_ctx.tickets.get(TICKET, session=session)
    assert t is not None and t.id == TICKET


async def test_as_agent_with_ro_on_queue_sees_ticket(
    factory: Any, hello_ctx: PluginContext
) -> None:
    t = await hello_ctx.tickets.get(TICKET, as_agent=AGENT_RO)
    assert t is not None and t.id == TICKET


async def test_as_agent_without_queue_permission_gets_none(
    factory: Any, hello_ctx: PluginContext
) -> None:
    assert await hello_ctx.tickets.get(TICKET, as_agent=AGENT_NONE) is None
    # the same ticket is visible in system context, so None is the permission, not absence
    assert await hello_ctx.tickets.get(TICKET) is not None


async def test_settings_defaults_then_saved_values(
    factory: Any, hello_ctx: PluginContext, admin_client: AsyncClient
) -> None:
    s = await hello_ctx.settings()
    assert s.greeting == "Hallo" and s.api_token is None  # type: ignore[attr-defined]
    listing = (await admin_client.get("/api/v1/admin/settings-sections")).json()
    assert {
        "key": "plugins.org.example.hello",
        "title_key": "plugin.org.example.hello:settings.title",
        "owner": "org.example.hello",
    } in listing
    r = await admin_client.put(
        SECTION, json={"values": {"greeting": "Moin", "api_token": "geheim-plg49"}}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["values"]["greeting"] == "Moin" and body["values"]["api_token"] == "********"
    assert "geheim-plg49" not in r.text
    again = await admin_client.get(SECTION)
    assert again.status_code == 200 and "geheim-plg49" not in again.text
    assert again.json()["values"]["api_token"] == "********"
    s = await hello_ctx.settings()
    assert s.greeting == "Moin"  # type: ignore[attr-defined]
    assert s.api_token.get_secret_value() == "geheim-plg49"  # type: ignore[attr-defined]
    async with factory() as session:
        s2 = await hello_ctx.settings(session)
    assert s2.greeting == "Moin"  # type: ignore[attr-defined]


async def test_secret_is_not_stored_in_clear(
    factory: Any, hello_ctx: PluginContext, admin_client: AsyncClient
) -> None:
    r = await admin_client.put(SECTION, json={"values": {"api_token": "geheim-plg49"}})
    assert r.status_code == 200
    async with factory() as session:
        rows = (
            await session.execute(
                select(TiqoraSettings.value).where(
                    TiqoraSettings.key.like("plugins.org.example.hello.%")
                )
            )
        ).all()
    assert rows and all("geheim-plg49" not in str(v) for (v,) in rows)


async def test_settings_missing_section_raises(factory: Any) -> None:
    set_registry_for_tests(None)
    ctx = PluginContext(
        "org.example.nosettings", "1.0.0", http=PluginHttp([]), tickets=PluginTickets()
    )
    with pytest.raises(LookupError, match="keine Einstellungen"):
        await ctx.settings()
