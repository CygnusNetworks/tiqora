"""Tests for the Telegram channel: gateway (httpx.MockTransport, token
scrubbing) and DB-backed inbound processing (ticket/article creation,
per-chat ticket continuity, contact upsert), plus the two Task 3 transports
(long-poll daemon tick, webhook route)."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
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
from tiqora.channels.telegram.messages import (
    ButtonSpec,
    buttons_from_json,
    get_by_article,
    get_by_message,
    record_message,
)
from tiqora.channels.telegram.outbound import TelegramDeliveryError, deliver_agent_telegram_reply
from tiqora.channels.telegram.service import process_update
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraTelegramContact
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.settings_store import KEY_TELEGRAM_UPDATE_OFFSET, get_setting, set_setting
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    InvalidInput,
    TelegramSendOptions,
    TicketWriteService,
    add_article,
)
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
    "tiqora_telegram_message",
    "article",
    "tiqora_cache_invalidation",
    "tiqora_event_outbox",
    "ticket_number_counter",
    "ticket",
    "tiqora_telegram_contact",
    "communication_channel",
)

# tiqora_telegram_message has no autoincrement id -- it's keyed on article_id
# (see its model docstring), which lives in the same numeric namespace as
# article.id since record_message() is always called with the just-created
# article's id. Every other table here is keyed on a real autoincrement id.
_ID_COLUMN: dict[str, str] = {"tiqora_telegram_message": "article_id"}


async def _snapshot_max_ids(session: AsyncSession) -> dict[str, int]:
    return {
        table: int(
            (
                await session.execute(
                    text(f"SELECT COALESCE(MAX({_ID_COLUMN.get(table, 'id')}), 0) FROM {table}")
                )
            ).scalar()
            or 0
        )
        for table in _WRITE_TABLES
    }


async def _cleanup_new_rows(session: AsyncSession, before: dict[str, int]) -> None:
    for table in _WRITE_TABLES:
        col = _ID_COLUMN.get(table, "id")
        await session.execute(text(f"DELETE FROM {table} WHERE {col} > :b"), {"b": before[table]})
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
# gateway: media, edit, delete
# ---------------------------------------------------------------------------


async def test_gateway_send_message_reply_to_message_id() -> None:
    gateway, calls = _recording_gateway()
    await gateway.send_message(123, "hi", reply_to_message_id=7)
    _method, payload = next(c for c in calls if c[0] == "sendMessage")
    assert payload["reply_parameters"] == {
        "message_id": 7,
        "allow_sending_without_reply": True,
    }


async def test_gateway_send_photo_multipart() -> None:
    gateway, calls = _recording_gateway()
    await gateway.send_photo(123, b"fake-jpeg-bytes", "cat.jpg", caption="Miau")
    _method, payload = next(c for c in calls if c[0] == "sendPhoto")
    assert payload["_content_type"].startswith("multipart/form-data")
    body = payload["_body"]
    assert b"cat.jpg" in body
    assert b"fake-jpeg-bytes" in body


async def test_gateway_send_document_multipart() -> None:
    gateway, calls = _recording_gateway()
    await gateway.send_document(123, b"%PDF-1.4 fake", "invoice.pdf", "application/pdf")
    _method, payload = next(c for c in calls if c[0] == "sendDocument")
    assert payload["_content_type"].startswith("multipart/form-data")
    body = payload["_body"]
    assert b"invoice.pdf" in body
    assert b"%PDF-1.4 fake" in body


async def test_gateway_send_photo_and_document_carry_reply_markup() -> None:
    """With an empty text body the last attachment carries the inline
    keyboard, so the multipart sends must be able to attach one."""
    gateway, calls = _recording_gateway()
    keyboard = {"inline_keyboard": [[{"text": "Ja", "callback_data": "tqb:0"}]]}
    await gateway.send_photo(123, b"img", "cat.jpg", reply_markup=keyboard)
    await gateway.send_document(123, b"doc", "a.pdf", "application/pdf", reply_markup=keyboard)
    for method in ("sendPhoto", "sendDocument"):
        _method, payload = next(c for c in calls if c[0] == method)
        assert b'name="reply_markup"' in payload["_body"]
        assert json.dumps(keyboard).encode() in payload["_body"]
    await gateway.send_photo(123, b"img", "cat.jpg")
    assert b'name="reply_markup"' not in calls[-1][1]["_body"]


async def test_gateway_edit_message_text() -> None:
    gateway, calls = _recording_gateway()
    await gateway.edit_message_text(123, 55, "updated text")
    _method, payload = next(c for c in calls if c[0] == "editMessageText")
    assert payload["chat_id"] == 123
    assert payload["message_id"] == 55
    assert payload["text"] == "updated text"
    assert "reply_markup" not in payload


async def test_gateway_edit_message_reply_markup_none_clears_keyboard() -> None:
    gateway, calls = _recording_gateway()
    await gateway.edit_message_reply_markup(123, 55, None)
    _method, payload = next(c for c in calls if c[0] == "editMessageReplyMarkup")
    assert payload["reply_markup"] == {"inline_keyboard": []}


async def test_gateway_delete_message() -> None:
    gateway, calls = _recording_gateway()
    await gateway.delete_message(123, 55)
    _method, payload = next(c for c in calls if c[0] == "deleteMessage")
    assert payload == {"chat_id": 123, "message_id": 55}


async def test_gateway_delete_message_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"ok": False, "description": "message can't be deleted for everyone"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gw = TelegramGateway(bot_token="test-token", client=client)
    with pytest.raises(TelegramApiError) as exc_info:
        await gw.delete_message(123, 55)
    await client.aclose()
    assert "message can't be deleted for everyone" in str(exc_info.value)


# ---------------------------------------------------------------------------
# DB-backed inbound processing
# ---------------------------------------------------------------------------


def _text_message(
    chat_id: int,
    text_body: str,
    *,
    user_id: int = 900,
    is_bot: bool = False,
    message_id: int = 1,
    reply_to_message_id: int | None = None,
) -> dict:
    message: dict[str, Any] = {
        "message_id": message_id,
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
    }
    if reply_to_message_id is not None:
        message["reply_to_message"] = {"message_id": reply_to_message_id}
    return {
        "update_id": 1,
        "message": message,
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
                await set_setting(session, "channel.telegram.consent_required", "0")

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

                # ArticleListItem.communication_channel_name: same join
                # TicketService.list_articles resolves via communication_channel.
                channel_name_row = (
                    await session.execute(
                        text(
                            "SELECT cc.name FROM article a"
                            " JOIN communication_channel cc ON cc.id = a.communication_channel_id"
                            " WHERE a.id = :aid"
                        ),
                        {"aid": result["article_id"]},
                    )
                ).first()
                assert channel_name_row is not None
                assert channel_name_row[0] == "Telegram"
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
                await set_setting(session, "channel.telegram.consent_required", "0")
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
async def test_inbound_records_telegram_message_id(mariadb_znuny_url: str) -> None:
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
                await set_setting(session, "channel.telegram.consent_required", "0")
                sysconfig = SysConfig(session)

                result = await process_update(
                    session,
                    factory,
                    sysconfig,
                    None,
                    _text_message(9701, "Need help with my order", message_id=101),
                    user_id=1,
                )
                await session.commit()

                row = await get_by_message(session, 9701, 101)
                assert row is not None
                assert row.article_id == result["article_id"]
                assert row.ticket_id == result["ticket_id"]
                assert row.direction == "in"
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_inbound_quote_reply_records_reply_to_article_id(mariadb_znuny_url: str) -> None:
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
                await set_setting(session, "channel.telegram.consent_required", "0")
                sysconfig = SysConfig(session)

                first = await process_update(
                    session,
                    factory,
                    sysconfig,
                    None,
                    _text_message(9702, "Original question", message_id=101),
                    user_id=1,
                )
                await session.commit()

                await process_update(
                    session,
                    factory,
                    sysconfig,
                    None,
                    _text_message(
                        9702, "Quoting the first one", message_id=102, reply_to_message_id=101
                    ),
                    user_id=1,
                )
                await session.commit()

                row = await get_by_message(session, 9702, 102)
                assert row is not None
                assert row.reply_to_article_id == first["article_id"]
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_inbound_quote_unknown_message_id_leaves_reply_to_article_id_none(
    mariadb_znuny_url: str,
) -> None:
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
                await set_setting(session, "channel.telegram.consent_required", "0")
                sysconfig = SysConfig(session)

                result = await process_update(
                    session,
                    factory,
                    sysconfig,
                    None,
                    _text_message(
                        9703,
                        "Quoting something we never saw",
                        message_id=201,
                        reply_to_message_id=999,
                    ),
                    user_id=1,
                )
                await session.commit()

                row = await get_by_message(session, 9703, 201)
                assert row is not None
                assert row.reply_to_article_id is None
                assert result["article_id"] is not None
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_inbound_duplicate_delivery_does_not_crash_message_map(
    mariadb_znuny_url: str,
) -> None:
    """Webhook retry racing the offset advance: two ``process_update`` calls
    for the very same ``(chat_id, message_id)`` must not raise -- the second
    call's ``record_message`` is a no-op thanks to the ``get_by_message``
    guard in ``process_update`` (see module docstring / Task 3 report), so
    exactly one map row survives and it still points at the first article."""
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
                await set_setting(session, "channel.telegram.consent_required", "0")
                sysconfig = SysConfig(session)

                update = _text_message(9704, "Duplicate delivery", message_id=301)

                first = await process_update(session, factory, sysconfig, None, update, user_id=1)
                await session.commit()

                # Same update delivered a second time, as if a webhook retry
                # raced the offset advance -- must not raise IntegrityError
                # on the (chat_id, message_id) unique index.
                await process_update(session, factory, sysconfig, None, update, user_id=1)
                await session.commit()

                rows = (
                    await session.execute(
                        text(
                            "SELECT article_id FROM tiqora_telegram_message"
                            " WHERE chat_id = :cid AND message_id = :mid"
                        ),
                        {"cid": 9704, "mid": 301},
                    )
                ).all()
                assert len(rows) == 1
                assert rows[0][0] == first["article_id"]
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
                await set_setting(session, "channel.telegram.consent_required", "0")
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
                await set_setting(session, "channel.telegram.consent_required", "0")
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
                await set_setting(session, "channel.telegram.consent_required", "0")
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
# DSGVO consent flow (Task 13)
# ---------------------------------------------------------------------------


def _callback_update(
    update_id: int, chat_id: int, user_id: int, *, data: str = "tiqora_consent_accept"
) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cbq{update_id}",
            "from": {
                "id": user_id,
                "is_bot": False,
                "first_name": "Ada",
                "last_name": "Lovelace",
                "username": "ada",
            },
            "message": {
                "message_id": update_id,
                "chat": {"id": chat_id, "type": "private"},
            },
            "data": data,
        },
    }


def _recording_gateway() -> tuple[TelegramGateway, list[tuple[str, dict]]]:
    """A Telegram gateway backed by ``httpx.MockTransport`` that always
    succeeds and records every call as ``(method, json_payload)``.

    Multipart calls (``sendPhoto``/``sendDocument``) have no JSON body, so
    their recorded payload is ``{"_content_type": ..., "_body": <raw bytes>}``
    instead -- enough to assert the content-type and that the filename/bytes
    made it into the upload."""
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = str(request.url).rsplit("/", 1)[-1]
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            payload: dict[str, Any] = {"_content_type": content_type, "_body": request.content}
        else:
            payload = json.loads(request.content or b"{}")
        calls.append((method, payload))
        if method == "answerCallbackQuery":
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TelegramGateway(bot_token="test-token", client=client), calls


@pytest.mark.db
async def test_consent_required_message_without_consent_skips_and_prompts(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                # consent_required left unset -- default is ON.
                await set_setting(
                    session, "channel.telegram.default_customer_user", "portal-default"
                )
                sysconfig = SysConfig(session)
                gateway, calls = _recording_gateway()

                result = await process_update(
                    session, factory, sysconfig, gateway, _text_message(9501, "Hallo"), user_id=1
                )
                await session.commit()

                assert result == {"skipped": "no_consent"}

                sent = [c for c in calls if c[0] == "sendMessage"]
                assert len(sent) == 1
                keyboard = sent[0][1]["reply_markup"]["inline_keyboard"]
                assert keyboard[0][0]["callback_data"] == "tiqora_consent_accept"

                ticket_count = (
                    await session.execute(
                        text("SELECT COUNT(*) FROM ticket WHERE id > :b"), {"b": before["ticket"]}
                    )
                ).scalar()
                assert ticket_count == 0

                contact = (
                    await session.execute(
                        select(TiqoraTelegramContact).where(TiqoraTelegramContact.chat_id == 9501)
                    )
                ).scalar_one()
                assert contact.consent_time is None
                assert contact.consent_prompt_time is not None
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_consent_reprompt_suppressed_within_window(mariadb_znuny_url: str) -> None:
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
                gateway, calls = _recording_gateway()

                first = await process_update(
                    session,
                    factory,
                    sysconfig,
                    gateway,
                    _text_message(9502, "One"),
                    user_id=1,
                )
                await session.commit()
                assert first == {"skipped": "no_consent"}
                assert len([c for c in calls if c[0] == "sendMessage"]) == 1

                second = await process_update(
                    session,
                    factory,
                    sysconfig,
                    gateway,
                    _text_message(9502, "Two"),
                    user_id=1,
                )
                await session.commit()
                assert second == {"skipped": "no_consent"}
                # Still just the one prompt -- the second message arrived
                # well within CONSENT_REPROMPT_SECONDS.
                assert len([c for c in calls if c[0] == "sendMessage"]) == 1
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_consent_callback_accept_sets_consent_no_ticket(mariadb_znuny_url: str) -> None:
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
                gateway, calls = _recording_gateway()

                result = await process_update(
                    session,
                    factory,
                    sysconfig,
                    gateway,
                    _callback_update(504, 9504, 19504),
                    user_id=1,
                )
                await session.commit()

                assert result == {"consent": "accepted"}
                assert len([c for c in calls if c[0] == "answerCallbackQuery"]) == 1
                sent = [c for c in calls if c[0] == "sendMessage"]
                assert len(sent) == 1
                assert sent[0][1]["text"] == (
                    "Vielen Dank! Ihre Zustimmung wurde gespeichert. "
                    "Bitte senden Sie jetzt Ihr Anliegen."
                )

                ticket_count = (
                    await session.execute(
                        text("SELECT COUNT(*) FROM ticket WHERE id > :b"), {"b": before["ticket"]}
                    )
                ).scalar()
                assert ticket_count == 0

                contact = (
                    await session.execute(
                        select(TiqoraTelegramContact).where(TiqoraTelegramContact.chat_id == 9504)
                    )
                ).scalar_one()
                assert contact.consent_time is not None
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_message_after_consent_creates_ticket(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                session.add(
                    TiqoraTelegramContact(
                        chat_id=9505,
                        telegram_user_id=19505,
                        username="ada",
                        display_name="Ada Lovelace",
                        consent_time=NOW,
                    )
                )
                await session.commit()
                await set_setting(
                    session, "channel.telegram.default_customer_user", "portal-default"
                )
                sysconfig = SysConfig(session)

                result = await process_update(
                    session, factory, sysconfig, None, _text_message(9505, "Ready now"), user_id=1
                )
                await session.commit()

                assert result["created_ticket"] is True
                assert "ticket_id" in result
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_consent_disabled_creates_ticket_immediately(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await set_setting(session, "channel.telegram.consent_required", "0")
                await set_setting(
                    session, "channel.telegram.default_customer_user", "portal-default"
                )
                sysconfig = SysConfig(session)

                result = await process_update(
                    session,
                    factory,
                    sysconfig,
                    None,
                    _text_message(9506, "Immediate"),
                    user_id=1,
                )
                await session.commit()

                assert result["created_ticket"] is True
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
                await set_setting(session, "channel.telegram.consent_required", "0")

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
                await set_setting(session, "channel.telegram.consent_required", "0")

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
                await set_setting(session, "channel.telegram.consent_required", "0")
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


def _updates_and_actions_gateway(updates: list[dict]) -> TelegramGateway:
    """Like :func:`_updates_gateway`, but also answers ``sendMessage`` /
    ``answerCallbackQuery`` calls -- needed for a callback_query update,
    whose consent-accept handling calls both."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/getUpdates" in url:
            return httpx.Response(200, json={"ok": True, "result": updates})
        if "/answerCallbackQuery" in url:
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TelegramGateway(bot_token="test-token", client=client)


