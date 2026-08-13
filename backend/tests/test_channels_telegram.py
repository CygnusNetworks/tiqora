"""Tests for the Telegram channel: gateway (httpx.MockTransport, token
scrubbing) and DB-backed inbound processing (ticket/article creation,
per-chat ticket continuity, contact upsert)."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.telegram.gateway import TelegramApiError, TelegramGateway
from tiqora.channels.telegram.service import process_update
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraTelegramContact
from tiqora.domain.settings_store import set_setting
from tiqora.znuny.sysconfig import SysConfig


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
            (await session.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {table}"))).scalar()
            or 0
        )
        for table in _WRITE_TABLES
    }


async def _cleanup_new_rows(session: AsyncSession, before: dict[str, int]) -> None:
    for table in _WRITE_TABLES:
        await session.execute(text(f"DELETE FROM {table} WHERE id > :b"), {"b": before[table]})
    # tiqora_settings is keyed on `key` (a reserved word -- must be quoted),
    # not an autoincrement id.
    await session.execute(
        text("DELETE FROM tiqora_settings WHERE `key` LIKE 'channel.telegram.%'")
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
