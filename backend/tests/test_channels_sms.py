"""Tests for the SMS channel: pure-function unit tests (phone normalization,
gateway signing/dispatch via httpx.MockTransport) and DB-backed tests
(inbound creates a ticket, a follow-up reply appends to it, channel row
registration, phone->customer_user resolution)."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.common import (
    ensure_channel_row,
    normalize_phone,
    resolve_customer_by_phone,
    verify_shared_secret,
)
from tiqora.channels.sms.gateway import GenericHttpSmsGateway, sign_payload
from tiqora.channels.sms.service import process_inbound_sms
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.znuny.sysconfig import SysConfig


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


# ---------------------------------------------------------------------------
# Pure-function unit tests (no DB, no network)
# ---------------------------------------------------------------------------


def test_normalize_phone_strips_formatting() -> None:
    assert normalize_phone("+49 30 123-456") == "4930123456"
    assert normalize_phone(None) == ""
    assert normalize_phone("") == ""


def test_verify_shared_secret() -> None:
    assert verify_shared_secret("s3cret", "s3cret") is True
    assert verify_shared_secret("s3cret", "wrong") is False
    assert verify_shared_secret("s3cret", None) is False
    assert verify_shared_secret(None, "s3cret") is False


def test_sign_payload_is_hmac_sha256() -> None:
    sig = sign_payload("secret", b"body")
    assert sig.startswith("sha256=")
    assert len(sig) == len("sha256=") + 64


async def test_generic_http_gateway_sends_signed_post() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = GenericHttpSmsGateway(
        "https://gw.example.com/send", shared_secret="topsecret", client=client
    )
    await gateway.send(to="+491701234567", body="hello")
    await client.aclose()

    assert captured["url"] == "https://gw.example.com/send"
    assert captured["body"] == {"to": "+491701234567", "body": "hello"}
    assert "x-tiqora-signature" in captured["headers"]  # type: ignore[operator]


async def test_generic_http_gateway_raises_on_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = GenericHttpSmsGateway("https://gw.example.com/send", client=client)
    with pytest.raises(httpx.HTTPStatusError):
        await gateway.send(to="+491701234567", body="hello")
    await client.aclose()


# ---------------------------------------------------------------------------
# DB-backed tests
# ---------------------------------------------------------------------------


def _ensure_tiqora_tables(sync_url: str) -> None:
    """``tiqora_*`` tables (settings, event outbox, cache invalidation, ...)
    are Alembic-managed in prod; the Znuny-only DDL fixture used by these
    tests doesn't include them, so create_ticket()/add_article() need them
    created ad hoc, same approach as test_webhooks.py."""
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()


async def _insert_customer_user(session: AsyncSession, login: str, phone: str) -> None:
    await session.execute(
        text(
            "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
            " phone, pw, valid_id, create_time, create_by, change_time, change_by)"
            " VALUES (:login, :email, :login, 'Test', 'Customer', :phone, 'x', 1,"
            " current_timestamp, 1, current_timestamp, 1)"
        ),
        {"login": login, "email": f"{login}@example.com", "phone": phone},
    )


@pytest.mark.db
async def test_ensure_channel_row_creates_and_is_idempotent(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            first_id = await ensure_channel_row(session, "SMS", "Tiqora::CommunicationChannel::SMS")
            second_id = await ensure_channel_row(
                session, "SMS", "Tiqora::CommunicationChannel::SMS"
            )
            await session.commit()
            assert first_id == second_id
            row = (
                await session.execute(
                    text("SELECT module FROM communication_channel WHERE id = :id"),
                    {"id": first_id},
                )
            ).first()
            assert row is not None
            assert row[0] == "Tiqora::CommunicationChannel::SMS"
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_resolve_customer_by_phone_matches_normalized_suffix(
    mariadb_znuny_url: str,
) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _insert_customer_user(session, "smstestuser1", "+49 170 1234567")
            await session.commit()

            customer_no, login = await resolve_customer_by_phone(session, "0170-1234567")
            assert login == "smstestuser1"
            assert customer_no == "smstestuser1"

            customer_no2, login2 = await resolve_customer_by_phone(session, "+1 555 000 0000")
            assert login2 is None
            assert customer_no2 is None
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_inbound_sms_creates_ticket_then_followup_appends(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _insert_customer_user(session, "smstestuser2", "+49 170 9998888")
            await session.commit()

            sysconfig = SysConfig(session)
            first = await process_inbound_sms(
                session,
                factory,
                sysconfig,
                from_number="+491709998888",
                to_number="+4930000000",
                body="My printer is broken",
                user_id=1,
            )
            await session.commit()
            assert first.created is True
            assert first.customer_user_id == "smstestuser2"

            second = await process_inbound_sms(
                session,
                factory,
                sysconfig,
                from_number="+491709998888",
                to_number="+4930000000",
                body="Still broken, any update?",
                user_id=1,
            )
            await session.commit()
            assert second.created is False
            assert second.ticket_id == first.ticket_id
            assert second.article_id != first.article_id

            count_row = (
                await session.execute(
                    text("SELECT COUNT(*) FROM article WHERE ticket_id = :tid"),
                    {"tid": first.ticket_id},
                )
            ).first()
            assert count_row is not None
            assert int(count_row[0]) == 2
    finally:
        await engine.dispose()