@pytest.mark.db
async def test_poller_tick_processes_callback_query_and_advances_offset(
    mariadb_znuny_url: str,
) -> None:
    """A callback_query update (consent accept) must be dispatched through
    the poller the same as a message update, advancing the offset."""
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

                update = _callback_update(601, 9601, 19601)
                result = await run_telegram_poller_tick(
                    session_factory=factory, gateway=_updates_and_actions_gateway([update])
                )
                assert result["updates"] == 1

                await session.commit()
                offset = await get_setting(session, KEY_TELEGRAM_UPDATE_OFFSET)
                assert offset == "602"

                contact = (
                    await session.execute(
                        select(TiqoraTelegramContact).where(TiqoraTelegramContact.chat_id == 9601)
                    )
                ).scalar_one()
                assert contact.consent_time is not None
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# /start = new dialog (Task: Telegram-Chat-UX)
# ---------------------------------------------------------------------------


def test_render_start_text_empty_first_name_no_double_space() -> None:
    from tiqora.channels.telegram.service import _render_start_text

    rendered = _render_start_text(
        "Hallo {first_name}! 👋 Schildere mir bitte kurz dein Anliegen.", ""
    )
    assert "  " not in rendered
    assert rendered == "Hallo ! 👋 Schildere mir bitte kurz dein Anliegen."

    rendered_named = _render_start_text("Hallo {first_name}!", "Ada")
    assert rendered_named == "Hallo Ada!"


