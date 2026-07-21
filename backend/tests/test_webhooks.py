"""Tests for webhook delivery: signature correctness, retry/backoff, event
filtering, and admin CRUD against the real DB (mariadb/postgres testcontainers).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraWebhook
from tiqora.worker.webhooks import dispatch_webhooks, sign_payload, webhook_matches_event

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://")
    return sync_url


def _seed_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()


async def _clear_webhooks(factory: async_sessionmaker[AsyncSession]) -> None:
    """The mariadb/postgres testcontainer fixtures are session-scoped and
    shared across every test in this module — dispatch tests must not see
    webhook rows left behind by earlier tests."""
    async with factory() as session, session.begin():
        await session.execute(text("DELETE FROM tiqora_webhook"))


def test_webhook_matches_event() -> None:
    assert webhook_matches_event("[]", "TicketCreate") is True
    assert webhook_matches_event('["*"]', "TicketCreate") is True
    assert webhook_matches_event('["TicketCreate"]', "TicketCreate") is True
    assert webhook_matches_event('["ArticleCreate"]', "TicketCreate") is False
    assert webhook_matches_event("not-json", "TicketCreate") is False


def test_sign_payload_is_correct_hmac_sha256() -> None:
    body = b'{"event":"TicketCreate"}'
    secret = "s3cret"
    sig = sign_payload(secret, body)
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert sig == f"sha256={expected}"
    # Wrong secret must not match
    assert sig != sign_payload("other-secret", body)


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_webhook_crud_roundtrip(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _seed_tables(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        wh = TiqoraWebhook(
            name="test-hook",
            url="https://example.com/hook",
            secret="s3cret",
            events=json.dumps(["TicketCreate"]),
            valid=True,
        )
        session.add(wh)
        await session.commit()
        await session.refresh(wh)
        wh_id = wh.id

    async with factory() as session:
        row = await session.get(TiqoraWebhook, wh_id)
        assert row is not None
        assert row.name == "test-hook"
        assert json.loads(row.events) == ["TicketCreate"]

        row.valid = False
        await session.commit()

    async with factory() as session:
        row2 = await session.get(TiqoraWebhook, wh_id)
        assert row2 is not None
        assert row2.valid is False

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_dispatch_delivers_signed_payload_and_filters_events(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _seed_tables(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _clear_webhooks(factory)
    secret = "unit-test-secret"

    async with factory() as session:
        session.add(
            TiqoraWebhook(
                name=f"hook-{uuid.uuid4().hex[:8]}",
                url="https://example.com/tiqora",
                secret=secret,
                events=json.dumps(["TicketCreate"]),
                valid=True,
            )
        )
        await session.commit()

    received: list[httpx.Request] = []

    def handler(request_: httpx.Request) -> httpx.Response:
        received.append(request_)
        return httpx.Response(200, json={"ok": True})

    settings = Settings(webhook_max_attempts=3, webhook_timeout_seconds=5.0)
    rows = [
        ("TicketCreate", 42, json.dumps({"tn": "20260719000001"})),
        ("ArticleCreate", 42, None),  # not subscribed -> must not be delivered
    ]
    result = await dispatch_webhooks(
        rows,
        settings=settings,
        session_factory=factory,
        transport=httpx.MockTransport(handler),
    )

    assert result == {"delivered": 1, "failed": 0}
    assert len(received) == 1
    req = received[0]
    body = req.content
    sig_header = req.headers["x-tiqora-signature"]
    expected = sign_payload(secret, body)
    assert sig_header == expected

    parsed = json.loads(body)
    assert parsed["schema_version"] == 1
    assert parsed["event"] == "TicketCreate"
    assert parsed["ticket_id"] == 42
    assert parsed["payload"] == {"tn": "20260719000001"}

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_dispatch_retries_and_counts_final_failure(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _seed_tables(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _clear_webhooks(factory)

    async with factory() as session:
        session.add(
            TiqoraWebhook(
                name=f"hook-{uuid.uuid4().hex[:8]}",
                url="https://example.com/always-down",
                secret="s3cret",
                events=json.dumps([]),  # subscribes to everything
                valid=True,
            )
        )
        await session.commit()

    attempts = 0

    def handler(request_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, json={"error": "boom"})

    # No real sleeping between retries in the test.
    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("tiqora.worker.webhooks.asyncio.sleep", _no_sleep)

    settings = Settings(webhook_max_attempts=3, webhook_timeout_seconds=5.0)
    result = await dispatch_webhooks(
        [("TicketCreate", 1, None)],
        settings=settings,
        session_factory=factory,
        transport=httpx.MockTransport(handler),
    )

    assert attempts == 3
    assert result == {"delivered": 0, "failed": 1}

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_dispatch_blocks_private_url_ssrf(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """H-4 / H-03: webhook delivery must not POST to loopback/private targets."""
    sync_url: str = request.getfixturevalue(url_fixture)
    _seed_tables(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _clear_webhooks(factory)

    async with factory() as session:
        session.add(
            TiqoraWebhook(
                name=f"ssrf-{uuid.uuid4().hex[:8]}",
                url="http://127.0.0.1:9/steal",
                secret="s3cret",
                events=json.dumps([]),
                valid=True,
            )
        )
        await session.commit()

    received: list[httpx.Request] = []

    def handler(request_: httpx.Request) -> httpx.Response:
        received.append(request_)
        return httpx.Response(200)

    settings = Settings(webhook_max_attempts=2, webhook_timeout_seconds=5.0)
    result = await dispatch_webhooks(
        [("TicketCreate", 1, None)],
        settings=settings,
        session_factory=factory,
        transport=httpx.MockTransport(handler),
    )

    assert result == {"delivered": 0, "failed": 1}
    assert received == []  # never connected

    await engine.dispose()
