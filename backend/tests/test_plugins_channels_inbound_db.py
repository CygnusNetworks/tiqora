"""Channel drivers from plugins: registration, channel row, inbound ``ingest`` (D11, both dialects).

Seed band 55300-55339: queue 55300 ``pp55-chatq``, customers ``pp55-cust`` (mail
``pp55-cust@example.org``) and ``pp55-phone`` (mobile +49 30 555 0101). Tickets and articles the
service creates are removed with ``snapshot_marks``/``delete_above_marks``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugin_points import boot_points_plugin, points_behaviour
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.api.app import create_app
from tiqora.channels.common import EMPTY_TABLES_CHANNEL_DATA, ensure_webchat_channel_row
from tiqora.config import Settings
from tiqora.extensions.core import get_registry
from tiqora.plugins.channels import (
    CHANNEL_DRIVERS,
    InboundChannelMessage,
    PluginChannels,
    driver_for,
)
from tiqora.plugins.routes import mount_plugin_routes
from tiqora.plugins.state import set_loaded_plugins
from tiqora.plugins.writes import PluginWriteError

pytestmark = pytest.mark.db

PLUGIN_ID = "org.example.points"
QUEUE = 55300
CHANNEL_NAME = "Plugin_points_Chat"
MARK_TABLES: tuple[str, ...] = (
    "tiqora_event_outbox",
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
    conn.execute(text("DELETE FROM customer_user WHERE login IN ('pp55-cust', 'pp55-phone')"))
    conn.execute(text("DELETE FROM queue WHERE id = 55300"))


def _seed(sync_url: str) -> None:
    from tiqora.db.tiqora.base import TiqoraBase

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn, checkfirst=True)
    with engine.begin() as conn:
        _clean_seed(conn)
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, unlock_timeout, system_address_id,"
                " salutation_id, signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (55300, 'pp55-chatq', 1, 0, 1, 1, 1, 1, 0, 1,"
                " current_timestamp, 1, current_timestamp, 1)"
            )
        )
        for login, mail, mobile in (
            ("pp55-cust", "pp55-cust@example.org", None),
            ("pp55-phone", "pp55-phone@example.org", "+49 30 555 0101"),
        ):
            conn.execute(
                text(
                    "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
                    " mobile, valid_id, create_time, create_by, change_time, change_by)"
                    " VALUES (:l, :m, 'pp55-co', 'Kim', 'Kunde', :mob, 1,"
                    " current_timestamp, 1, current_timestamp, 1)"
                ),
                {"l": login, "m": mail, "mob": mobile},
            )
    engine.dispose()


class Env:
    def __init__(self, sync_url: str) -> None:
        self.sync_url = sync_url
        self.base_tickets = 0

    def rows(self, sql: str, **params: Any) -> list[Any]:
        engine = create_engine(self.sync_url)
        try:
            with engine.begin() as conn:
                return list(conn.execute(text(sql), params).all())
        finally:
            engine.dispose()

    def scalar(self, sql: str, **params: Any) -> Any:
        (row,) = self.rows(sql, **params)
        return row[0]

    def articles(self, ticket_id: int) -> int:
        return int(self.scalar("SELECT COUNT(*) FROM article WHERE ticket_id = :t", t=ticket_id))

    def tickets(self) -> int:
        """Tickets created since the fixture started (the base schema ships one)."""
        return int(self.scalar("SELECT COUNT(*) FROM ticket")) - self.base_tickets

    def channel_row(self) -> Any:
        rows = self.rows(
            "SELECT id, module, package_name, channel_data FROM communication_channel"
            " WHERE name = :n",
            n=CHANNEL_NAME,
        )
        assert len(rows) == 1
        return rows[0]


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
        "tiqora.domain.ticket_write_service",
    ):
        monkeypatch.setattr(f"{mod}.get_session_factory", lambda fac=fac: fac, raising=False)
    boot_points_plugin(tmp_path, monkeypatch, role="worker")
    env_ = Env(sync_url)
    env_.base_tickets = int(env_.scalar("SELECT COUNT(*) FROM ticket"))
    yield env_
    await engine.dispose()
    delete_above_marks(sync_url, marks)
    clean = create_engine(sync_url)
    with clean.begin() as conn:
        conn.execute(text("DELETE FROM communication_channel WHERE name LIKE 'Plugin_points_%'"))
        _clean_seed(conn)
    clean.dispose()


def _msg(**kw: Any) -> InboundChannelMessage:
    kw.setdefault("sender", "pp55-cust@example.org")
    kw.setdefault("body", "Hallo")
    return InboundChannelMessage(**kw)


async def test_ingest_creates_ticket_on_plugin_channel(env: Env) -> None:
    res = await PluginChannels(PLUGIN_ID).ingest("Chat", _msg(external_id="e-1"))
    assert res.created is True and res.duplicate is False

    channel_id, module, package, data = env.channel_row()
    assert module == "Kernel::System::CommunicationChannel::Internal"
    assert package == "Tiqora"
    stored = data.decode() if isinstance(data, bytes | bytearray) else str(data)
    assert stored == EMPTY_TABLES_CHANNEL_DATA

    ((cc, sender, visible, a_from, subject, body, msg_id),) = env.rows(
        "SELECT a.communication_channel_id, st.name, a.is_visible_for_customer, m.a_from,"
        " m.a_subject, m.a_body, m.a_message_id FROM article a"
        " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
        " JOIN article_data_mime m ON m.article_id = a.id WHERE a.id = :a",
        a=res.article_id,
    )
    assert cc == channel_id and sender == "customer" and visible == 1
    assert a_from == "pp55-cust@example.org"  # the handle, so a reply knows where to go
    assert body == "Hallo" and subject.startswith("Chat (Test)")
    assert msg_id == "<plugin_points_chat.e-1@tiqora.invalid>"

    ((customer_user, customer_id, tn),) = env.rows(
        "SELECT customer_user_id, customer_id, tn FROM ticket WHERE id = :t", t=res.ticket_id
    )
    assert (customer_user, customer_id) == ("pp55-cust", "pp55-co")
    assert tn
    history = env.rows(
        "SELECT ht.name FROM ticket_history h JOIN ticket_history_type ht"
        " ON ht.id = h.history_type_id WHERE h.ticket_id = :t ORDER BY h.id",
        t=res.ticket_id,
    )
    assert "WebRequestCustomer" in [h[0] for h in history]


async def test_ingest_is_idempotent(env: Env) -> None:
    channels = PluginChannels(PLUGIN_ID)
    first = await channels.ingest("Chat", _msg(external_id="e-1"))
    again = await channels.ingest("Chat", _msg(external_id="e-1"))
    assert again.duplicate is True and again.created is False
    assert (again.ticket_id, again.article_id) == (first.ticket_id, first.article_id)
    assert env.articles(first.ticket_id) == 1
    assert env.tickets() == 1


async def test_ingest_appends_to_open_ticket(env: Env) -> None:
    channels = PluginChannels(PLUGIN_ID)
    first = await channels.ingest("Chat", _msg(external_id="e-1"))
    second = await channels.ingest("Chat", _msg(external_id="e-2", body="Noch eine Frage"))
    assert second.created is False and second.duplicate is False
    assert second.ticket_id == first.ticket_id and second.article_id != first.article_id
    assert env.articles(first.ticket_id) == 2
    # without an external id there is no idempotency: every call is a new article
    third = await channels.ingest("Chat", _msg(body="Und noch eine"))
    assert third.duplicate is False and third.ticket_id == first.ticket_id
    assert env.articles(first.ticket_id) == 3


async def test_ingest_phone_match_and_unknown_sender(env: Env) -> None:
    channels = PluginChannels(PLUGIN_ID)
    by_phone = await channels.ingest("Chat", _msg(sender="+49 30 555 0101"))
    assert env.rows(
        "SELECT customer_user_id, customer_id FROM ticket WHERE id = :t", t=by_phone.ticket_id
    ) == [("pp55-phone", "pp55-co")]

    stranger = await channels.ingest("Chat", _msg(sender="stranger-77", sender_display="Fremder"))
    assert env.rows(
        "SELECT customer_user_id, customer_id FROM ticket WHERE id = :t", t=stranger.ticket_id
    ) == [("plugin_points_chat:stranger-77", None)]
    assert stranger.created is True and stranger.ticket_id != by_phone.ticket_id
    ((a_from,),) = env.rows(
        "SELECT a_from FROM article_data_mime WHERE article_id = :a", a=stranger.article_id
    )
    assert a_from == "stranger-77"  # the handle, not the display name
    ((title,),) = env.rows("SELECT title FROM ticket WHERE id = :t", t=stranger.ticket_id)
    assert "Fremder" in title

    # the unknown sender's next message lands on the same ticket
    again = await channels.ingest("Chat", _msg(sender="stranger-77", body="Hallo?"))
    assert again.created is False and again.ticket_id == stranger.ticket_id


async def test_handle_does_not_phone_match_customer(env: Env) -> None:
    # pp55-phone's mobile ends in 1; "max-1" is a handle, not a phone number
    res = await PluginChannels(PLUGIN_ID).ingest("Chat", _msg(sender="max-1"))
    assert env.rows(
        "SELECT customer_user_id, customer_id FROM ticket WHERE id = :t", t=res.ticket_id
    ) == [("plugin_points_chat:max-1", None)]


async def test_handle_equal_to_login_does_not_join_customer(env: Env) -> None:
    channels = PluginChannels(PLUGIN_ID)
    known = await channels.ingest("Chat", _msg(sender="pp55-cust@example.org"))
    before = env.articles(known.ticket_id)
    stranger = await channels.ingest("Chat", _msg(sender="pp55-cust", body="Ich bin jemand"))
    assert stranger.created is True and stranger.ticket_id != known.ticket_id
    assert env.articles(known.ticket_id) == before
    assert (
        env.scalar("SELECT customer_user_id FROM ticket WHERE id = :t", t=stranger.ticket_id)
        == "plugin_points_chat:pp55-cust"
    )


async def test_explicit_customer_queue_and_attachment(env: Env) -> None:
    res = await PluginChannels(PLUGIN_ID).ingest(
        "Chat",
        _msg(
            sender="handle-1",
            customer_user="pp55-cust",
            queue="pp55-chatq",
            attachments=(("bild.txt", "text/plain", b"abc"),),
        ),
    )
    assert env.rows(
        "SELECT customer_user_id, customer_id, queue_id FROM ticket WHERE id = :t",
        t=res.ticket_id,
    ) == [("pp55-cust", "pp55-co", QUEUE)]
    assert (
        env.scalar(
            "SELECT COUNT(*) FROM article_data_mime_attachment WHERE article_id = :a",
            a=res.article_id,
        )
        == 1
    )


async def test_invalid_input_writes_nothing(env: Env) -> None:
    channels = PluginChannels(PLUGIN_ID)
    for bad, match in (
        (_msg(sender="  "), "Absender"),
        (_msg(sender="x" * 151), "Absender"),
        (_msg(external_id="a b"), "externe Kennung"),
        (_msg(external_id="<x>"), "externe Kennung"),
        (_msg(external_id="x" * 101), "externe Kennung"),
        (_msg(queue="gibt-es-nicht"), "gibt es nicht"),
    ):
        with pytest.raises(PluginWriteError, match=match):
            await channels.ingest("Chat", bad)
    assert env.tickets() == 0
    assert env.rows("SELECT id FROM communication_channel WHERE name = :n", n=CHANNEL_NAME) == []


async def test_foreign_channel_name_refused(env: Env) -> None:
    with pytest.raises(PluginWriteError, match="gehört nicht zu dieser Erweiterung"):
        await PluginChannels(PLUGIN_ID).ingest("Fremd", _msg())
    with pytest.raises(PluginWriteError, match="gehört nicht zu dieser Erweiterung"):
        await PluginChannels("org.example.other").ingest("Chat", _msg())
    # a core channel name is not a plugin channel either
    with pytest.raises(PluginWriteError, match="gehört nicht zu dieser Erweiterung"):
        await PluginChannels(PLUGIN_ID).ingest("Email", _msg())
    assert env.tickets() == 0


async def test_inactive_plugin_cannot_ingest(env: Env) -> None:
    set_loaded_plugins([])
    with pytest.raises(PluginWriteError, match="gehört nicht zu dieser Erweiterung"):
        await PluginChannels(PLUGIN_ID).ingest("Chat", _msg())
    assert driver_for("plugin_points_chat") is None
    assert env.tickets() == 0


async def test_driver_for_is_case_insensitive_and_plugin_only(env: Env) -> None:
    driver = driver_for("Plugin_Points_CHAT")
    assert driver is not None and driver.key == "plugin_points_chat"
    assert driver.name == CHANNEL_NAME and driver.plugin_id == PLUGIN_ID
    assert driver.capabilities.max_length == 500 and driver.send is not None
    for core in ("email", "phone", "internal", "note", "telegram", "webchat", "nope"):
        assert driver_for(core) is None


async def test_public_inbound_route(env: Env) -> None:
    app = create_app(Settings(environment="test"))
    mount_plugin_routes(app, get_registry())
    payload = {"sender": "pp55-cust@example.org", "body": "Hallo", "external_id": "r-1"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(f"/api/plugins/{PLUGIN_ID}/inbound", json=payload)
        again = await client.post(f"/api/plugins/{PLUGIN_ID}/inbound", json=payload)
        foreign = await client.post(
            f"/api/plugins/{PLUGIN_ID}/inbound", json={**payload, "channel": "Fremd"}
        )
    assert first.status_code == 200
    body = first.json()
    assert body["created"] is True and body["duplicate"] is False
    assert again.status_code == 200
    assert again.json() == {**body, "created": False, "duplicate": True}
    assert env.articles(body["ticket_id"]) == 1
    assert foreign.status_code == 422 and "gehört nicht" in foreign.json()["detail"]
    assert env.tickets() == 1
    assert [c[0] for c in points_behaviour().CALLS].count("channel.Chat.ingest") == 2


def test_registration_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.plugins.channels import ChannelCapabilities
    from tiqora.plugins.context import make_plugin_context
    from tiqora.plugins.discovery import discover_plugins
    from tiqora.plugins.loader import import_and_register
    from tiqora.plugins.registrar import RegistrationRejected

    boot_points_plugin(tmp_path, monkeypatch)
    (discovered,) = [d for d in discover_plugins() if d.plugin_id == PLUGIN_ID]
    registrar = import_and_register(discovered, make_plugin_context(discovered))

    async def async_send(ctx: Any, message: Any) -> Any:
        return None

    def sync_send(ctx: Any, message: Any) -> Any:
        return None

    def reject(match: str, name: str = "Fine", **over: Any) -> None:
        kw: dict[str, Any] = {"label": "Kanal", "send": async_send, **over}
        with pytest.raises(RegistrationRejected, match=match):
            registrar.channel(name, **kw)

    reject("Ungültiger Name", name="lower")
    reject("Ungültiger Name", name="email")  # not Plugin_… capitalised: refused, never "email"
    reject("Bezeichnung", label="")
    reject("Bezeichnung", label="x" * 101)
    reject("Höchstlänge", capabilities=ChannelCapabilities(max_length=0))
    reject("Höchstlänge", capabilities=ChannelCapabilities(max_length=True))  # type: ignore[arg-type]
    reject("Fähigkeiten", capabilities="viel")
    reject("zwischen 1 und 30", timeout_seconds=0)
    reject("zwischen 1 und 30", timeout_seconds=31)
    reject("zwischen 1 und 30", timeout_seconds=True)
    reject("async-Funktion", send=sync_send)
    # a core channel name is qualified, never shadowed
    assert registrar.channel("Email", label="Mail-Kanal", send=async_send) == "plugin_points_email"
    assert driver_for("email") is None  # the staged plugin item is not live and not "email"
    with pytest.raises(RegistrationRejected, match="bereits registriert"):
        registrar.channel("Email", label="Mail-Kanal")
    # names that differ only in case would share one channel key
    with pytest.raises(RegistrationRejected, match="bereits registriert"):
        registrar.channel("EMail", label="Mail-Kanal")
    # inbound only is fine
    assert registrar.channel("Nurein", label="Nur Eingang") == "plugin_points_nurein"
    # the registered fixture driver itself
    assert [d.key for d in get_registry().items(CHANNEL_DRIVERS)] == ["plugin_points_chat"]


async def test_webchat_row_unchanged(env: Env) -> None:
    existed = env.rows("SELECT id FROM communication_channel WHERE name = 'WebChat'")
    engine = create_async_engine(_to_async_url(env.sync_url))
    try:
        async with async_sessionmaker(engine, class_=AsyncSession)() as session:
            channel_id = await ensure_webchat_channel_row(session)
            again = await ensure_webchat_channel_row(session)
            await session.commit()
    finally:
        await engine.dispose()
    assert again == channel_id
    ((cid, module, package, data),) = env.rows(
        "SELECT id, module, package_name, channel_data FROM communication_channel"
        " WHERE name = 'WebChat'"
    )
    assert cid == channel_id
    assert module == "Kernel::System::CommunicationChannel::Internal"
    assert package == "Tiqora"
    stored = data.decode() if isinstance(data, bytes | bytearray) else str(data)
    assert stored == EMPTY_TABLES_CHANNEL_DATA
    if not existed:
        engine2 = create_engine(env.sync_url)
        with engine2.begin() as conn:
            conn.execute(text("DELETE FROM communication_channel WHERE name = 'WebChat'"))
        engine2.dispose()
