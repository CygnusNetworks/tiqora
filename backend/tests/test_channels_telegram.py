"""Tests for the Telegram channel: gateway (httpx.MockTransport, token
scrubbing) and DB-backed inbound processing (ticket/article creation,
per-chat ticket continuity, contact upsert), plus the two Task 3 transports
(long-poll daemon tick, webhook route)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import httpx
import pytest
from fastapi import HTTPException, Request
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.v1 import channels_telegram
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.channels.telegram.gateway import TelegramApiError, TelegramGateway
from tiqora.channels.telegram.outbound import TelegramDeliveryError, deliver_agent_telegram_reply
from tiqora.channels.telegram.service import process_update
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraTelegramContact
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.settings_store import KEY_TELEGRAM_UPDATE_OFFSET, get_setting, set_setting
from tiqora.domain.ticket_write_service import ArticleIn, TicketWriteService
from tiqora.worker.telegram_poller import run_telegram_poller_tick
from tiqora.znuny.password import hash_password
from tiqora.znuny.sysconfig import SysConfig

NOW = datetime(2026, 1, 1, 12, 0, 0)


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _ensure_tiqora_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()


# Every table process_update can write to, children-before-parents so a
# straight id-range DELETE never trips an FK. Tests snapshot MAX(id) per
# table before acting and delete everything newer afterwards -- cheaper and
# more robust than tracking each individual row, and (unlike the sibling
# sms/whatsapp/phone channel test files, which are grandfathered leakers)
# keeps this module out of tests/db_leak_baseline.txt.
_WRITE_TABLES = (
    "article_data_mime_attachment",
    "article_data_mime",
    "ticket_history",
    "article",
    "tiqora_cache_invalidation",
    "tiqora_event_outbox",
    "ticket_number_counter",
    "ticket",
    "tiqora_telegram_contact",
    "communication_channel",
)


async def _snapshot_max_ids(session: AsyncSession) -> dict[str, int]:
    return {
        table: int(
            (await session.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {table}"))).scalar() or 0
        )
        for table in _WRITE_TABLES
    }


async def _cleanup_new_rows(session: AsyncSession, before: dict[str, int]) -> None:
    for table in _WRITE_TABLES:
        await session.execute(text(f"DELETE FROM {table} WHERE id > :b"), {"b": before[table]})
    # tiqora_settings is keyed on `key` (a reserved word -- must be quoted),
    # not an autoincrement id.
    await session.execute(
        text(
            "DELETE FROM tiqora_settings WHERE `key` LIKE 'channel.telegram.%'"
            " OR `key` LIKE 'daemon.telegram_poller.%'"
        )
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Gateway (unit, MockTransport)
# ---------------------------------------------------------------------------


async def test_gateway_send_message_plain_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/bottest-token/sendMessage" in str(request.url)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gw = TelegramGateway(bot_token="test-token", client=client)
    result = await gw.send_message(123, "hi")
    await client.aclose()
    assert result["message_id"] == 42


async def test_gateway_download_file_two_step() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "/getFile" in str(request.url):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "photos/f.jpg"}})
        return httpx.Response(200, content=b"binarydata")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gw = TelegramGateway(bot_token="test-token", client=client)
    content, mime_type = await gw.download_file("file-id-1")
    await client.aclose()
    assert content == b"binarydata"
    assert mime_type == "image/jpeg"
    assert len(calls) == 2


async def test_gateway_error_scrubs_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "Unauthorized"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gw = TelegramGateway(bot_token="super-secret-token", client=client)
    with pytest.raises(TelegramApiError) as exc_info:
        await gw.send_message(1, "x")
    await client.aclose()
    assert "super-secret-token" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# DB-backed inbound processing
# ---------------------------------------------------------------------------


def _text_message(
    chat_id: int, text_body: str, *, user_id: int = 900, is_bot: bool = False
) -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "date": 1700000000,
            "chat": {"id": chat_id, "type": "private"},
            "from": {
                "id": user_id,
                "is_bot": is_bot,
                "first_name": "Ada",
                "last_name": "Lovelace",
                "username": "ada",
            },
            "text": text_body,
        },
    }


@pytest.mark.db
async def test_inbound_text_creates_ticket(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await set_setting(
                    session, "channel.telegram.default_customer_user", "portal-default"
                )

                sysconfig = SysConfig(session)
                update = _text_message(111, "Need help with my order")
                result = await process_update(session, factory, sysconfig, None, update, user_id=1)
                await session.commit()

                assert "ticket_id" in result
                assert result["created_ticket"] is True

                row = (
                    await session.execute(
                        text(
                            "SELECT a_body, a_from FROM article_data_mime WHERE article_id = :aid"
                        ),
                        {"aid": result["article_id"]},
                    )
                ).first()
                assert row is not None
                assert row[0] == "Need help with my order"
                assert row[1] == "Ada Lovelace <111@telegram.invalid>"

                ch_row = (
                    await session.execute(
                        text("SELECT name FROM communication_channel WHERE name = 'Telegram'")
                    )
                ).first()
                assert ch_row is not None

                sender_row = (
                    await session.execute(
                        text(
                            "SELECT ast.name FROM article a"
                            " JOIN article_sender_type ast ON ast.id = a.article_sender_type_id"
                            " WHERE a.id = :aid"
                        ),
                        {"aid": result["article_id"]},
                    )
                ).first()
                assert sender_row is not None
                assert sender_row[0] == "customer"
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_second_update_same_chat_appends_to_same_ticket(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await set_setting(
                    session, "channel.telegram.default_customer_user", "portal-default"
                )
                sysconfig = SysConfig(session)

                first = await process_update(
                    session,
                    factory,
                    sysconfig,
                    None,
                    _text_message(222, "First message"),
                    user_id=1,
                )
                await session.commit()

                second = await process_update(
                    session,
                    factory,
                    sysconfig,
                    None,
                    _text_message(222, "Second message"),
                    user_id=1,
                )
                await session.commit()

                assert first["created_ticket"] is True
                assert second["created_ticket"] is False
                assert second["ticket_id"] == first["ticket_id"]
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_two_unknown_chat_ids_create_two_tickets(mariadb_znuny_url: str) -> None:
    """Regression guard: two different Telegram users both falling back to the
    same default_customer_user must NOT be merged into one ticket."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await set_setting(
                    session, "channel.telegram.default_customer_user", "portal-default"
                )
                sysconfig = SysConfig(session)

                first = await process_update(
                    session,
                    factory,
                    sysconfig,
                    None,
                    _text_message(333, "Hi from user A"),
                    user_id=1,
                )
                await session.commit()

                second = await process_update(
                    session,
                    factory,
                    sysconfig,
                    None,
                    _text_message(444, "Hi from user B"),
                    user_id=1,
                )
                await session.commit()

                assert first["created_ticket"] is True
                assert second["created_ticket"] is True
                assert first["ticket_id"] != second["ticket_id"]
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_is_bot_and_edited_message_are_skipped(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await set_setting(
                    session, "channel.telegram.default_customer_user", "portal-default"
                )
                sysconfig = SysConfig(session)

                bot_result = await process_update(
                    session,
                    factory,
                    sysconfig,
                    None,
                    _text_message(555, "beep boop", is_bot=True),
                    user_id=1,
                )
                assert bot_result == {"skipped": "bot"}

                edited_update = {
                    "update_id": 2,
                    "edited_message": _text_message(555, "edited text")["message"],
                }
                edited_result = await process_update(
                    session, factory, sysconfig, None, edited_update, user_id=1
                )
                assert edited_result == {"skipped": "unsupported"}
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_no_customer_mapping_skips(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                # Each test cleans up its own settings row, but pin this
                # explicitly rather than relying on test execution order.
                await set_setting(session, "channel.telegram.default_customer_user", "")
                sysconfig = SysConfig(session)
                result = await process_update(
                    session, factory, sysconfig, None, _text_message(666, "hello"), user_id=1
                )
                assert result == {"skipped": "no_customer"}
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_contact_upsert_updates_display_name_keeps_login(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                session.add(
                    TiqoraTelegramContact(
                        chat_id=777,
                        telegram_user_id=900,
                        username="ada",
                        display_name="Old Name",
                        customer_user_login="realcustomer",
                    )
                )
                await session.commit()

                sysconfig = SysConfig(session)
                await process_update(
                    session, factory, sysconfig, None, _text_message(777, "hi again"), user_id=1
                )
                await session.commit()

                row = (
                    await session.execute(
                        select(TiqoraTelegramContact).where(TiqoraTelegramContact.chat_id == 777)
                    )
                ).scalar_one()
                assert row.display_name == "Ada Lovelace"
                assert row.customer_user_login == "realcustomer"
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Poller daemon tick (Task 3)
# ---------------------------------------------------------------------------


class _UnusedGateway:
    """Fails the test if the poller ever reaches out to Telegram — used for
    the gating-matrix cases, where a blocked gate must return before any
    gateway call."""

    async def get_updates(self, **_kwargs: Any) -> list[dict]:
        raise AssertionError("gateway.get_updates must not be called when a gate blocks the tick")


def _telegram_update(update_id: int, chat_id: int, text_body: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1700000000,
            "chat": {"id": chat_id, "type": "private"},
            "from": {
                "id": chat_id + 10_000,
                "is_bot": False,
                "first_name": "Ada",
                "last_name": "Lovelace",
                "username": "ada",
            },
            "text": text_body,
        },
    }


def _updates_gateway(updates: list[dict]) -> TelegramGateway:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/getUpdates" in str(request.url)
        return httpx.Response(200, json={"ok": True, "result": updates})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TelegramGateway(bot_token="test-token", client=client)


@pytest.mark.db
@pytest.mark.parametrize(
    ("enabled", "channel_enabled_val", "mode", "token", "expected"),
    [
        ("0", "1", "polling", "tok", {"enabled": 0}),
        ("1", "0", "polling", "tok", {"channel_disabled": 1}),
        ("1", "1", "webhook", "tok", {"skipped_mode_webhook": 1}),
        ("1", "1", "polling", "", {"no_token": 1}),
    ],
)
async def test_poller_tick_gating_matrix(
    mariadb_znuny_url: str,
    enabled: str,
    channel_enabled_val: str,
    mode: str,
    token: str,
    expected: dict,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await set_setting(session, "daemon.telegram_poller.enabled", enabled)
                await set_setting(session, "channel.telegram.enabled", channel_enabled_val)
                await set_setting(session, "channel.telegram.mode", mode)
                if token:
                    await set_setting(session, "channel.telegram.bot_token", token)

                result = await run_telegram_poller_tick(
                    session_factory=factory, gateway=_UnusedGateway()
                )
                assert result == expected
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_poller_tick_processes_updates_and_advances_offset(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await set_setting(session, "daemon.telegram_poller.enabled", "1")
                await set_setting(session, "channel.telegram.enabled", "1")
                await set_setting(session, "channel.telegram.mode", "polling")
                await set_setting(session, "channel.telegram.bot_token", "test-token")
                await set_setting(
                    session, "channel.telegram.default_customer_user", "portal-default"
                )

                updates = [
                    _telegram_update(101, 9101, "First"),
                    _telegram_update(102, 9102, "Second"),
                ]
                result = await run_telegram_poller_tick(
                    session_factory=factory, gateway=_updates_gateway(updates)
                )
                assert result == {
                    "updates": 2,
                    "articles": 2,
                    "tickets_created": 2,
                    "skipped": 0,
                }

                offset = await get_setting(session, KEY_TELEGRAM_UPDATE_OFFSET)
                assert offset == "103"
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_poller_tick_error_stops_offset_advance_and_next_tick_retries(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await set_setting(session, "daemon.telegram_poller.enabled", "1")
                await set_setting(session, "channel.telegram.enabled", "1")
                await set_setting(session, "channel.telegram.mode", "polling")
                await set_setting(session, "channel.telegram.bot_token", "test-token")
                await set_setting(
                    session, "channel.telegram.default_customer_user", "portal-default"
                )

                good = _telegram_update(201, 9201, "Good")
                # A message with no "chat" key breaks _upsert_contact (KeyError),
                # simulating a per-update failure the tick must not paper over.
                broken = {"update_id": 202, "message": {"text": "boom"}}

                result = await run_telegram_poller_tick(
                    session_factory=factory, gateway=_updates_gateway([good, broken])
                )
                assert result["articles"] == 1
                # The poller tick committed its own writes on a *different*
                # session; end this session's REPEATABLE READ snapshot so the
                # next read observes them instead of a stale pre-tick view.
                await session.commit()
                offset = await get_setting(session, KEY_TELEGRAM_UPDATE_OFFSET)
                assert offset == "202"  # advanced past 201 only, not past the broken 202

                # Next tick: the same broken update_id, now fixed, must be
                # reprocessed (offset did not skip past it).
                fixed = _telegram_update(202, 9202, "Fixed")
                result2 = await run_telegram_poller_tick(
                    session_factory=factory, gateway=_updates_gateway([fixed])
                )
                assert result2 == {
                    "updates": 1,
                    "articles": 1,
                    "tickets_created": 1,
                    "skipped": 0,
                }
                await session.commit()
                offset2 = await get_setting(session, KEY_TELEGRAM_UPDATE_OFFSET)
                assert offset2 == "203"
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_poller_tick_dedup_skips_updates_below_offset(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await set_setting(session, "daemon.telegram_poller.enabled", "1")
                await set_setting(session, "channel.telegram.enabled", "1")
                await set_setting(session, "channel.telegram.mode", "polling")
                await set_setting(session, "channel.telegram.bot_token", "test-token")
                await set_setting(
                    session, "channel.telegram.default_customer_user", "portal-default"
                )
                await set_setting(session, KEY_TELEGRAM_UPDATE_OFFSET, "302")

                stale = _telegram_update(301, 9301, "Stale, already processed")
                fresh = _telegram_update(302, 9302, "Fresh")
                result = await run_telegram_poller_tick(
                    session_factory=factory, gateway=_updates_gateway([stale, fresh])
                )
                assert result == {
                    "updates": 2,
                    "articles": 1,
                    "tickets_created": 1,
                    "skipped": 1,
                }
                offset = await get_setting(session, KEY_TELEGRAM_UPDATE_OFFSET)
                assert offset == "303"
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Webhook route (Task 3)
# ---------------------------------------------------------------------------


def _fake_request(body: dict, *, secret: str | None) -> Request:
    import json

    payload = json.dumps(body).encode()
    headers = [(b"content-type", b"application/json")]
    if secret is not None:
        headers.append((b"x-telegram-bot-api-secret-token", secret.encode()))
    scope = {"type": "http", "method": "POST", "headers": headers}

    async def receive() -> dict:
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(scope, receive)


async def _webhook_setup(session: AsyncSession, *, mode: str = "webhook") -> None:
    await set_setting(session, "channel.telegram.enabled", "1")
    await set_setting(session, "channel.telegram.mode", mode)
    await set_setting(session, "channel.telegram.webhook_secret_token", "wh-secret")
    await set_setting(session, "channel.telegram.default_customer_user", "portal-default")


@pytest.mark.db
async def test_webhook_happy_path_creates_article(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # process_update needs a session_factory for ticket creation; the route's
    # own get_session_factory() would reach for the (unconfigured) default
    # engine here since we're calling the route function directly rather
    # than through the FastAPI app, so point it at the test engine.
    monkeypatch.setattr(channels_telegram, "get_session_factory", lambda: factory)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await _webhook_setup(session)
                update = _telegram_update(401, 9401, "Hi via webhook")
                request = _fake_request(update, secret="wh-secret")

                response = await channels_telegram.receive_webhook(
                    request, session, x_secret="wh-secret"
                )
                assert response.ok is True
                assert response.skipped is False

                offset = await get_setting(session, KEY_TELEGRAM_UPDATE_OFFSET)
                assert offset == "402"

                row = (
                    await session.execute(
                        text("SELECT COUNT(*) FROM article_data_mime WHERE a_body = :b"),
                        {"b": "Hi via webhook"},
                    )
                ).scalar()
                assert row == 1
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_webhook_wrong_secret_401(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await _webhook_setup(session)
                update = _telegram_update(402, 9402, "Nope")
                request = _fake_request(update, secret="wrong")

                with pytest.raises(HTTPException) as exc_info:
                    await channels_telegram.receive_webhook(request, session, x_secret="wrong")
                assert exc_info.value.status_code == 401
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_webhook_channel_disabled_404(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                # channel.telegram.enabled left unset (default off).
                update = _telegram_update(403, 9403, "Nope")
                request = _fake_request(update, secret="wh-secret")

                with pytest.raises(HTTPException) as exc_info:
                    await channels_telegram.receive_webhook(request, session, x_secret="wh-secret")
                assert exc_info.value.status_code == 404
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_webhook_mode_polling_409(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await _webhook_setup(session, mode="polling")
                update = _telegram_update(404, 9404, "Nope")
                request = _fake_request(update, secret="wh-secret")

                with pytest.raises(HTTPException) as exc_info:
                    await channels_telegram.receive_webhook(request, session, x_secret="wh-secret")
                assert exc_info.value.status_code == 409
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_webhook_duplicate_update_skipped(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(channels_telegram, "get_session_factory", lambda: factory)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await _webhook_setup(session)
                update = _telegram_update(405, 9405, "Once")

                first = await channels_telegram.receive_webhook(
                    _fake_request(update, secret="wh-secret"), session, x_secret="wh-secret"
                )
                assert first.ok is True
                assert first.skipped is False

                second = await channels_telegram.receive_webhook(
                    _fake_request(update, secret="wh-secret"), session, x_secret="wh-secret"
                )
                assert second.ok is True
                assert second.skipped is True
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# webhook-register / webhook-unregister (Task 3, admin-only)
# ---------------------------------------------------------------------------


def _root_user() -> AuthenticatedUser:
    # Root (id=1) is present via Znuny's initial_insert seed data and is a
    # member of the admin group -- same convention as test_admin_channels.py.
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


def _seed_plain_user(sync_url: str) -> int:
    ns = uuid.uuid4().int % 1_000_000
    plain_id = 500_000 + ns
    login = f"plain.telegram.{ns}"
    pw = hash_password("secret")
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM group_user WHERE user_id = :id"), {"id": plain_id})
        conn.execute(text("DELETE FROM role_user WHERE user_id = :id"), {"id": plain_id})
        conn.execute(
            text("DELETE FROM users WHERE id = :id OR login = :login"),
            {"id": plain_id, "login": login},
        )
        conn.execute(
            text(
                """
                INSERT INTO users (id, login, pw, first_name, last_name, valid_id,
                                  create_time, create_by, change_time, change_by)
                VALUES (:id, :login, :pw, 'Plain', 'Telegram', 1, :t, 1, :t, 1)
                """
            ),
            {"id": plain_id, "login": login, "pw": pw, "t": NOW},
        )
    engine.dispose()
    return plain_id


class _FakeRegisterGateway:
    calls: list[tuple] = []

    def __init__(self, *, bot_token: str) -> None:
        self.bot_token = bot_token

    async def set_webhook(
        self, url: str, secret_token: str, allowed_updates: list[str] | None = None
    ) -> None:
        _FakeRegisterGateway.calls.append(("set_webhook", url, secret_token, allowed_updates))

    async def delete_webhook(self) -> None:
        _FakeRegisterGateway.calls.append(("delete_webhook",))


@pytest.mark.db
async def test_webhook_register_calls_set_webhook_with_url_and_secret(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    _FakeRegisterGateway.calls = []
    monkeypatch.setattr(channels_telegram, "TelegramGateway", _FakeRegisterGateway)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await set_setting(session, "channel.telegram.bot_token", "test-token")
                await set_setting(
                    session, "channel.telegram.webhook_url", "https://example.com/hook"
                )
                await set_setting(session, "channel.telegram.webhook_secret_token", "wh-secret")

                response = await channels_telegram.register_webhook(
                    channels_telegram.TelegramWebhookRegisterRequest(), _root_user(), session
                )
                assert response.ok is True
                assert response.url == "https://example.com/hook"
                assert _FakeRegisterGateway.calls == [
                    ("set_webhook", "https://example.com/hook", "wh-secret", ["message"])
                ]
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_webhook_register_missing_config_409(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                with pytest.raises(HTTPException) as exc_info:
                    await channels_telegram.register_webhook(
                        channels_telegram.TelegramWebhookRegisterRequest(), _root_user(), session
                    )
                assert exc_info.value.status_code == 409
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_webhook_register_403_for_non_admin(mariadb_znuny_url: str) -> None:
    plain_id = _seed_plain_user(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            try:
                plain_user = AuthenticatedUser(
                    id=plain_id,
                    login="plain",
                    first_name="Plain",
                    last_name="Telegram",
                    auth_method="session",
                )
                with pytest.raises(HTTPException) as exc_info:
                    await get_admin_user(plain_user, session)
                assert exc_info.value.status_code == 403
            finally:
                # Unlike test_admin_daemons.py (a grandfathered leaker, see
                # db_leak_baseline.txt), this module deletes what it commits.
                await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": plain_id})
                await session.commit()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Outbound agent replies (Task 4: send-then-store + dispatch seam)
# ---------------------------------------------------------------------------


class _FakeTelegramGateway:
    """Records sends; can be made to fail like a real Bot API error."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[int, str]] = []
        self._fail = fail

    async def send_message(self, chat_id: int | str, text_body: str) -> dict:
        if self._fail:
            raise TelegramApiError("boom")
        self.sent.append((int(chat_id), text_body))
        return {"message_id": 999}


@pytest.mark.db
async def test_agent_reply_via_write_service_sends_and_stores(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """channel=telegram agent article, dispatched through TicketWriteService.add_article
    (the seam), resolves chat_id via the mapped tiqora_telegram_contact row."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await set_setting(
                    session, "channel.telegram.default_customer_user", "portal-default"
                )
                await set_setting(session, "channel.telegram.enabled", "1")
                sysconfig = SysConfig(session)

                inbound = await process_update(
                    session, factory, sysconfig, None, _text_message(555, "Hi there"), user_id=1
                )
                await session.commit()

                # Simulate an admin-linked contact so resolution uses the
                # contact-mapping path rather than the a_from fallback.
                await session.execute(
                    text(
                        "UPDATE tiqora_telegram_contact SET customer_user_login = 'portal-default'"
                        " WHERE chat_id = 555"
                    )
                )
                await session.commit()

                fake_gateway = _FakeTelegramGateway()

                async def _fake_build_gateway(_session: AsyncSession) -> _FakeTelegramGateway:
                    return fake_gateway

                monkeypatch.setattr(
                    "tiqora.channels.telegram.outbound.build_gateway", _fake_build_gateway
                )

                async with session.begin():
                    svc = TicketWriteService(session, factory, sysconfig)
                    article_id = await svc.add_article(
                        1,
                        inbound["ticket_id"],
                        ArticleIn(
                            sender_type="agent",
                            is_visible_for_customer=False,  # forced True for telegram agent
                            subject="Re: Hi there",
                            body="Thanks, we will help.",
                            channel="telegram",
                        ),
                    )

                assert fake_gateway.sent == [(555, "Thanks, we will help.")]

                row = (
                    await session.execute(
                        text(
                            "SELECT a_body, a_to FROM article_data_mime WHERE article_id = :aid"
                        ),
                        {"aid": article_id},
                    )
                ).first()
                assert row is not None
                assert row[0] == "Thanks, we will help."
                assert row[1] == "555@telegram.invalid"

                ch_row = (
                    await session.execute(
                        text(
                            "SELECT cc.name, ast.name FROM article a"
                            " JOIN communication_channel cc ON cc.id = a.communication_channel_id"
                            " JOIN article_sender_type ast ON ast.id = a.article_sender_type_id"
                            " WHERE a.id = :aid"
                        ),
                        {"aid": article_id},
                    )
                ).first()
                assert ch_row is not None
                assert ch_row[0] == "Telegram"
                assert ch_row[1] == "agent"

                visible_row = (
                    await session.execute(
                        text("SELECT is_visible_for_customer FROM article WHERE id = :aid"),
                        {"aid": article_id},
                    )
                ).first()
                assert visible_row is not None
                assert bool(visible_row[0]) is True
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_agent_reply_resolves_chat_id_via_a_from_fallback(mariadb_znuny_url: str) -> None:
    """Ticket without a contact->customer_user mapping: chat_id comes from the
    most recent inbound article's a_from local-part instead."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await set_setting(
                    session, "channel.telegram.default_customer_user", "portal-default"
                )
                await set_setting(session, "channel.telegram.enabled", "1")
                sysconfig = SysConfig(session)

                inbound = await process_update(
                    session, factory, sysconfig, None, _text_message(777, "Fallback please"), user_id=1
                )
                await session.commit()

                # No contact.customer_user_login mapping was ever set -- the
                # contact-mapping path in _resolve_chat_id must miss.
                fake_gateway = _FakeTelegramGateway()
                async with session.begin():
                    article_id = await deliver_agent_telegram_reply(
                        session,
                        sysconfig,
                        ticket_id=inbound["ticket_id"],
                        user_id=1,
                        article=ArticleIn(
                            sender_type="agent",
                            is_visible_for_customer=False,
                            subject="Re: Fallback please",
                            body="Got it via fallback.",
                            channel="telegram",
                        ),
                        gateway=fake_gateway,
                    )

                assert fake_gateway.sent == [(777, "Got it via fallback.")]
                to_row = (
                    await session.execute(
                        text("SELECT a_to FROM article_data_mime WHERE article_id = :aid"),
                        {"aid": article_id},
                    )
                ).first()
                assert to_row is not None
                assert to_row[0] == "777@telegram.invalid"
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_agent_reply_send_failure_stores_no_article(mariadb_znuny_url: str) -> None:
    """send-then-store: a failed Bot API call must leave no article row."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await set_setting(
                    session, "channel.telegram.default_customer_user", "portal-default"
                )
                await set_setting(session, "channel.telegram.enabled", "1")
                sysconfig = SysConfig(session)

                inbound = await process_update(
                    session, factory, sysconfig, None, _text_message(888, "Will fail"), user_id=1
                )
                await session.commit()

                count_before = (
                    await session.execute(
                        text("SELECT COUNT(*) FROM article WHERE ticket_id = :tid"),
                        {"tid": inbound["ticket_id"]},
                    )
                ).scalar()
                # The read above autobegins a transaction (SQLAlchemy 2
                # autobegin) -- close it out before session.begin() below,
                # which errors on "A transaction is already begun".
                await session.commit()

                failing_gateway = _FakeTelegramGateway(fail=True)
                with pytest.raises(TelegramDeliveryError):
                    async with session.begin():
                        await deliver_agent_telegram_reply(
                            session,
                            sysconfig,
                            ticket_id=inbound["ticket_id"],
                            user_id=1,
                            article=ArticleIn(
                                sender_type="agent",
                                is_visible_for_customer=False,
                                subject="Re: Will fail",
                                body="This never arrives.",
                                channel="telegram",
                            ),
                            gateway=failing_gateway,
                        )

                count_after = (
                    await session.execute(
                        text("SELECT COUNT(*) FROM article WHERE ticket_id = :tid"),
                        {"tid": inbound["ticket_id"]},
                    )
                ).scalar()
                assert count_after == count_before
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_agent_reply_disabled_channel_raises(mariadb_znuny_url: str) -> None:
    """Channel not enabled (default) -> TelegramDeliveryError, no send attempted."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await set_setting(
                    session, "channel.telegram.default_customer_user", "portal-default"
                )
                # channel.telegram.enabled intentionally left unset (defaults False).
                sysconfig = SysConfig(session)

                inbound = await process_update(
                    session, factory, sysconfig, None, _text_message(999, "Disabled test"), user_id=1
                )
                await session.commit()

                fake_gateway = _FakeTelegramGateway()
                with pytest.raises(TelegramDeliveryError):
                    async with session.begin():
                        await deliver_agent_telegram_reply(
                            session,
                            sysconfig,
                            ticket_id=inbound["ticket_id"],
                            user_id=1,
                            article=ArticleIn(
                                sender_type="agent",
                                is_visible_for_customer=False,
                                subject="Re: Disabled test",
                                body="Should not send.",
                                channel="telegram",
                            ),
                            gateway=fake_gateway,
                        )
                assert fake_gateway.sent == []
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()
