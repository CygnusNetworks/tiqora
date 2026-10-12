"""Notification transports from plugins (D10, both dialects).

Seed band 55260-55299: queue 55260, agents 55261 (owner) and 55262 (responsible), tickets 55261
(agents) and 55262 (customer ``pp55-cust``), notification events 55261-55270. Rows the engine
adds (ticket history) are removed with the ticket.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugin_points import boot_points_plugin, points_behaviour
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.channels.email.smtp import CapturingMailSender
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions.core import get_registry
from tiqora.plugins import notification_transports
from tiqora.plugins.notification_transports import NOTIFICATION_TRANSPORTS
from tiqora.plugins.state import set_loaded_plugins
from tiqora.worker import notifications
from tiqora.worker.notifications import process_event
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

QUEUE = 55260
OWNER, RESPONSIBLE = 55261, 55262
TICKET_AGENTS, TICKET_CUSTOMER = 55261, 55262
NOTIFY = 55261  # notification_event ids 55261..
CHAT = "Plugin_points_Chat"
MARK_TABLES: tuple[str, ...] = ("tiqora_cache_invalidation", "tiqora_event_outbox")
ROOT = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _clean(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for sql in (
            "DELETE FROM ticket_history WHERE ticket_id IN (55261, 55262)",
            "DELETE FROM article WHERE ticket_id IN (55261, 55262)",
            "DELETE FROM ticket WHERE id IN (55261, 55262)",
            "DELETE FROM notification_event_message WHERE notification_id BETWEEN 55261 AND 55270",
            "DELETE FROM notification_event_item WHERE notification_id BETWEEN 55261 AND 55270",
            "DELETE FROM notification_event WHERE id BETWEEN 55261 AND 55270",
            "DELETE FROM user_preferences WHERE user_id IN (55261, 55262)",
            "DELETE FROM users WHERE id IN (55261, 55262)",
            "DELETE FROM customer_user WHERE login = 'pp55-cust'",
            "DELETE FROM queue WHERE id = 55260",
        ):
            conn.execute(text(sql))
    engine.dispose()


def _seed(sync_url: str) -> None:
    from tiqora.db.tiqora.base import TiqoraBase

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn, checkfirst=True)
    _clean(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, unlock_timeout, system_address_id,"
                " salutation_id, signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (55260, 'pp55-notify', 1, 0, 1, 1, 1, 1, 0, 1,"
                " current_timestamp, 1, current_timestamp, 1)"
            )
        )
        for uid, login, first, last, mail in (
            (OWNER, "pp55.owner", "Olga", "Owner", "owner@example.org"),
            (RESPONSIBLE, "pp55.resp", "Rolf", "Responsible", "resp@example.org"),
        ):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, :login, 'x', :first, :last, 1,"
                    " current_timestamp, 1, current_timestamp, 1)"
                ),
                {"uid": uid, "login": login, "first": first, "last": last},
            )
            for key, value in (("UserEmail", mail), ("UserLanguage", "en")):
                conn.execute(
                    text(
                        "INSERT INTO user_preferences (user_id, preferences_key, preferences_value)"
                        " VALUES (:uid, :k, :v)"
                    ),
                    {"uid": uid, "k": key, "v": value},
                )
        conn.execute(
            text(
                "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES ('pp55-cust', 'cust@example.org', 'pp55', 'Carla', 'Kunde', 1,"
                " current_timestamp, 1, current_timestamp, 1)"
            )
        )
        for tid, tn, cust in (
            (TICKET_AGENTS, "PP55N1", None),
            (TICKET_CUSTOMER, "PP55N2", "pp55-cust"),
        ):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time, escalation_solution_time,"
                    " archive_flag, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'Drucker defekt', 55260, 1, 1, :owner, :resp, 3, 4,"
                    " 'pp55', :cust, 0, 0, 0, 0, 0, 0, 0,"
                    " current_timestamp, 1, current_timestamp, 1)"
                ),
                {"id": tid, "tn": tn, "owner": OWNER, "resp": RESPONSIBLE, "cust": cust},
            )
    engine.dispose()


async def _notification(
    session: AsyncSession, nid: int, transports: Iterable[str], recipients: Iterable[str]
) -> None:
    await session.execute(
        text(
            "INSERT INTO notification_event (id, name, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:id, :name, 1, current_timestamp, 1,"
            " current_timestamp, 1)"
        ),
        {"id": nid, "name": f"pp55-notify-{nid}"},
    )
    items = {
        "Events": ["TicketCreate"],
        "QueueID": ["55260"],
        "Recipients": list(recipients),
        "Transports": list(transports),
    }
    for key, values in items.items():
        for value in values:
            await session.execute(
                text(
                    "INSERT INTO notification_event_item (notification_id, event_key, event_value)"
                    " VALUES (:nid, :k, :v)"
                ),
                {"nid": nid, "k": key, "v": value},
            )
    await session.execute(
        text(
            "INSERT INTO notification_event_message"
            " (notification_id, subject, text, content_type, language)"
            " VALUES (:nid, 'Neu: <OTRS_TICKET_Title>', 'Ticket <OTRS_TICKET_TicketNumber>',"
            " 'text/plain', 'en')"
        ),
        {"nid": nid},
    )
    await session.commit()


class Env:
    def __init__(self, sync_url: str, fac: async_sessionmaker[AsyncSession]) -> None:
        self.sync_url = sync_url
        self.factory = fac
        self.sender = CapturingMailSender()

    async def run(
        self,
        transports: Iterable[str],
        *,
        ticket_id: int = TICKET_AGENTS,
        recipients: Iterable[str] = ("AgentOwner", "AgentResponsible"),
    ) -> tuple[int, int]:
        async with self.factory() as session:
            await _notification(session, NOTIFY, transports, recipients)
        async with self.factory() as session, session.begin():
            return await process_event(
                session,
                SysConfig(session),
                self.sender,
                event_type="TicketCreate",
                ticket_id=ticket_id,
                payload={},
                user_id=1,
            )

    async def history(self, ticket_id: int = TICKET_AGENTS) -> int:
        async with self.factory() as session:
            row = (
                await session.execute(
                    text("SELECT COUNT(*) FROM ticket_history WHERE ticket_id = :t"),
                    {"t": ticket_id},
                )
            ).first()
            assert row is not None
            return int(row[0])


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    marks = snapshot_marks(sync_url, MARK_TABLES)
    engine = create_async_engine(_to_async_url(sync_url))
    fac = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    notifications._warned_transports.clear()
    boot_points_plugin(tmp_path, monkeypatch, role="worker")
    yield Env(sync_url, fac)
    await engine.dispose()
    delete_above_marks(sync_url, marks)
    _clean(sync_url)


def _chat_calls() -> list[tuple[str, str, str]]:
    return [c[1] for c in points_behaviour().CALLS if c[0] == "transport.Chat"]  # type: ignore[misc]


def _messages() -> list[Any]:
    return [c[1] for c in points_behaviour().CALLS if c[0] == "transport.Chat.message"]


async def test_plugin_only_transport(env: Env) -> None:
    sent, failed = await env.run([CHAT])
    assert (sent, failed) == (2, 0)
    assert env.sender.sent == []
    calls = sorted(_chat_calls(), key=lambda c: c[1])
    assert [(kind, email) for kind, email, _ in calls] == [
        ("agent", "owner@example.org"),
        ("agent", "resp@example.org"),
    ]
    assert {subject for _, _, subject in calls} == {"[Ticket#PP55N1] Neu: Drucker defekt"}


async def test_message_carries_ticket_and_recipient(env: Env) -> None:
    await env.run([CHAT])
    by_mail = {m.recipient.email: m for m in _messages()}
    owner = by_mail["owner@example.org"]
    assert owner.recipient.kind == "agent"
    assert owner.recipient.user_id == OWNER
    assert owner.recipient.customer_login is None
    assert owner.recipient.full_name == "Olga Owner"
    assert owner.recipient.language == "en"
    assert owner.ticket.id == TICKET_AGENTS and owner.ticket.tn == "PP55N1"
    assert owner.notification_id == NOTIFY and owner.notification_name == f"pp55-notify-{NOTIFY}"
    assert owner.event == "TicketCreate"
    assert owner.body == "Ticket PP55N1" and owner.content_type == "text/plain"
    assert by_mail["resp@example.org"].recipient.user_id == RESPONSIBLE


async def test_customer_recipient_fields(env: Env) -> None:
    sent, failed = await env.run([CHAT], ticket_id=TICKET_CUSTOMER, recipients=("Customer",))
    assert (sent, failed) == (1, 0)
    (message,) = _messages()
    assert message.recipient.kind == "customer"
    assert message.recipient.user_id is None
    assert message.recipient.customer_login == "pp55-cust"
    assert message.recipient.full_name == "Carla Kunde"
    assert message.recipient.email == "cust@example.org"
    assert env.sender.sent == []
    assert await env.history(TICKET_CUSTOMER) == 0


async def test_email_and_plugin(env: Env) -> None:
    sent, failed = await env.run(["Email", CHAT])
    assert (sent, failed) == (4, 0)
    assert sorted(str(m["To"]) for m in env.sender.sent) == [
        "owner@example.org",
        "resp@example.org",
    ]
    assert len(_chat_calls()) == 2
    # one subject for both ways: rendered exactly as for the mail
    assert {str(m["Subject"]) for m in env.sender.sent} == {s for _, _, s in _chat_calls()}


async def test_email_only_unchanged_and_default_is_email(env: Env) -> None:
    assert await env.run(["Email"]) == (2, 0)
    assert len(env.sender.sent) == 2
    assert _chat_calls() == []


async def test_unknown_transport_ignored(env: Env) -> None:
    assert await env.run(["SMS"]) == (0, 0)
    assert env.sender.sent == []
    assert _chat_calls() == []
    assert await env.history() == 0


async def test_unknown_next_to_known_still_delivers(env: Env) -> None:
    assert await env.run(["SMS", CHAT]) == (2, 0)
    assert len(_chat_calls()) == 2 and env.sender.sent == []


async def test_failing_transport_counted(env: Env) -> None:
    points_behaviour().MODE["transport"] = "raise"
    sent, failed = await env.run(["Email", CHAT])
    assert (sent, failed) == (2, 2)  # (n_email, n_recipients)
    assert len(env.sender.sent) == 2  # the mail still went out
    assert len(_chat_calls()) == 2  # every recipient was tried


async def test_hanging_transport_bounded(env: Env) -> None:
    points_behaviour().MODE["transport"] = "hang"
    started = time.monotonic()
    sent, failed = await env.run([CHAT])
    elapsed = time.monotonic() - started
    assert (sent, failed) == (0, 2)
    assert elapsed < 5.0  # 1 s timeout per recipient, never the plugin's 30 s


async def test_no_history_for_plugin_transport(env: Env) -> None:
    assert await env.run([CHAT]) == (2, 0)
    assert await env.history() == 0


async def test_history_only_for_the_email_transport(env: Env) -> None:
    assert await env.run(["Email", CHAT]) == (4, 0)
    assert await env.history() == 2  # SendAgentNotification per recipient, none for the chat


async def test_inactive_plugin_transport_is_ignored(env: Env) -> None:
    set_loaded_plugins([])
    assert await env.run([CHAT]) == (0, 0)
    assert _chat_calls() == []


# -- registration and the admin listing -------------------------------------------------------


def test_notification_transport_point_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boot_points_plugin(tmp_path, monkeypatch)
    items = list(get_registry().items(NOTIFICATION_TRANSPORTS))
    assert [(t.key, t.plugin_id, t.label, t.timeout_seconds) for t in items] == [
        (CHAT, "org.example.points", "Chat (Test)", 1)
    ]
    assert NOTIFICATION_TRANSPORTS.key == "core.notifications.transports"
    assert NOTIFICATION_TRANSPORTS.sdk_stable is True
    assert notification_transports.active_transports([CHAT, "Email", "SMS"]) == items
    set_loaded_plugins([])
    assert notification_transports.active_transports([CHAT]) == []


async def _async_send(ctx: Any, message: Any) -> None:
    return None


def _sync_send(ctx: Any, message: Any) -> None:
    return None


def test_registration_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.plugins.context import make_plugin_context
    from tiqora.plugins.discovery import discover_plugins
    from tiqora.plugins.loader import import_and_register
    from tiqora.plugins.registrar import RegistrationRejected

    boot_points_plugin(tmp_path, monkeypatch)
    (discovered,) = [d for d in discover_plugins() if d.plugin_id == "org.example.points"]
    registrar = import_and_register(discovered, make_plugin_context(discovered))

    def reject(match: str, name: str = "Fine", send: Any = _async_send, **over: Any) -> None:
        with pytest.raises(RegistrationRejected, match=match):
            registrar.notification_transport(name, send, **{"label": "Weg", **over})

    reject("Ungültiger Name", name="lower")
    reject("Bezeichnung", label="")
    reject("Bezeichnung", label="x" * 101)
    reject("async-Funktion", send=_sync_send)
    reject("zwischen 1 und 60", timeout_seconds=0)
    reject("zwischen 1 und 60", timeout_seconds=61)
    reject("zwischen 1 und 60", timeout_seconds=True)
    # a core name is qualified, never shadowed
    assert (
        registrar.notification_transport("Email", _async_send, label="Weg") == "Plugin_points_Email"
    )
    with pytest.raises(RegistrationRejected, match="bereits registriert"):
        registrar.notification_transport("Email", _async_send, label="Weg")


def test_undeclared_capability_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.plugins.context import make_plugin_context
    from tiqora.plugins.discovery import discover_plugins
    from tiqora.plugins.loader import import_and_register
    from tiqora.plugins.registrar import RegistrationRejected

    boot_points_plugin(tmp_path, monkeypatch)
    (discovered,) = [d for d in discover_plugins() if d.plugin_id == "org.example.points"]
    registrar = import_and_register(discovered, make_plugin_context(discovered))
    registrar._manifest.capabilities.extension_points.remove("core.notifications.transports")
    with pytest.raises(RegistrationRejected, match="core.notifications.transports"):
        registrar.notification_transport("Other", _async_send, label="Weg")


@pytest.fixture
async def admin(env: Env) -> AsyncIterator[AsyncClient]:
    async def _db() -> Any:
        async with env.factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_admin_user] = lambda: ROOT
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_admin_lists_transports(env: Env, admin: AsyncClient) -> None:
    response = await admin.get("/api/v1/admin/notification-events/transports")
    assert response.status_code == 200
    assert response.json() == [
        {"key": "Email", "label": "E-Mail", "plugin_id": None},
        {"key": CHAT, "label": "Chat (Test)", "plugin_id": "org.example.points"},
    ]


async def test_admin_lists_only_email_without_plugins(env: Env, admin: AsyncClient) -> None:
    set_loaded_plugins([])
    response = await admin.get("/api/v1/admin/notification-events/transports")
    assert response.json() == [{"key": "Email", "label": "E-Mail", "plugin_id": None}]
