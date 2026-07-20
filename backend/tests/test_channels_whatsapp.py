"""Tests for the WhatsApp channel: signature verification (unit), gateway
send/media-download via httpx.MockTransport (unit), and DB-backed webhook
processing (inbound text creates a ticket, media becomes an attachment)."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.whatsapp.gateway import WhatsAppGateway
from tiqora.channels.whatsapp.service import (
    process_webhook_payload,
    verify_webhook_signature,
)
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.znuny.sysconfig import SysConfig


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _ensure_tiqora_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()


# ---------------------------------------------------------------------------
# Signature verification (unit)
# ---------------------------------------------------------------------------


def test_verify_webhook_signature_valid() -> None:
    secret = "app-secret"
    body = b'{"object":"whatsapp_business_account"}'
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(secret, body, f"sha256={digest}") is True


def test_verify_webhook_signature_invalid() -> None:
    secret = "app-secret"
    body = b'{"object":"whatsapp_business_account"}'
    assert verify_webhook_signature(secret, body, "sha256=deadbeef") is False
    assert verify_webhook_signature(secret, body, None) is False
    assert verify_webhook_signature(None, body, "sha256=deadbeef") is False
    assert verify_webhook_signature(secret, body, "not-prefixed") is False


# ---------------------------------------------------------------------------
# Gateway (unit, MockTransport)
# ---------------------------------------------------------------------------


async def test_gateway_send_text_returns_message_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-token"
        body = json.loads(request.content)
        assert body["to"] == "491701234567"
        assert body["type"] == "text"
        return httpx.Response(200, json={"messages": [{"id": "wamid.ABC"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gw = WhatsAppGateway(phone_number_id="12345", access_token="test-token", client=client)
    msg_id = await gw.send_text(to="491701234567", body="hi")
    await client.aclose()
    assert msg_id == "wamid.ABC"


async def test_gateway_download_media_two_step() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url).endswith("/media-id-1"):
            return httpx.Response(
                200, json={"url": "https://cdn.example.com/file", "mime_type": "image/png"}
            )
        return httpx.Response(200, content=b"binarydata")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gw = WhatsAppGateway(phone_number_id="12345", access_token="test-token", client=client)
    content, mime_type = await gw.download_media("media-id-1")
    await client.aclose()
    assert content == b"binarydata"
    assert mime_type == "image/png"
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# DB-backed webhook processing
# ---------------------------------------------------------------------------


async def _insert_customer_user(session: AsyncSession, login: str, phone: str) -> None:
    await session.execute(
        text(
            "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
            " mobile, pw, valid_id, create_time, create_by, change_time, change_by)"
            " VALUES (:login, :email, :login, 'Test', 'Customer', :phone, 'x', 1,"
            " current_timestamp, 1, current_timestamp, 1)"
        ),
        {"login": login, "email": f"{login}@example.com", "phone": phone},
    )


def _text_message_payload(wa_id: str, body: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": wa_id,
                                    "id": "wamid.1",
                                    "type": "text",
                                    "text": {"body": body},
                                }
                            ]
                        },
                        "field": "messages",
                    }
                ]
            }
        ],
    }


@pytest.mark.db
async def test_inbound_text_creates_ticket(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _insert_customer_user(session, "watestuser1", "491701112222")
            await session.commit()

            sysconfig = SysConfig(session)
            payload = _text_message_payload("491701112222", "Need help with my order")
            results = await process_webhook_payload(
                session, factory, sysconfig, None, payload, user_id=1
            )
            await session.commit()

            assert len(results) == 1
            assert results[0].created is True
            assert results[0].message_type == "text"

            row = (
                await session.execute(
                    text("SELECT a_body FROM article_data_mime WHERE article_id = :aid"),
                    {"aid": results[0].article_id},
                )
            ).first()
            assert row is not None
            assert row[0] == "Need help with my order"
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_inbound_media_stores_attachment(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/media-xyz"):
            return httpx.Response(
                200, json={"url": "https://cdn.example.com/img", "mime_type": "image/jpeg"}
            )
        return httpx.Response(200, content=b"\xff\xd8\xff\xd9")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = WhatsAppGateway(phone_number_id="1", access_token="t", client=client)

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "491703334444",
                                    "id": "wamid.2",
                                    "type": "image",
                                    "image": {"id": "media-xyz", "caption": "see attached"},
                                }
                            ]
                        },
                        "field": "messages",
                    }
                ]
            }
        ],
    }
    try:
        async with factory() as session:
            sysconfig = SysConfig(session)
            results = await process_webhook_payload(
                session, factory, sysconfig, gateway, payload, user_id=1
            )
            await session.commit()

            assert len(results) == 1
            assert results[0].message_type == "image"

            att = (
                await session.execute(
                    text(
                        "SELECT content, content_type FROM article_data_mime_attachment"
                        " WHERE article_id = :aid"
                    ),
                    {"aid": results[0].article_id},
                )
            ).first()
            assert att is not None
            assert att[1] == "image/jpeg"
    finally:
        await client.aclose()
        await engine.dispose()
