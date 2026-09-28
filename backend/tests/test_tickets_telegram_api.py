"""DB integration tests: POST /tickets/{id}/articles with the Telegram chat
composer options (attachments, inline buttons, quote reply), plus the Task 6
edit/retract/chat-info/typing endpoints.

Uses the 784xx id range (queue/group/user/ticket/chat).
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.telegram.gateway import TelegramApiError
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)

QUEUE_ID = 78400
GROUP_ID = 78430
TICKET_ID = 78470
AGENT = 78401
CHAT_ID = 78455
CUSTOMER_LOGIN = "tg-composer@example.com"
TICKET_TITLE = "Telegram composer ticket"
# Not seeded anywhere (no users/group_user rows): PermissionEngine.check finds
# no group membership for it, so it always fails a "note" permission check.
OUTSIDER = 78402
CUSTOMER_ARTICLE_ID = 78490
CUSTOMER_MESSAGE_ID = 78491
# A map row whose ticket_id deliberately does not match TICKET_ID, while its
# article row physically lives under TICKET_ID -- edit/retract must not trust
# the article_id path param and reach across tickets.
FOREIGN_TICKET_ID = 78472
FOREIGN_ARTICLE_ID = 78493
FOREIGN_MESSAGE_ID = 78494
# Merged into TICKET_ID by the merge test (a real second ticket, same queue).
MERGE_TICKET_ID = 78473
MERGE_ARTICLE_ID = 78495
MERGE_MESSAGE_ID = 78496


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _cleanup(conn: Any) -> None:
    for tid in (TICKET_ID, MERGE_TICKET_ID):
        _cleanup_ticket(conn, tid)
    for sql in (
        f"DELETE FROM queue WHERE id = {QUEUE_ID}",
        f"DELETE FROM group_user WHERE user_id = {AGENT} OR group_id = {GROUP_ID}",
        f"DELETE FROM permission_groups WHERE id = {GROUP_ID}",
        f"DELETE FROM users WHERE id = {AGENT}",
        f"DELETE FROM tiqora_telegram_contact WHERE chat_id = {CHAT_ID}",
        "DELETE FROM tiqora_settings WHERE `key` LIKE 'channel.telegram.%'",
    ):
        conn.execute(text(sql))


def _cleanup_ticket(conn: Any, ticket_id: int) -> None:
    arts = f"(SELECT id FROM article WHERE ticket_id = {ticket_id})"
    for sql in (
        f"DELETE FROM tiqora_telegram_message WHERE ticket_id = {ticket_id}",
        # A map row's ticket_id can deliberately differ from its article's
        # real ticket_id (see FOREIGN_TICKET_ID) -- clean those up by article
        # too, or they'd survive the ticket_id-scoped delete above.
        f"DELETE FROM tiqora_telegram_message WHERE article_id IN {arts}",
        f"DELETE FROM ticket_history WHERE ticket_id = {ticket_id}",
        f"DELETE FROM article_data_mime_attachment WHERE article_id IN {arts}",
        f"DELETE FROM article_data_mime WHERE article_id IN {arts}",
        f"DELETE FROM article_data_mime_plain WHERE article_id IN {arts}",
        f"DELETE FROM article_flag WHERE article_id IN {arts}",
        f"DELETE FROM article_search_index WHERE ticket_id = {ticket_id}",
        f"DELETE FROM article WHERE ticket_id = {ticket_id}",
        f"DELETE FROM ticket_flag WHERE ticket_id = {ticket_id}",
        f"DELETE FROM ticket_index WHERE ticket_id = {ticket_id}",
        f"DELETE FROM ticket_lock_index WHERE ticket_id = {ticket_id}",
        f"DELETE FROM tiqora_event_outbox WHERE ticket_id = {ticket_id}",
        f"DELETE FROM tiqora_cache_invalidation WHERE ticket_id = {ticket_id}",
        f"DELETE FROM ticket WHERE id = {ticket_id}",
    ):
        conn.execute(text(sql))


def _seed(sync_url: str) -> bool:
    """Seed agent/queue/ticket plus a linked Telegram contact. Returns whether
    the ``Telegram`` communication_channel row was created here (and so must
    be removed again on teardown)."""
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _cleanup(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, 'tgcomposer.agent', 'x', 'Tg', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"t": NOW, "uid": AGENT},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:gid, 'tg-composer-grp', 1, :t, 1, :t, 1)"
            ),
            {"gid": GROUP_ID, "t": NOW},
        )
        for key in ("ro", "rw", "create", "note"):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, :gid, :k, :t, 1, :t, 1)"
                ),
                {"uid": AGENT, "gid": GROUP_ID, "k": key, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:qid, 'TgComposerQueue', :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"qid": QUEUE_ID, "gid": GROUP_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:tid, '20240601784701', :title, :qid, 1, 1,"
                " :uid, 1, 3, 4, 'CUST1', :cu,"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {
                "tid": TICKET_ID,
                "title": TICKET_TITLE,
                "qid": QUEUE_ID,
                "uid": AGENT,
                "cu": CUSTOMER_LOGIN,
                "t": NOW,
            },
        )
        conn.execute(
            text(
                "INSERT INTO tiqora_telegram_contact (chat_id, customer_user_login,"
                " create_time, change_time) VALUES (:chat, :cu, :t, :t)"
            ),
            {"chat": CHAT_ID, "cu": CUSTOMER_LOGIN, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO tiqora_settings (`key`, value) VALUES"
                " ('channel.telegram.enabled', '1')"
            )
        )
        has_channel = conn.execute(
            text("SELECT id FROM communication_channel WHERE name = 'Telegram'")
        ).first()
        if has_channel is None:
            conn.execute(
                text(
                    "INSERT INTO communication_channel"
                    " (name, module, package_name, channel_data, valid_id,"
                    "  create_time, create_by, change_time, change_by)"
                    " SELECT 'Telegram', 'Tiqora::CommunicationChannel::Telegram', 'Tiqora',"
                    " channel_data, 1, :t, 1, :t, 1 FROM communication_channel"
                    " WHERE name = 'Internal'"
                ),
                {"t": NOW},
            )
    engine.dispose()
    return has_channel is None


def _teardown(sync_url: str, *, drop_channel: bool) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        _cleanup(conn)
        if drop_channel:
            conn.execute(text("DELETE FROM communication_channel WHERE name = 'Telegram'"))
    engine.dispose()


class _FakeGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # Method names that should raise TelegramApiError instead of
        # succeeding, for testing the 409 paths (edit/retract).
        self.fail_methods: set[str] = set()
        # delete_message only: message_id -> error text, for tests that need
        # one specific part of a multi-part retract to fail (the others must
        # still succeed).
        self.fail_message_ids: dict[int, str] = {}
        # edit_message_reply_markup only: Telegram's refusal text to raise.
        self.reply_markup_error: str | None = None

    def _ok(self, method: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((method, kwargs))
        if method in self.fail_methods:
            raise TelegramApiError("simulated Telegram failure")
        return {"message_id": 5000 + len(self.calls)}

    async def send_message(self, chat_id: int | str, text_body: str, **kwargs: Any) -> dict:
        return self._ok("send_message", chat_id=chat_id, text=text_body, **kwargs)

    async def send_photo(
        self, chat_id: int | str, content: bytes, filename: str, **kwargs: Any
    ) -> dict:
        return self._ok("send_photo", chat_id=chat_id, filename=filename, **kwargs)

    async def send_document(
        self, chat_id: int | str, content: bytes, filename: str, content_type: str, **kwargs: Any
    ) -> dict:
        return self._ok("send_document", chat_id=chat_id, filename=filename, **kwargs)

    async def delete_message(self, chat_id: int | str, message_id: int) -> None:
        if message_id in self.fail_message_ids:
            self.calls.append(("delete_message", {"chat_id": chat_id, "message_id": message_id}))
            raise TelegramApiError(self.fail_message_ids[message_id])
        self._ok("delete_message", chat_id=chat_id, message_id=message_id)

    async def edit_message_text(
        self, chat_id: int | str, message_id: int, text_body: str, **kwargs: Any
    ) -> None:
        self._ok(
            "edit_message_text", chat_id=chat_id, message_id=message_id, text=text_body, **kwargs
        )

    async def send_chat_action(self, chat_id: int | str, action: str = "typing") -> None:
        self._ok("send_chat_action", chat_id=chat_id, action=action)

    async def edit_message_reply_markup(
        self, chat_id: int | str, message_id: int, reply_markup: dict[str, Any] | None
    ) -> None:
        if self.reply_markup_error is not None:
            self.calls.append(("edit_message_reply_markup", {"message_id": message_id}))
            raise TelegramApiError(self.reply_markup_error)
        self._ok(
            "edit_message_reply_markup",
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
        )


@pytest.fixture
async def seeded(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[str, async_sessionmaker[AsyncSession], _FakeGateway]]:
    drop_channel = _seed(mariadb_znuny_url)
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    from tiqora.api.v1 import tickets as tickets_api

    monkeypatch.setattr(tickets_api, "get_session_factory", lambda *a, **kw: factory)
    gateway = _FakeGateway()

    async def _build_gateway(_session: AsyncSession) -> _FakeGateway:
        return gateway

    monkeypatch.setattr("tiqora.channels.telegram.outbound.build_gateway", _build_gateway)
    try:
        yield mariadb_znuny_url, factory, gateway
    finally:
        await engine.dispose()
        _teardown(mariadb_znuny_url, drop_channel=drop_channel)


def _client(
    factory: async_sessionmaker[AsyncSession],
    *,
    user_id: int = AGENT,
    login: str = "tgcomposer.agent",
) -> Any:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    fake_user = AuthenticatedUser(
        id=user_id,
        login=login,
        first_name="Tg",
        last_name="Agent",
        auth_method="session",
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = _override_get_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _article_rows(sync_url: str) -> list[tuple[int, str, str]]:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT a.id, m.a_subject, m.a_body FROM article a"
                " JOIN article_data_mime m ON m.article_id = a.id"
                " WHERE a.ticket_id = :t ORDER BY a.id"
            ),
            {"t": TICKET_ID},
        ).all()
    engine.dispose()
    return [(int(r[0]), str(r[1]), str(r[2])) for r in rows]


def _reply(**extra: Any) -> dict[str, Any]:
    return {
        "subject": "",
        "body": "Hallo!",
        "channel": "telegram",
        "is_visible_for_customer": True,
        **extra,
    }


async def test_attachments_on_email_reply_are_422(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    url, factory, gateway = seeded
    async with _client(factory) as client:
        resp = await client.post(
            f"/api/v1/tickets/{TICKET_ID}/articles",
            json=_reply(
                channel="email",
                subject="Re",
                attachments=[{"filename": "a.txt", "content_base64": _b64(b"a")}],
            ),
        )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == (
        "attachments/buttons/quotes are only supported for Telegram replies"
    )
    assert _article_rows(url) == []
    assert gateway.calls == []


@pytest.mark.parametrize(
    "extra",
    [
        {"telegram_buttons": [{"label": "Ja"}]},
        {"telegram_reply_to_article_id": 1},
    ],
)
async def test_buttons_or_quote_on_note_are_422(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway], extra: dict[str, Any]
) -> None:
    _url, factory, _gateway = seeded
    async with _client(factory) as client:
        resp = await client.post(
            f"/api/v1/tickets/{TICKET_ID}/articles",
            json=_reply(channel="note", subject="n", **extra),
        )
    assert resp.status_code == 422, resp.text


async def test_bad_base64_is_422_and_nothing_sent(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    url, factory, gateway = seeded
    async with _client(factory) as client:
        resp = await client.post(
            f"/api/v1/tickets/{TICKET_ID}/articles",
            json=_reply(attachments=[{"filename": "a.bin", "content_base64": "not*base64!"}]),
        )
    assert resp.status_code == 422, resp.text
    assert _article_rows(url) == []
    assert gateway.calls == []


async def test_attachments_over_total_limit_are_422(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tiqora.api.v1 import tickets as tickets_api

    # Shrink the 50 MB cap instead of posting 50 MB through the test client.
    monkeypatch.setattr(tickets_api, "_TELEGRAM_ATTACHMENTS_MAX_BYTES", 10)
    url, factory, gateway = seeded
    async with _client(factory) as client:
        resp = await client.post(
            f"/api/v1/tickets/{TICKET_ID}/articles",
            json=_reply(
                attachments=[
                    {"filename": "a.bin", "content_base64": _b64(b"123456")},
                    {"filename": "b.bin", "content_base64": _b64(b"123456")},
                ]
            ),
        )
    assert resp.status_code == 422, resp.text
    assert _article_rows(url) == []
    assert gateway.calls == []


async def test_telegram_reply_with_empty_subject_attachment_and_buttons(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    url, factory, gateway = seeded
    async with _client(factory) as client:
        resp = await client.post(
            f"/api/v1/tickets/{TICKET_ID}/articles",
            json=_reply(
                attachments=[
                    {
                        "filename": "info.pdf",
                        "content_type": "application/pdf",
                        "content_base64": _b64(b"%PDF-1.4"),
                    }
                ],
                telegram_buttons=[
                    {"label": "Ja, geht wieder", "action": "resolve_yes"},
                    {"label": "Rückfrage"},
                ],
            ),
        )
    assert resp.status_code == 201, resp.text
    article_id = resp.json()["article_id"]

    assert [c[0] for c in gateway.calls] == ["send_document", "send_message"]
    assert gateway.calls[0][1]["filename"] == "info.pdf"
    keyboard = gateway.calls[1][1]["reply_markup"]
    assert [row[0]["callback_data"] for row in keyboard["inline_keyboard"]] == ["tqb:0", "tqb:1"]

    assert _article_rows(url) == [(article_id, TICKET_TITLE, "Hallo!")]
    engine = create_engine(url)
    with engine.begin() as conn:
        filenames = conn.execute(
            text("SELECT filename FROM article_data_mime_attachment WHERE article_id = :a"),
            {"a": article_id},
        ).all()
        row = conn.execute(
            text(
                "SELECT chat_id, direction, buttons_json FROM tiqora_telegram_message"
                " WHERE article_id = :a"
            ),
            {"a": article_id},
        ).one()
    engine.dispose()
    assert [r[0] for r in filenames] == ["info.pdf"]
    assert int(row[0]) == CHAT_ID
    assert row[1] == "out"
    assert "resolve_yes" in row[2]


# ---------------------------------------------------------------------------
# Task 6: chat info, typing, edit, retract
# ---------------------------------------------------------------------------


async def _send_out_reply(client: Any, **extra: Any) -> int:
    resp = await client.post(
        f"/api/v1/tickets/{TICKET_ID}/articles",
        json=_reply(**extra),
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["article_id"])


def _insert_customer_article(sync_url: str) -> None:
    """A customer-sent (inbound) Telegram article + map row, outside the
    normal add_article path -- Task 6 must refuse to edit/retract it."""
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        channel_id = conn.execute(
            text("SELECT id FROM communication_channel WHERE name = 'Telegram'")
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                " communication_channel_id, is_visible_for_customer, search_index_needs_rebuild,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:aid, :tid, 3, :cid, 1, 0, :t, 1, :t, 1)"
            ),
            {"aid": CUSTOMER_ARTICLE_ID, "tid": TICKET_ID, "cid": channel_id, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO article_data_mime (id, article_id, a_from, a_subject,"
                " a_content_type, a_body, a_message_id, incoming_time,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:aid, :aid, :from_, 'Telegram', 'text/plain; charset=utf-8',"
                " 'Hallo vom Kunden', '<msg-customer@x>', 1717243200, :t, 1, :t, 1)"
            ),
            {"aid": CUSTOMER_ARTICLE_ID, "from_": f"{CHAT_ID}@telegram.invalid", "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO tiqora_telegram_message"
                " (article_id, ticket_id, chat_id, message_id, direction)"
                " VALUES (:aid, :tid, :chat, :mid, 'in')"
            ),
            {
                "aid": CUSTOMER_ARTICLE_ID,
                "tid": TICKET_ID,
                "chat": CHAT_ID,
                "mid": CUSTOMER_MESSAGE_ID,
            },
        )
    engine.dispose()


def _insert_foreign_ticket_article(sync_url: str) -> None:
    """An agent-sent Telegram article physically stored under TICKET_ID (so
    the article row itself exists), but whose ``tiqora_telegram_message`` map
    row points at a different ``ticket_id`` -- edit/retract must not trust
    the article_id path param and reach across tickets."""
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        channel_id = conn.execute(
            text("SELECT id FROM communication_channel WHERE name = 'Telegram'")
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                " communication_channel_id, is_visible_for_customer, search_index_needs_rebuild,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:aid, :tid, 1, :cid, 1, 0, :t, 1, :t, 1)"
            ),
            {"aid": FOREIGN_ARTICLE_ID, "tid": TICKET_ID, "cid": channel_id, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO article_data_mime (id, article_id, a_from, a_subject,"
                " a_content_type, a_body, a_message_id, incoming_time,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:aid, :aid, 'bot', 'Telegram', 'text/plain; charset=utf-8',"
                " 'Fremdes Ticket', '<msg-foreign@x>', 1717243200, :t, 1, :t, 1)"
            ),
            {"aid": FOREIGN_ARTICLE_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO tiqora_telegram_message"
                " (article_id, ticket_id, chat_id, message_id, direction)"
                " VALUES (:aid, :tid, :chat, :mid, 'out')"
            ),
            {
                "aid": FOREIGN_ARTICLE_ID,
                "tid": FOREIGN_TICKET_ID,
                "chat": CHAT_ID,
                "mid": FOREIGN_MESSAGE_ID,
            },
        )
    engine.dispose()


def _history_names(sync_url: str) -> list[str]:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT name FROM ticket_history WHERE ticket_id = :t ORDER BY id"),
            {"t": TICKET_ID},
        ).all()
    engine.dispose()
    return [str(r[0]) for r in rows]


async def test_telegram_chat_info_returns_contact_and_messages(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    _url, factory, gateway = seeded
    async with _client(factory) as client:
        article_id = await _send_out_reply(
            client, telegram_buttons=[{"label": "Ja", "action": "resolve_yes"}]
        )
        resp = await client.get(f"/api/v1/tickets/{TICKET_ID}/telegram")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["chat_id"] == CHAT_ID
    assert data["customer_user_login"] == CUSTOMER_LOGIN
    assert data["identity_verified"] is True
    assert data["ai_escalated_at"] is None
    messages = {m["article_id"]: m for m in data["messages"]}
    assert article_id in messages
    meta = messages[article_id]
    assert meta["direction"] == "out"
    assert meta["buttons"] == [{"label": "Ja", "action": "resolve_yes"}]
    assert meta["answered_button"] is None
    assert meta["edited_at"] is None
    assert meta["retracted_at"] is None
    assert meta["editable"] is True
    assert gateway.calls[-1][0] == "send_message"


async def test_telegram_chat_info_requires_ro_permission(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    _url, factory, _gateway = seeded
    async with _client(factory, user_id=OUTSIDER, login="outsider") as client:
        resp = await client.get(f"/api/v1/tickets/{TICKET_ID}/telegram")
    assert resp.status_code == 403, resp.text


async def test_edit_telegram_article_updates_body_and_meta(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    url, factory, gateway = seeded
    async with _client(factory) as client:
        article_id = await _send_out_reply(
            client, telegram_buttons=[{"label": "Ja", "action": "resolve_yes"}]
        )
        resp = await client.patch(
            f"/api/v1/tickets/{TICKET_ID}/articles/{article_id}/telegram",
            json={"body": "Aktualisierter Text"},
        )
    assert resp.status_code == 204, resp.text

    assert gateway.calls[-1][0] == "edit_message_text"
    edit_kwargs = gateway.calls[-1][1]
    assert edit_kwargs["text"] == "Aktualisierter Text"
    assert edit_kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "tqb:0"

    assert _article_rows(url) == [(article_id, TICKET_TITLE, "Aktualisierter Text")]
    engine = create_engine(url)
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT edited_at, original_body FROM tiqora_telegram_message WHERE article_id = :a"
            ),
            {"a": article_id},
        ).one()
    engine.dispose()
    assert row[0] is not None
    assert row[1] == "Hallo!"
    assert any(n.startswith(f"%%TelegramEdited%%{article_id}") for n in _history_names(url))

    # A second edit must not overwrite the already-stored original_body.
    async with _client(factory) as client:
        resp = await client.patch(
            f"/api/v1/tickets/{TICKET_ID}/articles/{article_id}/telegram",
            json={"body": "Noch ein Text"},
        )
    assert resp.status_code == 204, resp.text
    with engine.begin() as conn:
        row2 = conn.execute(
            text("SELECT original_body FROM tiqora_telegram_message WHERE article_id = :a"),
            {"a": article_id},
        ).one()
    assert row2[0] == "Hallo!"


async def test_edit_customer_article_is_409(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    url, factory, gateway = seeded
    _insert_customer_article(url)
    async with _client(factory) as client:
        resp = await client.patch(
            f"/api/v1/tickets/{TICKET_ID}/articles/{CUSTOMER_ARTICLE_ID}/telegram",
            json={"body": "Nope"},
        )
    assert resp.status_code == 409, resp.text
    assert gateway.calls == []


async def test_edit_unknown_article_is_404(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    _url, factory, gateway = seeded
    async with _client(factory) as client:
        resp = await client.patch(
            f"/api/v1/tickets/{TICKET_ID}/articles/999999999/telegram",
            json={"body": "Nope"},
        )
    assert resp.status_code == 404, resp.text
    assert gateway.calls == []


async def test_edit_article_from_a_different_ticket_is_404(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    url, factory, gateway = seeded
    _insert_foreign_ticket_article(url)
    async with _client(factory) as client:
        resp = await client.patch(
            f"/api/v1/tickets/{TICKET_ID}/articles/{FOREIGN_ARTICLE_ID}/telegram",
            json={"body": "Nope"},
        )
    assert resp.status_code == 404, resp.text
    assert gateway.calls == []


async def test_retract_telegram_article_success(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    url, factory, gateway = seeded
    async with _client(factory) as client:
        article_id = await _send_out_reply(client)
        resp = await client.post(
            f"/api/v1/tickets/{TICKET_ID}/articles/{article_id}/telegram/retract"
        )
    assert resp.status_code == 204, resp.text
    assert gateway.calls[-1][0] == "delete_message"

    engine = create_engine(url)
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT retracted_at, retracted_by FROM tiqora_telegram_message"
                " WHERE article_id = :a"
            ),
            {"a": article_id},
        ).one()
    engine.dispose()
    assert row[0] is not None
    assert int(row[1]) == AGENT
    assert any(n.startswith(f"%%TelegramRetracted%%{article_id}") for n in _history_names(url))


async def test_retract_telegram_article_gateway_failure_is_409_and_unchanged(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    url, factory, gateway = seeded
    async with _client(factory) as client:
        article_id = await _send_out_reply(client)
        gateway.fail_methods.add("delete_message")
        resp = await client.post(
            f"/api/v1/tickets/{TICKET_ID}/articles/{article_id}/telegram/retract"
        )
    assert resp.status_code == 409, resp.text
    assert _article_rows(url) == [(article_id, TICKET_TITLE, "Hallo!")]

    engine = create_engine(url)
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT retracted_at FROM tiqora_telegram_message WHERE article_id = :a"),
            {"a": article_id},
        ).one()
    engine.dispose()
    assert row[0] is None


async def test_retract_telegram_article_partial_failure_still_retracts(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    """message_id + 2 extra (attachment) ids; the second delete overall
    refuses with a real (non-"not found") error. All three deletes must
    still be attempted, and -- because the first one already succeeded --
    the retract must still go through (204, retracted) rather than getting
    permanently stuck on a half-deleted chat."""
    url, factory, gateway = seeded
    async with _client(factory) as client:
        article_id = await _send_out_reply(
            client,
            attachments=[
                {
                    "filename": "a.pdf",
                    "content_type": "application/pdf",
                    "content_base64": _b64(b"%PDF-1"),
                },
                {
                    "filename": "b.pdf",
                    "content_type": "application/pdf",
                    "content_base64": _b64(b"%PDF-2"),
                },
            ],
        )
    engine = create_engine(url)
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT message_id, extra_message_ids FROM tiqora_telegram_message"
                " WHERE article_id = :a"
            ),
            {"a": article_id},
        ).one()
    engine.dispose()
    text_message_id = int(row[0])
    extra_ids = [int(i) for i in json.loads(row[1])]
    assert len(extra_ids) == 2

    gateway.fail_message_ids[extra_ids[0]] = "Forbidden: bot was blocked by the user"
    async with _client(factory) as client:
        resp = await client.post(
            f"/api/v1/tickets/{TICKET_ID}/articles/{article_id}/telegram/retract"
        )
    assert resp.status_code == 204, resp.text

    delete_calls = [c[1] for c in gateway.calls if c[0] == "delete_message"]
    assert [c["message_id"] for c in delete_calls] == [text_message_id, *extra_ids]

    engine = create_engine(url)
    with engine.begin() as conn:
        retracted = conn.execute(
            text(
                "SELECT retracted_at, retracted_by FROM tiqora_telegram_message"
                " WHERE article_id = :a"
            ),
            {"a": article_id},
        ).one()
    engine.dispose()
    assert retracted[0] is not None
    assert int(retracted[1]) == AGENT


async def test_retract_telegram_article_not_found_part_is_treated_as_gone(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    """Telegram refusing a delete with "message to delete not found" (already deleted, by
    us on an earlier attempt or by the customer) must count as success, not
    as a conflict -- otherwise a retry after a partial failure could never
    complete."""
    url, factory, gateway = seeded
    async with _client(factory) as client:
        article_id = await _send_out_reply(client)
    engine = create_engine(url)
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT message_id FROM tiqora_telegram_message WHERE article_id = :a"),
            {"a": article_id},
        ).one()
    engine.dispose()
    message_id = int(row[0])
    gateway.fail_message_ids[message_id] = "Bad Request: message to delete not found"

    async with _client(factory) as client:
        resp = await client.post(
            f"/api/v1/tickets/{TICKET_ID}/articles/{article_id}/telegram/retract"
        )
    assert resp.status_code == 204, resp.text

    engine = create_engine(url)
    with engine.begin() as conn:
        retracted = conn.execute(
            text("SELECT retracted_at FROM tiqora_telegram_message WHERE article_id = :a"),
            {"a": article_id},
        ).one()
    engine.dispose()
    assert retracted[0] is not None


async def test_telegram_typing_sends_chat_action(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    _url, factory, gateway = seeded
    async with _client(factory) as client:
        resp = await client.post(f"/api/v1/tickets/{TICKET_ID}/telegram/typing")
    assert resp.status_code == 204, resp.text
    assert [c[0] for c in gateway.calls] == ["send_chat_action"]
    assert gateway.calls[0][1] == {"chat_id": CHAT_ID, "action": "typing"}


async def test_telegram_typing_requires_note_permission(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    _url, factory, gateway = seeded
    async with _client(factory, user_id=OUTSIDER, login="outsider") as client:
        resp = await client.post(f"/api/v1/tickets/{TICKET_ID}/telegram/typing")
    assert resp.status_code == 403, resp.text
    assert gateway.calls == []


async def test_attachment_only_reply_is_not_editable(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    """Without text the map row points at the attachment itself, which
    editMessageText can't change -- the meta says so and the edit is 409."""
    _url, factory, gateway = seeded
    async with _client(factory) as client:
        article_id = await _send_out_reply(
            client,
            body="",
            attachments=[
                {
                    "filename": "a.pdf",
                    "content_type": "application/pdf",
                    "content_base64": _b64(b"%PDF-1"),
                }
            ],
        )
        info = await client.get(f"/api/v1/tickets/{TICKET_ID}/telegram")
        calls_before = len(gateway.calls)
        resp = await client.patch(
            f"/api/v1/tickets/{TICKET_ID}/articles/{article_id}/telegram",
            json={"body": "Doch mit Text"},
        )
    assert info.status_code == 200, info.text
    meta = {m["article_id"]: m for m in info.json()["messages"]}[article_id]
    assert meta["editable"] is False
    assert resp.status_code == 409, resp.text
    assert "Anhang" in resp.json()["detail"]
    assert len(gateway.calls) == calls_before