@pytest.mark.db
async def test_start_without_consent_still_prompts_consent(mariadb_znuny_url: str) -> None:
    """Consent gate has priority: /start from an unconsented chat is treated
    like any other message -- consent prompt, no greeting, no reset."""
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
                gateway, calls = _recording_gateway()

                result = await process_update(
                    session, factory, sysconfig, gateway, _text_message(9601, "/start"), user_id=1
                )
                await session.commit()

                assert result == {"skipped": "no_consent"}
                sent = [c for c in calls if c[0] == "sendMessage"]
                assert len(sent) == 1
                assert sent[0][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == (
                    "tiqora_consent_accept"
                )

                contact = (
                    await session.execute(
                        select(TiqoraTelegramContact).where(TiqoraTelegramContact.chat_id == 9601)
                    )
                ).scalar_one()
                assert contact.new_dialog_since is None
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_start_with_consent_greets_and_resets_no_ticket(mariadb_znuny_url: str) -> None:
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
                await set_setting(session, "channel.telegram.consent_required", "0")
                sysconfig = SysConfig(session)
                gateway, calls = _recording_gateway()

                result = await process_update(
                    session, factory, sysconfig, gateway, _text_message(9602, "/start"), user_id=1
                )
                await session.commit()

                assert result == {"command": "start"}

                sent = [c for c in calls if c[0] == "sendMessage"]
                assert len(sent) == 1
                assert "Ada" in sent[0][1]["text"]
                assert "  " not in sent[0][1]["text"]

                contact = (
                    await session.execute(
                        select(TiqoraTelegramContact).where(TiqoraTelegramContact.chat_id == 9602)
                    )
                ).scalar_one()
                assert contact.new_dialog_since is not None

                ticket_count = (
                    await session.execute(
                        text("SELECT COUNT(*) FROM ticket WHERE id > :b"), {"b": before["ticket"]}
                    )
                ).scalar()
                assert ticket_count == 0
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_start_resets_ticket_continuity(mariadb_znuny_url: str) -> None:
    """/start forces a new ticket on the next message even though the old
    per-chat ticket is still open; a further message then continues the
    *new* ticket (stage b picks it up again once it has its own article)."""
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
                await set_setting(session, "channel.telegram.consent_required", "0")
                sysconfig = SysConfig(session)
                gateway, _calls = _recording_gateway()

                first = await process_update(
                    session,
                    factory,
                    sysconfig,
                    gateway,
                    _text_message(9603, "Old conversation"),
                    user_id=1,
                )
                await session.commit()
                assert first["created_ticket"] is True

                start_result = await process_update(
                    session, factory, sysconfig, gateway, _text_message(9603, "/start"), user_id=1
                )
                await session.commit()
                assert start_result == {"command": "start"}

                # DATETIME columns are second-resolution: without a real gap,
                # the next article could land in the very same wall-clock
                # second as new_dialog_since and the strict '>' comparison
                # would (correctly, if narrowly) miss it -- a real customer
                # always takes longer than that to type a reply.
                await asyncio.sleep(1.1)

                second = await process_update(
                    session,
                    factory,
                    sysconfig,
                    gateway,
                    _text_message(9603, "Brand new topic"),
                    user_id=1,
                )
                await session.commit()
                assert second["created_ticket"] is True
                assert second["ticket_id"] != first["ticket_id"]

                third = await process_update(
                    session,
                    factory,
                    sysconfig,
                    gateway,
                    _text_message(9603, "Follow-up on the new topic"),
                    user_id=1,
                )
                await session.commit()
                assert third["created_ticket"] is False
                assert third["ticket_id"] == second["ticket_id"]
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_followup_tag_beats_start_reset(mariadb_znuny_url: str) -> None:
    """An explicit follow-up tag in the message text outranks a prior
    /start reset -- stage (a) always wins."""
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
                await set_setting(session, "channel.telegram.consent_required", "0")
                sysconfig = SysConfig(session)
                gateway, _calls = _recording_gateway()

                first = await process_update(
                    session,
                    factory,
                    sysconfig,
                    gateway,
                    _text_message(9604, "Original issue"),
                    user_id=1,
                )
                await session.commit()

                tn_row = (
                    await session.execute(
                        text("SELECT tn FROM ticket WHERE id = :tid"), {"tid": first["ticket_id"]}
                    )
                ).first()
                assert tn_row is not None
                tn = tn_row[0]

                start_result = await process_update(
                    session, factory, sysconfig, gateway, _text_message(9604, "/start"), user_id=1
                )
                await session.commit()
                assert start_result == {"command": "start"}

                followup = await process_update(
                    session,
                    factory,
                    sysconfig,
                    gateway,
                    _text_message(9604, f"Re: [Ticket#{tn}] more details"),
                    user_id=1,
                )
                await session.commit()
                assert followup["created_ticket"] is False
                assert followup["ticket_id"] == first["ticket_id"]
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
    await set_setting(session, "channel.telegram.consent_required", "0")


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


@pytest.mark.db
async def test_webhook_processes_callback_query_and_advances_offset(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A callback_query delivery (consent accept) must be dispatched through
    the webhook route the same as a message update, advancing the offset."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(channels_telegram, "get_session_factory", lambda: factory)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                await _webhook_setup(session)
                update = _callback_update(406, 9406, 19406)
                request = _fake_request(update, secret="wh-secret")

                response = await channels_telegram.receive_webhook(
                    request, session, x_secret="wh-secret"
                )
                assert response.ok is True
                assert response.skipped is False

                offset = await get_setting(session, KEY_TELEGRAM_UPDATE_OFFSET)
                assert offset == "407"

                contact = (
                    await session.execute(
                        select(TiqoraTelegramContact).where(TiqoraTelegramContact.chat_id == 9406)
                    )
                ).scalar_one()
                assert contact.consent_time is not None
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
                    (
                        "set_webhook",
                        "https://example.com/hook",
                        "wh-secret",
                        ["message", "callback_query"],
                    )
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
    """Records sends; can be made to fail like a real Bot API error.

    ``fail=True`` fails every send; ``fail_on`` names the methods that fail
    (e.g. ``{"send_message"}`` to fail the text after a photo went out).
    ``calls`` records ``(method, kwargs)`` for every successful send in
    order; ``sent`` keeps the plain ``(chat_id, text)`` view of
    ``send_message`` the older tests assert on. Message ids count up from
    1000 so they never collide with an inbound message id in the same chat
    (``(chat_id, message_id)`` is unique in the map table)."""

    _SENDS = frozenset({"send_message", "send_photo", "send_document"})

    def __init__(
        self,
        *,
        fail: bool = False,
        fail_on: set[str] | None = None,
        fail_delete: bool = False,
    ) -> None:
        self.sent: list[tuple[int, str]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[tuple[int, int]] = []
        self._fail_on = set(self._SENDS) if fail else set(fail_on or ())
        self._fail_delete = fail_delete
        self._next_id = 1000

    def _record(self, method: str, **kwargs: Any) -> dict:
        if method in self._fail_on:
            raise TelegramApiError("boom")
        self._next_id += 1
        self.calls.append((method, {**kwargs, "message_id": self._next_id}))
        return {"message_id": self._next_id}

    async def send_message(
        self,
        chat_id: int | str,
        text_body: str,
        *,
        reply_markup: dict | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict:
        result = self._record(
            "send_message",
            chat_id=int(chat_id),
            text=text_body,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )
        self.sent.append((int(chat_id), text_body))
        return result

    async def send_photo(
        self,
        chat_id: int | str,
        content: bytes,
        filename: str,
        *,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
        reply_markup: dict | None = None,
    ) -> dict:
        return self._record(
            "send_photo",
            chat_id=int(chat_id),
            filename=filename,
            content=content,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )

    async def send_document(
        self,
        chat_id: int | str,
        content: bytes,
        filename: str,
        content_type: str,
        *,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
        reply_markup: dict | None = None,
    ) -> dict:
        return self._record(
            "send_document",
            chat_id=int(chat_id),
            filename=filename,
            content=content,
            content_type=content_type,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )

    async def delete_message(self, chat_id: int | str, message_id: int) -> None:
        if self._fail_delete:
            raise TelegramApiError("message can't be deleted")
        self.deleted.append((int(chat_id), message_id))


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
                await set_setting(session, "channel.telegram.consent_required", "0")
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
                        text("SELECT a_body, a_to FROM article_data_mime WHERE article_id = :aid"),
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
                await set_setting(session, "channel.telegram.consent_required", "0")
                await set_setting(session, "channel.telegram.enabled", "1")
                sysconfig = SysConfig(session)

                inbound = await process_update(
                    session,
                    factory,
                    sysconfig,
                    None,
                    _text_message(777, "Fallback please"),
                    user_id=1,
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
                await set_setting(session, "channel.telegram.consent_required", "0")
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
                await set_setting(session, "channel.telegram.consent_required", "0")
                # channel.telegram.enabled intentionally left unset (defaults False).
                sysconfig = SysConfig(session)

                inbound = await process_update(
                    session,
                    factory,
                    sysconfig,
                    None,
                    _text_message(999, "Disabled test"),
                    user_id=1,
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


# ---------------------------------------------------------------------------
# Outbound composer options: attachments, buttons, quote (chat composer Task 4)
# ---------------------------------------------------------------------------


async def _telegram_ticket(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    chat_id: int,
    text_body: str,
    *,
    message_id: int = 1,
) -> dict:
    """Enable the channel and open a ticket from one inbound customer message."""
    await set_setting(session, "channel.telegram.default_customer_user", "portal-default")
    await set_setting(session, "channel.telegram.consent_required", "0")
    await set_setting(session, "channel.telegram.enabled", "1")
    inbound = await process_update(
        session,
        factory,
        SysConfig(session),
        None,
        _text_message(chat_id, text_body, message_id=message_id),
        user_id=1,
    )
    await session.commit()
    return inbound


def _agent_article(body: str, **extra: Any) -> ArticleIn:
    return ArticleIn(
        sender_type="agent",
        is_visible_for_customer=True,
        subject="Re: test",
        body=body,
        channel="telegram",
        **extra,
    )


async def _article_count(session: AsyncSession, ticket_id: int) -> int:
    count = (
        await session.execute(
            text("SELECT COUNT(*) FROM article WHERE ticket_id = :tid"), {"tid": ticket_id}
        )
    ).scalar()
    await session.commit()
    return int(count or 0)


@pytest.mark.db
async def test_agent_reply_buttons_send_keyboard_and_store_them(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                inbound = await _telegram_ticket(session, factory, 4101, "Geht nicht")
                buttons = (
                    ButtonSpec("Ja, geht wieder", "resolve_yes"),
                    ButtonSpec("Nein", "resolve_no"),
                )
                gw = _FakeTelegramGateway()
                async with session.begin():
                    article_id = await deliver_agent_telegram_reply(
                        session,
                        SysConfig(session),
                        ticket_id=inbound["ticket_id"],
                        user_id=1,
                        article=_agent_article(
                            "Geht es wieder?", telegram=TelegramSendOptions(buttons=buttons)
                        ),
                        gateway=gw,
                    )

                assert [c[0] for c in gw.calls] == ["send_message"]
                sent = gw.calls[0][1]
                assert sent["reply_markup"] == {
                    "inline_keyboard": [
                        [{"text": "Ja, geht wieder", "callback_data": "tqb:0"}],
                        [{"text": "Nein", "callback_data": "tqb:1"}],
                    ]
                }
                assert sent["reply_to_message_id"] is None

                row = await get_by_article(session, article_id)
                assert row is not None
                assert row.direction == "out"
                assert row.chat_id == 4101
                assert row.message_id == sent["message_id"]
                assert row.extra_message_ids is None
                assert buttons_from_json(row.buttons_json) == list(buttons)
                await session.commit()
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_agent_reply_attachments_sent_before_text_and_stored(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                inbound = await _telegram_ticket(session, factory, 4102, "Schick mal")
                gw = _FakeTelegramGateway()
                async with session.begin():
                    article_id = await deliver_agent_telegram_reply(
                        session,
                        SysConfig(session),
                        ticket_id=inbound["ticket_id"],
                        user_id=1,
                        article=_agent_article(
                            "Hier die Unterlagen.",
                            attachments=[
                                ("screen.png", "image/png", b"png-bytes"),
                                ("rechnung.pdf", "application/pdf", b"%PDF-1.4"),
                            ],
                        ),
                        gateway=gw,
                    )

                assert [c[0] for c in gw.calls] == ["send_photo", "send_document", "send_message"]
                assert gw.calls[0][1]["filename"] == "screen.png"
                assert gw.calls[1][1]["content_type"] == "application/pdf"
                photo_id, doc_id, text_id = (c[1]["message_id"] for c in gw.calls)

                stored = (
                    await session.execute(
                        text(
                            "SELECT filename, content FROM article_data_mime_attachment"
                            " WHERE article_id = :aid ORDER BY id"
                        ),
                        {"aid": article_id},
                    )
                ).all()
                assert [(r[0], bytes(r[1])) for r in stored] == [
                    ("screen.png", b"png-bytes"),
                    ("rechnung.pdf", b"%PDF-1.4"),
                ]

                row = await get_by_article(session, article_id)
                assert row is not None
                assert row.message_id == text_id
                assert json.loads(row.extra_message_ids or "null") == [photo_id, doc_id]
                assert row.buttons_json is None
                await session.commit()
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_agent_reply_big_image_goes_out_as_document(mariadb_znuny_url: str) -> None:
    """Photos over Telegram's 10 MB photo limit (and non-photo image types)
    are sent as documents instead of failing."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                inbound = await _telegram_ticket(session, factory, 4103, "Bilder")
                gw = _FakeTelegramGateway()
                big = b"x" * (10 * 1024 * 1024 + 1)
                async with session.begin():
                    await deliver_agent_telegram_reply(
                        session,
                        SysConfig(session),
                        ticket_id=inbound["ticket_id"],
                        user_id=1,
                        article=_agent_article(
                            "Bilder",
                            attachments=[
                                ("gross.jpg", "image/jpeg", big),
                                ("klein.webp", "image/webp; name=klein.webp", b"w"),
                                ("anim.gif", "image/gif", b"g"),
                            ],
                        ),
                        gateway=gw,
                    )
                assert [c[0] for c in gw.calls] == [
                    "send_document",
                    "send_photo",
                    "send_document",
                    "send_message",
                ]
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_agent_reply_empty_body_last_attachment_carries_keyboard(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                inbound = await _telegram_ticket(
                    session, factory, 4104, "Foto bitte", message_id=77
                )
                gw = _FakeTelegramGateway()
                buttons = (ButtonSpec("Passt"),)
                async with session.begin():
                    article_id = await deliver_agent_telegram_reply(
                        session,
                        SysConfig(session),
                        ticket_id=inbound["ticket_id"],
                        user_id=1,
                        article=_agent_article(
                            "",
                            attachments=[
                                ("a.pdf", "application/pdf", b"a"),
                                ("b.png", "image/png", b"b"),
                            ],
                            telegram=TelegramSendOptions(
                                reply_to_article_id=inbound["article_id"], buttons=buttons
                            ),
                        ),
                        gateway=gw,
                    )

                assert [c[0] for c in gw.calls] == ["send_document", "send_photo"]
                first, last = gw.calls[0][1], gw.calls[1][1]
                # Quote on the first message sent, keyboard on the last one.
                assert first["reply_to_message_id"] == 77
                assert first["reply_markup"] is None
                assert last["reply_to_message_id"] is None
                assert last["reply_markup"] is not None

                body = (
                    await session.execute(
                        text("SELECT a_body FROM article_data_mime WHERE article_id = :aid"),
                        {"aid": article_id},
                    )
                ).scalar()
                assert body == "[Anhang]"

                row = await get_by_article(session, article_id)
                assert row is not None
                assert row.message_id == last["message_id"]
                assert json.loads(row.extra_message_ids or "null") == [first["message_id"]]
                assert row.reply_to_article_id == inbound["article_id"]
                assert buttons_from_json(row.buttons_json) == list(buttons)
                await session.commit()
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_agent_reply_quotes_mapped_customer_message(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                inbound = await _telegram_ticket(
                    session, factory, 4105, "Router blinkt rot", message_id=42
                )
                gw = _FakeTelegramGateway()
                async with session.begin():
                    article_id = await deliver_agent_telegram_reply(
                        session,
                        SysConfig(session),
                        ticket_id=inbound["ticket_id"],
                        user_id=1,
                        article=_agent_article(
                            "Zieh mal den Stecker.",
                            telegram=TelegramSendOptions(reply_to_article_id=inbound["article_id"]),
                        ),
                        gateway=gw,
                    )
                assert gw.calls[0][1]["reply_to_message_id"] == 42
                row = await get_by_article(session, article_id)
                assert row is not None
                assert row.reply_to_article_id == inbound["article_id"]
                await session.commit()
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_agent_reply_quote_of_unmapped_article_sends_without_quote(
    mariadb_znuny_url: str,
) -> None:
    """A customer message from before the map table existed has no row: the
    reply still goes out, just without ``reply_parameters``."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                inbound = await _telegram_ticket(session, factory, 4106, "Alt", message_id=43)
                await session.execute(
                    text("DELETE FROM tiqora_telegram_message WHERE article_id = :aid"),
                    {"aid": inbound["article_id"]},
                )
                await session.commit()
                gw = _FakeTelegramGateway()
                async with session.begin():
                    article_id = await deliver_agent_telegram_reply(
                        session,
                        SysConfig(session),
                        ticket_id=inbound["ticket_id"],
                        user_id=1,
                        article=_agent_article(
                            "Antwort",
                            telegram=TelegramSendOptions(reply_to_article_id=inbound["article_id"]),
                        ),
                        gateway=gw,
                    )
                assert gw.sent == [(4106, "Antwort")]
                assert gw.calls[0][1]["reply_to_message_id"] is None
                row = await get_by_article(session, article_id)
                assert row is not None
                assert row.reply_to_article_id == inbound["article_id"]
                await session.commit()
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_agent_reply_quote_of_other_ticket_rejected_before_send(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                mine = await _telegram_ticket(session, factory, 4107, "Meins")
                other = await _telegram_ticket(session, factory, 4108, "Fremd")
                gw = _FakeTelegramGateway()
                with pytest.raises(InvalidInput):
                    async with session.begin():
                        await deliver_agent_telegram_reply(
                            session,
                            SysConfig(session),
                            ticket_id=mine["ticket_id"],
                            user_id=1,
                            article=_agent_article(
                                "x",
                                telegram=TelegramSendOptions(
                                    reply_to_article_id=other["article_id"]
                                ),
                            ),
                            gateway=gw,
                        )
                assert gw.calls == []
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_agent_reply_text_failure_retracts_sent_photo(mariadb_znuny_url: str) -> None:
    """Attachments go out first; if the text then fails, the photo already in
    the customer's chat is deleted again and nothing is stored, so the agent
    can retry without the customer seeing half a reply twice."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                inbound = await _telegram_ticket(session, factory, 4109, "Halb")
                count_before = await _article_count(session, inbound["ticket_id"])
                gw = _FakeTelegramGateway(fail_on={"send_message"})
                with pytest.raises(TelegramDeliveryError):
                    async with session.begin():
                        await deliver_agent_telegram_reply(
                            session,
                            SysConfig(session),
                            ticket_id=inbound["ticket_id"],
                            user_id=1,
                            article=_agent_article(
                                "Text", attachments=[("p.jpg", "image/jpeg", b"jpg")]
                            ),
                            gateway=gw,
                        )
                photo_id = gw.calls[0][1]["message_id"]
                assert gw.deleted == [(4109, photo_id)]
                assert await _article_count(session, inbound["ticket_id"]) == count_before
                out_rows = (
                    await session.execute(
                        text(
                            "SELECT COUNT(*) FROM tiqora_telegram_message"
                            " WHERE ticket_id = :tid AND direction = 'out'"
                        ),
                        {"tid": inbound["ticket_id"]},
                    )
                ).scalar()
                assert out_rows == 0
                await session.commit()
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_agent_reply_retract_failure_still_raises_delivery_error(
    mariadb_znuny_url: str,
) -> None:
    """The cleanup delete is best effort: a refused delete must not mask the
    original send failure."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                inbound = await _telegram_ticket(session, factory, 4110, "Halb")
                gw = _FakeTelegramGateway(fail_on={"send_document"}, fail_delete=True)
                with pytest.raises(TelegramDeliveryError):
                    async with session.begin():
                        await deliver_agent_telegram_reply(
                            session,
                            SysConfig(session),
                            ticket_id=inbound["ticket_id"],
                            user_id=1,
                            article=_agent_article(
                                "Text",
                                attachments=[
                                    ("p.jpg", "image/jpeg", b"jpg"),
                                    ("d.pdf", "application/pdf", b"pdf"),
                                ],
                            ),
                            gateway=gw,
                        )
                assert [c[0] for c in gw.calls] == ["send_photo"]
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_agent_reply_store_failure_retracts_everything_sent(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sent but not stored is the other half-state: the customer would see a
    reply the agent's ticket does not show (and a retry would repeat it)."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                inbound = await _telegram_ticket(session, factory, 4111, "DB kaputt")

                async def _broken_add_article(*_a: Any, **_kw: Any) -> int:
                    raise RuntimeError("db gone")

                monkeypatch.setattr(
                    "tiqora.channels.telegram.outbound.add_article", _broken_add_article
                )
                gw = _FakeTelegramGateway()
                with pytest.raises(RuntimeError, match="db gone"):
                    async with session.begin():
                        await deliver_agent_telegram_reply(
                            session,
                            SysConfig(session),
                            ticket_id=inbound["ticket_id"],
                            user_id=1,
                            article=_agent_article(
                                "Text", attachments=[("p.jpg", "image/jpeg", b"jpg")]
                            ),
                            gateway=gw,
                        )
                sent_ids = [c[1]["message_id"] for c in gw.calls]
                assert len(sent_ids) == 2
                assert sorted(m for _chat, m in gw.deleted) == sorted(sent_ids)
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_agent_reply_empty_subject_uses_ticket_title(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                inbound = await _telegram_ticket(session, factory, 4112, "Titel hier")
                title = (
                    await session.execute(
                        text("SELECT title FROM ticket WHERE id = :tid"),
                        {"tid": inbound["ticket_id"]},
                    )
                ).scalar()
                await session.commit()
                async with session.begin():
                    article_id = await deliver_agent_telegram_reply(
                        session,
                        SysConfig(session),
                        ticket_id=inbound["ticket_id"],
                        user_id=1,
                        article=replace(_agent_article("Antwort"), subject="  "),
                        gateway=_FakeTelegramGateway(),
                    )
                subject = (
                    await session.execute(
                        text("SELECT a_subject FROM article_data_mime WHERE article_id = :aid"),
                        {"aid": article_id},
                    )
                ).scalar()
                assert title
                assert subject == title
                await session.commit()
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Inbound button-tap callback (Task 5)
# ---------------------------------------------------------------------------


async def _agent_question_with_buttons(
    session: AsyncSession,
    *,
    ticket_id: int,
    chat_id: int,
    message_id: int,
    buttons: tuple[ButtonSpec, ...],
) -> int:
    """Store an agent "question" article plus the outbound map row a real
    :func:`deliver_agent_telegram_reply` send would have left behind, with a
    caller-chosen ``message_id`` (button-tap tests need to control it
    directly, unlike ``_FakeTelegramGateway``/``_recording_gateway``, whose
    ``message_id`` is fixed)."""
    article_id = await add_article(
        session,
        ticket_id=ticket_id,
        article=_agent_article("Ist es jetzt wieder da?"),
        user_id=1,
        sysconfig=SysConfig(session),
    )
    await record_message(
        session,
        article_id=article_id,
        ticket_id=ticket_id,
        chat_id=chat_id,
        message_id=message_id,
        direction="out",
        buttons=list(buttons),
    )
    await session.commit()
    return article_id


async def _ticket_state_name(session: AsyncSession, ticket_id: int) -> str:
    row = (
        await session.execute(
            text(
                "SELECT ts.name FROM ticket t"
                " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                " WHERE t.id = :tid"
            ),
            {"tid": ticket_id},
        )
    ).first()
    return str(row[0]) if row is not None else ""


_RESOLVE_BUTTONS = (ButtonSpec("Ja", "resolve_yes"), ButtonSpec("Nein", "resolve_no"))


@pytest.mark.db
async def test_button_tap_resolve_yes_then_repeat_tap_is_noop(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                inbound = await _telegram_ticket(session, factory, 4601, "Internet down")
                ticket_id = inbound["ticket_id"]
                await _agent_question_with_buttons(
                    session,
                    ticket_id=ticket_id,
                    chat_id=4601,
                    message_id=777,
                    buttons=_RESOLVE_BUTTONS,
                )
                articles_before = await _article_count(session, ticket_id)

                gateway, calls = _recording_gateway()
                result = await process_update(
                    session,
                    factory,
                    SysConfig(session),
                    gateway,
                    _callback_update(777, 4601, 19601, data="tqb:0"),
                    user_id=1,
                )
                await session.commit()

                assert result["ticket_id"] == ticket_id
                assert result["button_action"] == "resolve_yes"
                new_article_id = result["article_id"]

                body_row = (
                    await session.execute(
                        text(
                            "SELECT a_body, a_from FROM article_data_mime WHERE article_id = :aid"
                        ),
                        {"aid": new_article_id},
                    )
                ).first()
                assert body_row is not None
                assert body_row[0] == "Ja"
                assert body_row[1] == "Ada Lovelace <4601@telegram.invalid>"

                sender_row = (
                    await session.execute(
                        text(
                            "SELECT ast.name FROM article a"
                            " JOIN article_sender_type ast ON ast.id = a.article_sender_type_id"
                            " WHERE a.id = :aid"
                        ),
                        {"aid": new_article_id},
                    )
                ).first()
                assert sender_row is not None
                assert sender_row[0] == "customer"

                assert await _ticket_state_name(session, ticket_id) == "closed successful"
                assert await _article_count(session, ticket_id) == articles_before + 1

                map_row = await get_by_message(session, 4601, 777)
                assert map_row is not None
                assert map_row.answered_button == 0
                assert map_row.answered_at is not None

                answered = await get_by_article(session, new_article_id)
                assert answered is not None
                assert answered.message_id is None
                assert answered.reply_to_article_id == map_row.article_id

                answer_calls = [c for c in calls if c[0] == "answerCallbackQuery"]
                assert len(answer_calls) == 1
                assert answer_calls[0][1]["text"] == "Danke!"
                edit_calls = [c for c in calls if c[0] == "editMessageReplyMarkup"]
                assert len(edit_calls) == 1
                assert edit_calls[0][1]["reply_markup"] == {"inline_keyboard": []}
                assert edit_calls[0][1]["message_id"] == 777

                # Second tap on the very same button: no new article, no
                # state change -- just the "already answered" callback answer.
                repeat = await process_update(
                    session,
                    factory,
                    SysConfig(session),
                    gateway,
                    _callback_update(777, 4601, 19601, data="tqb:0"),
                    user_id=1,
                )
                await session.commit()

                assert repeat == {"skipped": "already_answered"}
                assert await _article_count(session, ticket_id) == articles_before + 1
                answer_calls = [c for c in calls if c[0] == "answerCallbackQuery"]
                assert answer_calls[-1][1]["text"] == "Schon beantwortet 👍"
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_button_tap_resolve_no_reopens_ticket(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                inbound = await _telegram_ticket(session, factory, 4602, "Immer noch kaputt")
                ticket_id = inbound["ticket_id"]
                assert await _ticket_state_name(session, ticket_id) != "open"
                await _agent_question_with_buttons(
                    session,
                    ticket_id=ticket_id,
                    chat_id=4602,
                    message_id=778,
                    buttons=_RESOLVE_BUTTONS,
                )

                gateway, calls = _recording_gateway()
                result = await process_update(
                    session,
                    factory,
                    SysConfig(session),
                    gateway,
                    _callback_update(778, 4602, 19602, data="tqb:1"),
                    user_id=1,
                )
                await session.commit()

                assert result["button_action"] == "resolve_no"
                assert await _ticket_state_name(session, ticket_id) == "open"

                body = (
                    await session.execute(
                        text("SELECT a_body FROM article_data_mime WHERE article_id = :aid"),
                        {"aid": result["article_id"]},
                    )
                ).scalar()
                assert body == "Nein"
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_button_tap_unknown_message_answers_invalid_and_creates_nothing(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                article_count_before = (
                    await session.execute(text("SELECT COUNT(*) FROM article"))
                ).scalar()

                gateway, calls = _recording_gateway()
                result = await process_update(
                    session,
                    factory,
                    SysConfig(session),
                    gateway,
                    _callback_update(4603, 9603, 19603, data="tqb:0"),
                    user_id=1,
                )
                await session.commit()

                assert result == {"skipped": "unknown_button"}
                answer_calls = [c for c in calls if c[0] == "answerCallbackQuery"]
                assert len(answer_calls) == 1
                assert answer_calls[0][1]["text"] == "Diese Auswahl ist nicht mehr gültig."

                article_count_after = (
                    await session.execute(text("SELECT COUNT(*) FROM article"))
                ).scalar()
                assert article_count_after == article_count_before
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_button_tap_index_out_of_range_answers_invalid(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _snapshot_max_ids(session)
            try:
                inbound = await _telegram_ticket(session, factory, 4604, "Frage")
                await _agent_question_with_buttons(
                    session,
                    ticket_id=inbound["ticket_id"],
                    chat_id=4604,
                    message_id=779,
                    buttons=_RESOLVE_BUTTONS,
                )

                gateway, calls = _recording_gateway()
                result = await process_update(
                    session,
                    factory,
                    SysConfig(session),
                    gateway,
                    _callback_update(779, 4604, 19604, data="tqb:5"),
                    user_id=1,
                )
                await session.commit()

                assert result == {"skipped": "unknown_button"}
                answer_calls = [c for c in calls if c[0] == "answerCallbackQuery"]
                assert answer_calls[-1][1]["text"] == "Diese Auswahl ist nicht mehr gültig."
            finally:
                await _cleanup_new_rows(session, before)
    finally:
        await engine.dispose()
