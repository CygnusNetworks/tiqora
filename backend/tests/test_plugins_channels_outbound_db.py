"""Agent replies over plugin channels: send-then-store, limits, veto order, composer list (D11).

Seed band 55340-55369: queue ``pp55o-q`` 55341 in group 55342, agent ``pp55o-agent`` 55343
(``ro``/``rw``/``create``/``note``), customer ``pp55o-cust`` (mail ``pp55-cust@example.org``).
Tickets come from an inbound ``ingest`` of the fixture's ``Chat`` channel; everything the tests
create is removed with ``snapshot_marks``/``delete_above_marks``.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugin_points import boot_points_plugin, points_behaviour
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.ai.gate import is_tiqora_only_channel
from tiqora.channels.email.smtp import CapturingMailSender
from tiqora.domain.ticket_write_service import ArticleIn, InvalidInput, TicketWriteService
from tiqora.plugins import channels as plugin_channels
from tiqora.plugins.channels import (
    ChannelDeliveryError,
    InboundChannelMessage,
    OutboundChannelMessage,
    PluginChannels,
)
from tiqora.plugins.context import PluginTickets
from tiqora.plugins.sync_hooks import OperationVetoed, SyncBreaker
from tiqora.plugins.writes import PluginWriteError, TicketDraft
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 13, 12, 0, 0)
PLUGIN_ID = "org.example.points"
QUEUE, GROUP, AGENT = 55341, 55342, 55343
CHANNEL = "plugin_points_chat"
CHANNEL_NAME = "Plugin_points_Chat"
VETO = "Vertragsnummer fehlt"
MARK_TABLES: tuple[str, ...] = (
    "tiqora_event_outbox",
    "tiqora_mail_log",
    "tiqora_cache_invalidation",
    "ticket_history",
    "dynamic_field_value",
    "article_data_mime_attachment",
    "article_data_mime_plain",
    "article_data_mime",
    "article",
    "ticket",
    "ticket_number_counter",
)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _clean_seed(conn: Any) -> None:
    conn.execute(text("DELETE FROM customer_user WHERE login = 'pp55o-cust'"))
    conn.execute(text(f"DELETE FROM queue WHERE id = {QUEUE}"))
    conn.execute(text(f"DELETE FROM group_user WHERE group_id = {GROUP}"))
    conn.execute(text(f"DELETE FROM permission_groups WHERE id = {GROUP}"))
    conn.execute(text(f"DELETE FROM users WHERE id = {AGENT}"))


def _seed(sync_url: str) -> None:
    from tiqora.db.tiqora.base import TiqoraBase

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn, checkfirst=True)
    with engine.begin() as conn:
        _clean_seed(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:i, 'pp55o-agent', 'x', 'Pp55', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"i": AGENT, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:g, 'pp55o-grp', 1, :t, 1, :t, 1)"
            ),
            {"g": GROUP, "t": NOW},
        )
        for k in ("ro", "rw", "create", "note"):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:u, :g, :k, :t, 1, :t, 1)"
                ),
                {"u": AGENT, "g": GROUP, "k": k, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:q, 'pp55o-q', :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"q": QUEUE, "g": GROUP, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES ('pp55o-cust', 'pp55-cust@example.org', 'pp55o-co', 'Kim', 'Kunde', 1,"
                " current_timestamp, 1, current_timestamp, 1)"
            )
        )
    engine.dispose()


class Env:
    def __init__(self, sync_url: str, fac: async_sessionmaker[AsyncSession]) -> None:
        self.sync_url = sync_url
        self.factory = fac
        self.sender = CapturingMailSender()

    def rows(self, sql: str, **params: Any) -> list[Any]:
        engine = create_engine(self.sync_url)
        try:
            with engine.begin() as conn:
                return list(conn.execute(text(sql), params).all())
        finally:
            engine.dispose()

    def scalar(self, sql: str, **params: Any) -> Any:
        return self.rows(sql, **params)[0][0]

    def articles(self, ticket_id: int) -> int:
        return int(self.scalar("SELECT COUNT(*) FROM article WHERE ticket_id = :t", t=ticket_id))

    async def run(self, fn: Callable[[TicketWriteService], Awaitable[Any]]) -> Any:
        """One request transaction: commit on success, roll back when ``fn`` raises."""
        async with self.factory() as s, s.begin():
            return await fn(TicketWriteService(s, self.factory, SysConfig(s), self.sender))

    async def ticket(self) -> int:
        res = await PluginChannels(PLUGIN_ID).ingest(
            "Chat",
            InboundChannelMessage(sender="pp55-cust@example.org", body="Hallo", queue="pp55o-q"),
        )
        return res.ticket_id

    async def reply(self, ticket_id: int, **kw: Any) -> int:
        article = ArticleIn(
            **{
                "sender_type": "agent",
                "channel": CHANNEL,
                "subject": "",
                "body": "Antwort",
                "to_address": "pp55-cust@example.org",
                "is_visible_for_customer": True,
                **kw,
            }
        )
        return int(await self.run(lambda svc: svc.add_article(AGENT, ticket_id, article)))


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    marks = snapshot_marks(sync_url, MARK_TABLES)
    engine = create_async_engine(_to_async_url(sync_url))
    fac = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    for mod in (
        "tiqora.plugins.writes",
        "tiqora.plugins.channels",
        "tiqora.plugins.customers",
        "tiqora.plugins.context",
        "tiqora.plugins.sync_hooks",
        "tiqora.domain.ticket_write_service",
    ):
        monkeypatch.setattr(f"{mod}.get_session_factory", lambda fac=fac: fac, raising=False)
    SyncBreaker.reset_for_tests()
    SyncBreaker.persistence = False
    boot_points_plugin(tmp_path, monkeypatch)
    yield Env(sync_url, fac)
    SyncBreaker.reset_for_tests()
    await engine.dispose()
    delete_above_marks(sync_url, marks)
    clean = create_engine(sync_url)
    with clean.begin() as conn:
        conn.execute(text("DELETE FROM communication_channel WHERE name LIKE 'Plugin_points_%'"))
        _clean_seed(conn)
    clean.dispose()


def _calls(name: str) -> list[Any]:
    return [c for n, c in points_behaviour().CALLS if n == name]


def _client(env_: Env, monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.api.v1 import tickets as tickets_api
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    async def _db() -> Any:
        async with env_.factory() as session:
            yield session

    monkeypatch.setattr(tickets_api, "get_session_factory", lambda *a, **kw: env_.factory)
    monkeypatch.setattr(
        tickets_api,
        "_write_service",
        lambda session, settings: TicketWriteService(
            session, env_.factory, SysConfig(session), mail_sender=env_.sender
        ),
    )
    user = AuthenticatedUser(
        id=AGENT, login="pp55o-agent", first_name="P", last_name="A", auth_method="session"
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_agent_reply_sends_then_stores(env: Env) -> None:
    tid = await env.ticket()
    before = env.articles(tid)
    aid = await env.reply(tid)

    (sent,) = _calls("channel.Chat.send")
    assert isinstance(sent, OutboundChannelMessage)
    assert sent.ticket.id == tid and sent.ticket.queue_name == "pp55o-q"
    assert sent.agent_id == AGENT
    assert sent.to == "pp55-cust@example.org"
    assert sent.body == "Antwort" and sent.attachments == ()
    assert sent.subject == ""  # the channel has no subject

    assert env.articles(tid) == before + 1
    ((channel, sender, visible, msg_id, to, body),) = env.rows(
        "SELECT cc.name, st.name, a.is_visible_for_customer, m.a_message_id, m.a_to, m.a_body"
        " FROM article a JOIN communication_channel cc ON cc.id = a.communication_channel_id"
        " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
        " JOIN article_data_mime m ON m.article_id = a.id WHERE a.id = :a",
        a=aid,
    )
    assert (channel, sender, visible) == (CHANNEL_NAME, "agent", 1)
    assert msg_id == "<plugin_points_chat.m-1@tiqora.invalid>"
    assert to == "pp55-cust@example.org" and body == "Antwort"
    assert env.sender.sent == []  # never over SMTP


async def test_invisible_note_on_plugin_channel_is_not_sent(env: Env) -> None:
    tid = await env.ticket()
    aid = await env.reply(tid, is_visible_for_customer=False)
    assert _calls("channel.Chat.send") == []
    assert env.scalar("SELECT is_visible_for_customer FROM article WHERE id = :a", a=aid) == 0


async def test_send_failure_stores_nothing(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    tid = await env.ticket()
    before = env.articles(tid)
    points_behaviour().MODE["channel.send"] = "raise"
    with pytest.raises(ChannelDeliveryError) as info:
        await env.reply(tid)
    assert "Zustellung über „Chat (Test)“ fehlgeschlagen" in str(info.value)
    assert "kaputt" not in str(info.value)  # the plugin's own error text stays in the log
    assert env.articles(tid) == before
    assert len(_calls("channel.Chat.send")) == 1

    async with _client(env, monkeypatch) as client:
        resp = await client.post(
            f"/api/v1/tickets/{tid}/articles",
            json={
                "subject": "",
                "body": "Antwort",
                "channel": CHANNEL,
                "sender_type": "agent",
                "to_address": "pp55-cust@example.org",
                "is_visible_for_customer": True,
            },
        )
    assert resp.status_code == 502, resp.text
    assert "Zustellung über „Chat (Test)“ fehlgeschlagen" in resp.json()["detail"]
    assert env.articles(tid) == before

    points_behaviour().MODE.pop("channel.send")
    async with _client(env, monkeypatch) as client:
        ok = await client.post(
            f"/api/v1/tickets/{tid}/articles",
            json={
                "subject": "",
                "body": "Antwort",
                "channel": CHANNEL,
                "sender_type": "agent",
                "to_address": "pp55-cust@example.org",
                "is_visible_for_customer": True,
            },
        )
    assert ok.status_code == 201, ok.text  # positive control: the same request works
    assert env.articles(tid) == before + 1


async def test_send_timeout_bounded(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    tid = await env.ticket()
    before = env.articles(tid)
    real = plugin_channels.driver_for

    def short(channel: str) -> Any:
        driver = real(channel)
        return None if driver is None else replace(driver, timeout_seconds=1)

    monkeypatch.setattr(plugin_channels, "driver_for", short)
    points_behaviour().MODE["channel.send"] = "hang"
    started = time.monotonic()
    with pytest.raises(ChannelDeliveryError, match="Zeitüberschreitung"):
        await env.reply(tid)
    assert time.monotonic() - started < 5
    assert env.articles(tid) == before


async def test_capability_limits(env: Env) -> None:
    tid = await env.ticket()
    before = env.articles(tid)
    with pytest.raises(
        InvalidInput, match="zu lang für Chat \\(Test\\) \\(höchstens 500 Zeichen\\)"
    ):
        await env.reply(tid, body="x" * 501)
    with pytest.raises(InvalidInput, match="keine Anhänge"):
        await env.reply(tid, attachments=[("a.txt", "text/plain", b"abc")])
    assert _calls("channel.Chat.send") == []
    assert env.articles(tid) == before
    await env.reply(tid, body="x" * 500)  # exactly at the limit goes through
    assert len(_calls("channel.Chat.send")) == 1


async def test_limits_are_422_through_the_api(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    tid = await env.ticket()
    async with _client(env, monkeypatch) as client:
        resp = await client.post(
            f"/api/v1/tickets/{tid}/articles",
            json={
                "subject": "",
                "body": "x" * 501,
                "channel": CHANNEL,
                "sender_type": "agent",
                "to_address": "pp55-cust@example.org",
                "is_visible_for_customer": True,
            },
        )
    assert resp.status_code == 422, resp.text
    assert "höchstens 500 Zeichen" in resp.json()["detail"]
    assert _calls("channel.Chat.send") == []


async def test_inbound_only_channel_cannot_reply(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    tid = await env.ticket()
    before = env.articles(tid)
    real = plugin_channels.driver_for

    def inbound_only(channel: str) -> Any:
        driver = real(channel)
        return None if driver is None else replace(driver, send=None)

    monkeypatch.setattr(plugin_channels, "driver_for", inbound_only)
    with pytest.raises(InvalidInput, match="Über diesen Kanal kann nicht geantwortet werden"):
        await env.reply(tid)
    assert _calls("channel.Chat.send") == []
    assert env.articles(tid) == before


_ATT = [{"filename": "a.txt", "content_type": "text/plain", "content_base64": "YWJj"}]


def _att_body(**kw: Any) -> dict[str, Any]:
    return {
        "subject": "",
        "body": "Antwort",
        "channel": CHANNEL,
        "sender_type": "agent",
        "to_address": "pp55-cust@example.org",
        "is_visible_for_customer": True,
        "attachments": _ATT,
        **kw,
    }


async def test_attachment_without_capability_is_german_422_through_the_api(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = await env.ticket()
    before = env.articles(tid)
    async with _client(env, monkeypatch) as client:
        resp = await client.post(f"/api/v1/tickets/{tid}/articles", json=_att_body())
    assert resp.status_code == 422, resp.text
    assert "keine Anhänge" in resp.json()["detail"]
    assert _calls("channel.Chat.send") == []
    assert env.articles(tid) == before


async def test_attachment_with_capability_is_sent_through_the_api(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = await env.ticket()
    real = plugin_channels.driver_for

    def with_attachments(channel: str) -> Any:
        driver = real(channel)
        if driver is None:
            return None
        return replace(driver, capabilities=replace(driver.capabilities, attachments=True))

    monkeypatch.setattr(plugin_channels, "driver_for", with_attachments)
    async with _client(env, monkeypatch) as client:
        resp = await client.post(f"/api/v1/tickets/{tid}/articles", json=_att_body())
    assert resp.status_code == 201, resp.text
    (sent,) = _calls("channel.Chat.send")
    assert sent.attachments == (("a.txt", "text/plain", b"abc"),)


async def test_veto_hook_runs_before_channel_send(env: Env) -> None:
    tid = await env.ticket()
    before = env.articles(tid)
    points_behaviour().MODE["hook.article.create"] = "veto"
    with pytest.raises(OperationVetoed, match=VETO):
        await env.reply(tid)
    assert len(_calls("hook.article.create")) == 1
    assert _calls("channel.Chat.send") == []  # nothing left the building
    assert env.articles(tid) == before


async def test_composer_list(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _client(env, monkeypatch) as client:
        resp = await client.get("/api/v1/channels/plugin")
    assert resp.status_code == 200, resp.text
    assert resp.json() == [
        {
            "key": CHANNEL,
            "name": CHANNEL_NAME,
            "label": "Chat (Test)",
            "plugin_id": PLUGIN_ID,
            "capabilities": {
                "subject": False,
                "html": False,
                "attachments": False,
                "max_length": 500,
            },
            "can_reply": True,
        }
    ]


async def test_reply_on_a_channel_whose_plugin_is_gone_is_refused(env: Env) -> None:
    """Disabled, failed or not loaded: never stored as a visible Internal article with a 201."""
    from tiqora.plugins.state import set_loaded_plugins

    tid = await env.ticket()
    before = env.articles(tid)
    set_loaded_plugins([])
    assert plugin_channels.driver_for(CHANNEL) is None
    with pytest.raises(InvalidInput, match="derzeit nicht verfügbar"):
        await env.reply(tid)
    with pytest.raises(InvalidInput, match="derzeit nicht verfügbar"):
        await env.reply(tid, is_visible_for_customer=False)
    assert env.articles(tid) == before
    assert _calls("channel.Chat.send") == [] and env.sender.sent == []


def _own_tickets() -> Any:
    """``ctx.tickets`` exactly as the fixture plugin got it from ``make_plugin_context``."""
    driver = plugin_channels.driver_for(CHANNEL)
    assert driver is not None and driver.context is not None
    return driver.context.tickets


async def test_plugin_records_a_message_on_its_own_channel(env: Env) -> None:
    """D3: a plugin may write on its own channel; without ``as_agent`` it is stored, not sent."""
    tid = await env.ticket()
    aid = await _own_tickets().add_article(
        tid,
        body="Erinnerung",
        subject="Erinnerung",
        sender_type="system",
        channel="Plugin_points_Chat",
        visible_for_customer=True,
    )
    ((channel, sender, visible),) = env.rows(
        "SELECT cc.name, st.name, a.is_visible_for_customer FROM article a"
        " JOIN communication_channel cc ON cc.id = a.communication_channel_id"
        " JOIN article_sender_type st ON st.id = a.article_sender_type_id WHERE a.id = :a",
        a=aid,
    )
    assert (channel, sender, visible) == (CHANNEL_NAME, "system", 1)
    assert _calls("channel.Chat.send") == []  # the plugin delivered it itself


async def test_plugin_agent_message_on_its_own_channel_is_sent(env: Env) -> None:
    tid = await env.ticket()
    before = env.articles(tid)
    aid = await _own_tickets().add_article(
        tid, body="Ihr Termin", channel=CHANNEL, visible_for_customer=True, as_agent=AGENT
    )
    (sent,) = _calls("channel.Chat.send")
    assert sent.ticket.id == tid and sent.agent_id == AGENT and sent.body == "Ihr Termin"
    assert sent.to is None  # the plugin routes by message.ticket
    assert env.articles(tid) == before + 1
    ((channel, msg_id),) = env.rows(
        "SELECT cc.name, m.a_message_id FROM article a"
        " JOIN communication_channel cc ON cc.id = a.communication_channel_id"
        " JOIN article_data_mime m ON m.article_id = a.id WHERE a.id = :a",
        a=aid,
    )
    assert (channel, msg_id) == (CHANNEL_NAME, "<plugin_points_chat.m-1@tiqora.invalid>")


async def test_plugin_creates_a_ticket_on_its_own_channel(env: Env) -> None:
    tid = await _own_tickets().create(
        TicketDraft(title="Neu", queue="pp55o-q", body="Hallo", channel=CHANNEL)
    )
    assert [
        r[0]
        for r in env.rows(
            "SELECT cc.name FROM article a"
            " JOIN communication_channel cc ON cc.id = a.communication_channel_id"
            " WHERE a.ticket_id = :t",
            t=tid,
        )
    ] == [CHANNEL_NAME]
    assert _calls("channel.Chat.send") == []


async def test_other_callers_cannot_write_on_a_plugin_channel(env: Env) -> None:
    tid = await env.ticket()
    before = env.articles(tid)
    for tickets in (PluginTickets(), PluginTickets("org.example.other")):
        with pytest.raises(PluginWriteError, match="einen eigenen Kanal"):
            await tickets.add_article(tid, body="x", channel=CHANNEL)
        with pytest.raises(PluginWriteError, match="einen eigenen Kanal"):
            await tickets.create(TicketDraft(title="x", queue="pp55o-q", body="b", channel=CHANNEL))
    with pytest.raises(PluginWriteError, match="einen eigenen Kanal"):
        await _own_tickets().add_article(tid, body="x", channel="email")
    assert env.articles(tid) == before
    assert _calls("channel.Chat.send") == []


def test_plugin_channels_are_tiqora_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert not is_tiqora_only_channel("plugin_points_chat")  # no plugin loaded yet
    boot_points_plugin(tmp_path, monkeypatch)
    assert is_tiqora_only_channel("plugin_points_chat") and is_tiqora_only_channel("telegram")
    assert is_tiqora_only_channel("Plugin_Points_Chat")  # case-insensitive like the core set
    assert not is_tiqora_only_channel("email")
    assert not is_tiqora_only_channel(None) and not is_tiqora_only_channel("")