async def test_inbound_message_is_not_editable(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    url, factory, _gateway = seeded
    _insert_customer_article(url)
    async with _client(factory) as client:
        info = await client.get(f"/api/v1/tickets/{TICKET_ID}/telegram")
    assert info.status_code == 200, info.text
    meta = {m["article_id"]: m for m in info.json()["messages"]}[CUSTOMER_ARTICLE_ID]
    assert meta["editable"] is False


@pytest.mark.parametrize("action", ["edit", "retract"])
async def test_edit_and_retract_without_bot_token_are_409(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    from tiqora.channels.telegram.outbound import TelegramDeliveryError

    _url, factory, _gateway = seeded
    async with _client(factory) as client:
        article_id = await _send_out_reply(client)

        async def _no_gateway(_session: AsyncSession) -> Any:
            raise TelegramDeliveryError("Telegram channel has no bot_token configured")

        monkeypatch.setattr("tiqora.channels.telegram.outbound.build_gateway", _no_gateway)
        path = f"/api/v1/tickets/{TICKET_ID}/articles/{article_id}/telegram"
        if action == "edit":
            resp = await client.patch(path, json={"body": "Neu"})
        else:
            resp = await client.post(f"{path}/retract")
    assert resp.status_code == 409, resp.text
    assert "bot_token" in resp.json()["detail"]


async def test_retract_chat_not_found_is_a_conflict_not_already_gone(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    """Only "message to delete not found" means the message is gone; "chat
    not found" is a real refusal and must not mark the article retracted."""
    url, factory, gateway = seeded
    async with _client(factory) as client:
        article_id = await _send_out_reply(client)
    engine = create_engine(url)
    with engine.begin() as conn:
        message_id = int(
            conn.execute(
                text("SELECT message_id FROM tiqora_telegram_message WHERE article_id = :a"),
                {"a": article_id},
            ).scalar_one()
        )
    gateway.fail_message_ids[message_id] = "Bad Request: chat not found"
    async with _client(factory) as client:
        resp = await client.post(
            f"/api/v1/tickets/{TICKET_ID}/articles/{article_id}/telegram/retract"
        )
    assert resp.status_code == 409, resp.text
    with engine.begin() as conn:
        retracted = conn.execute(
            text("SELECT retracted_at FROM tiqora_telegram_message WHERE article_id = :a"),
            {"a": article_id},
        ).scalar()
    engine.dispose()
    assert retracted is None


def _insert_merge_ticket_with_message(sync_url: str) -> None:
    """A second real ticket in the same queue with one agent Telegram
    article and its map row -- to be merged into TICKET_ID."""
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:tid, '20240601784731', 'Merge me', :qid, 1, 1,"
                " :uid, 1, 3, 4, 'CUST1', :cu,"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {
                "tid": MERGE_TICKET_ID,
                "qid": QUEUE_ID,
                "uid": AGENT,
                "cu": CUSTOMER_LOGIN,
                "t": NOW,
            },
        )
        channel_id = conn.execute(
            text("SELECT id FROM communication_channel WHERE name = 'Telegram'")
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                " communication_channel_id, is_visible_for_customer, search_index_needs_rebuild,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:aid, :tid, 1, :cid, 1, 0, :t, 1, :t, 1)"
            ),
            {"aid": MERGE_ARTICLE_ID, "tid": MERGE_TICKET_ID, "cid": channel_id, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO article_data_mime (id, article_id, a_from, a_subject,"
                " a_content_type, a_body, a_message_id, incoming_time,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:aid, :aid, 'bot', 'Telegram', 'text/plain; charset=utf-8',"
                " 'Vor dem Merge', '<msg-merge@x>', 1717243200, :t, 1, :t, 1)"
            ),
            {"aid": MERGE_ARTICLE_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO tiqora_telegram_message"
                " (article_id, ticket_id, chat_id, message_id, direction)"
                " VALUES (:aid, :tid, :chat, :mid, 'out')"
            ),
            {
                "aid": MERGE_ARTICLE_ID,
                "tid": MERGE_TICKET_ID,
                "chat": CHAT_ID,
                "mid": MERGE_MESSAGE_ID,
            },
        )
    engine.dispose()


async def test_merge_moves_telegram_map_rows_to_main_ticket(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    """Merged articles keep their Telegram meta: the map rows follow the
    articles to the main ticket (else edit/retract/quote there 404)."""
    from tiqora.domain.ticket_write_service import merge_tickets
    from tiqora.znuny.sysconfig import SysConfig

    url, factory, _gateway = seeded
    _insert_merge_ticket_with_message(url)
    async with factory() as session, session.begin():
        await merge_tickets(
            session,
            main_ticket_id=TICKET_ID,
            merge_ticket_id=MERGE_TICKET_ID,
            user_id=AGENT,
            sysconfig=SysConfig(session),
        )

    engine = create_engine(url)
    with engine.begin() as conn:
        moved = conn.execute(
            text("SELECT ticket_id FROM tiqora_telegram_message WHERE article_id = :a"),
            {"a": MERGE_ARTICLE_ID},
        ).scalar_one()
    engine.dispose()
    assert int(moved) == TICKET_ID

    async with _client(factory) as client:
        info = await client.get(f"/api/v1/tickets/{TICKET_ID}/telegram")
    assert info.status_code == 200, info.text
    assert MERGE_ARTICLE_ID in {m["article_id"] for m in info.json()["messages"]}


# ---------------------------------------------------------------------------
# Removing the inline keyboard from a sent message
# ---------------------------------------------------------------------------

_YES_NO = [{"label": "Ja", "action": "resolve_yes"}, {"label": "Nein", "action": "resolve_no"}]


def _map_row(sync_url: str, article_id: int) -> tuple[int, str | None]:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT message_id, buttons_json FROM tiqora_telegram_message WHERE article_id = :a"
            ),
            {"a": article_id},
        ).one()
    engine.dispose()
    return int(row[0]), row[1]


def _buttons_path(article_id: int) -> str:
    return f"/api/v1/tickets/{TICKET_ID}/articles/{article_id}/telegram/buttons"


async def test_remove_buttons_clears_keyboard_and_meta(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    url, factory, gateway = seeded
    async with _client(factory) as client:
        article_id = await _send_out_reply(client, telegram_buttons=_YES_NO)
        message_id, _ = _map_row(url, article_id)
        resp = await client.delete(_buttons_path(article_id))
        info = await client.get(f"/api/v1/tickets/{TICKET_ID}/telegram")
    assert resp.status_code == 204, resp.text
    assert gateway.calls[-1] == (
        "edit_message_reply_markup",
        {"chat_id": CHAT_ID, "message_id": message_id, "reply_markup": None},
    )
    assert _map_row(url, article_id)[1] is None
    meta = {m["article_id"]: m for m in info.json()["messages"]}[article_id]
    assert meta["buttons"] == []
    assert meta["retracted_at"] is None
    assert any(n.startswith(f"%%TelegramButtonsRemoved%%{article_id}") for n in _history_names(url))


async def test_remove_buttons_without_buttons_is_409(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    _url, factory, gateway = seeded
    async with _client(factory) as client:
        article_id = await _send_out_reply(client)
        calls_before = len(gateway.calls)
        resp = await client.delete(_buttons_path(article_id))
    assert resp.status_code == 409, resp.text
    assert "keine Buttons" in resp.json()["detail"]
    assert len(gateway.calls) == calls_before


async def test_remove_buttons_after_answer_is_409(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    url, factory, gateway = seeded
    async with _client(factory) as client:
        article_id = await _send_out_reply(client, telegram_buttons=_YES_NO)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE tiqora_telegram_message SET answered_button = 0,"
                " answered_at = current_timestamp WHERE article_id = :a"
            ),
            {"a": article_id},
        )
    engine.dispose()
    calls_before = len(gateway.calls)
    async with _client(factory) as client:
        resp = await client.delete(_buttons_path(article_id))
    assert resp.status_code == 409, resp.text
    assert len(gateway.calls) == calls_before
    assert _map_row(url, article_id)[1] is not None


async def test_remove_buttons_on_customer_article_is_409(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    url, factory, gateway = seeded
    _insert_customer_article(url)
    async with _client(factory) as client:
        resp = await client.delete(_buttons_path(CUSTOMER_ARTICLE_ID))
    assert resp.status_code == 409, resp.text
    assert gateway.calls == []


async def test_remove_buttons_on_foreign_ticket_article_is_404(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    url, factory, gateway = seeded
    _insert_foreign_ticket_article(url)
    async with _client(factory) as client:
        resp = await client.delete(_buttons_path(FOREIGN_ARTICLE_ID))
    assert resp.status_code == 404, resp.text
    assert gateway.calls == []


async def test_remove_buttons_requires_note_permission(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    url, factory, _gateway = seeded
    async with _client(factory) as client:
        article_id = await _send_out_reply(client, telegram_buttons=_YES_NO)
    async with _client(factory, user_id=OUTSIDER, login="outsider") as client:
        resp = await client.delete(_buttons_path(article_id))
    assert resp.status_code == 403, resp.text
    assert _map_row(url, article_id)[1] is not None


async def test_remove_buttons_refused_by_telegram_is_409_and_unchanged(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    url, factory, gateway = seeded
    async with _client(factory) as client:
        article_id = await _send_out_reply(client, telegram_buttons=_YES_NO)
        gateway.reply_markup_error = "Bad Request: chat not found"
        resp = await client.delete(_buttons_path(article_id))
    assert resp.status_code == 409, resp.text
    assert "chat not found" in resp.json()["detail"]
    assert _map_row(url, article_id)[1] is not None
    assert not any(n.startswith("%%TelegramButtonsRemoved%%") for n in _history_names(url))


async def test_remove_buttons_already_gone_counts_as_success(
    seeded: tuple[str, async_sessionmaker[AsyncSession], _FakeGateway],
) -> None:
    """Keyboard already empty at Telegram ("message is not modified") -- the
    goal is reached, so the stored buttons are cleared anyway."""
    url, factory, gateway = seeded
    async with _client(factory) as client:
        article_id = await _send_out_reply(client, telegram_buttons=_YES_NO)
        gateway.reply_markup_error = (
            "Bad Request: message is not modified: specified new message content"
            " and reply markup are exactly the same"
        )
        resp = await client.delete(_buttons_path(article_id))
    assert resp.status_code == 204, resp.text
    assert _map_row(url, article_id)[1] is None
