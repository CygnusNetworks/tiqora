"""tiqora.ai.limits: budget status, and announcing an exhausted limit once
per window (outbox row for webhooks + admin SSE notice).

Seed ids use the 995x range. The module deletes everything it commits
(``tests._row_cleanup``) so it passes on its own and under
``TIQORA_STRICT_DB_LEAKS=1``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests._row_cleanup import cleanup_module
from tiqora.ai import limits as ai_limits
from tiqora.ai import policies as ai_policies
from tiqora.ai import providers as ai_providers
from tiqora.ai import usage as usage_service
from tiqora.ai.limits import (
    EVENT_AI_LIMIT_REACHED,
    KIND_PROVIDER_COST,
    KIND_QUEUE_TOKENS_DAY,
    announce_exhausted_limits,
    collect_limits,
)
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings

pytestmark = pytest.mark.db

QUEUE_ID = 9951
PROVIDER_NAME = "limits-test-provider-9951"
NOW = datetime(2024, 6, 1, 12, 0, 0)


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _wipe(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tiqora_ai_usage WHERE queue_id = :q"), {"q": QUEUE_ID})
            conn.execute(
                text(
                    "DELETE FROM tiqora_ai_usage WHERE provider_id IN"
                    " (SELECT id FROM tiqora_llm_provider WHERE name = :n)"
                ),
                {"n": PROVIDER_NAME},
            )
            conn.execute(
                text("DELETE FROM tiqora_ai_queue_policy WHERE queue_id = :q"), {"q": QUEUE_ID}
            )
            conn.execute(
                text("DELETE FROM tiqora_llm_provider WHERE name = :n"), {"n": PROVIDER_NAME}
            )
            conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key.like("ai.limit_alert.%")))
    finally:
        engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _cleanup(mariadb_znuny_url: str) -> Iterator[None]:
    engine = create_engine(mariadb_znuny_url)
    TiqoraBase.metadata.create_all(engine)
    engine.dispose()
    _wipe(mariadb_znuny_url)
    yield from cleanup_module(mariadb_znuny_url)
    _wipe(mariadb_znuny_url)


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture admin SSE notices instead of talking to Redis."""
    calls: list[str] = []

    async def _fake_publish(_client: Any) -> None:
        calls.append("ai_limit_changed")

    monkeypatch.setattr("tiqora.events.pubsub.publish_ai_limit_changed", _fake_publish)
    monkeypatch.setattr("tiqora.events.pubsub.get_pubsub_redis", lambda *_a, **_k: None)
    return calls


def _seed_queue(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :name, 1, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": QUEUE_ID, "name": f"LimitsQueue{QUEUE_ID}", "t": NOW},
            )
    finally:
        engine.dispose()


def _ours(statuses: list[ai_limits.LimitStatus], kind: str) -> list[ai_limits.LimitStatus]:
    if kind == KIND_QUEUE_TOKENS_DAY:
        return [s for s in statuses if s.kind == kind and s.subject_id == QUEUE_ID]
    return [s for s in statuses if s.kind == kind and s.subject_name == PROVIDER_NAME]


async def _outbox_rows(session: Any) -> list[dict[str, Any]]:
    rows = await session.execute(
        text("SELECT ticket_id, payload FROM tiqora_event_outbox WHERE event_type = :e"),
        {"e": EVENT_AI_LIMIT_REACHED},
    )
    return [{"ticket_id": int(r[0]), **json.loads(r[1])} for r in rows.all()]


def test_windows_are_calendar_aligned() -> None:
    w = ai_limits._windows(datetime(2026, 12, 31, 18, 30))  # a Thursday
    assert (w["day"].start, w["day"].end) == (datetime(2026, 12, 31), datetime(2027, 1, 1))
    assert (w["week"].start, w["week"].end) == (datetime(2026, 12, 28), datetime(2027, 1, 4))
    assert (w["month"].start, w["month"].end) == (datetime(2026, 12, 1), datetime(2027, 1, 1))


async def test_queue_budget_status_and_single_announcement(
    mariadb_znuny_url: str, published: list[str]
) -> None:
    _seed_queue(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await ai_policies.create_queue_policy(
                session, change_by=1, queue_id=QUEUE_ID, budget_tokens_day=100
            )
            # Under budget: listed, not exhausted, nothing announced. Summary
            # tokens do not count against the queue budget.
            await usage_service.record_usage(
                session, queue_id=QUEUE_ID, feature="summary", prompt_tokens=500
            )
            await usage_service.record_usage(
                session, queue_id=QUEUE_ID, feature="triage", prompt_tokens=60
            )
            [status] = _ours(await collect_limits(session), KIND_QUEUE_TOKENS_DAY)
            assert (status.used, status.limit, status.exhausted) == (60.0, 100.0, False)
            assert status.subject_name == f"LimitsQueue{QUEUE_ID}"
            assert not [r for r in await _outbox_rows(session) if r["subject_id"] == QUEUE_ID]

            # This call crosses the line → announced right away, with its ticket.
            await usage_service.record_usage(
                session,
                queue_id=QUEUE_ID,
                ticket_id=4711,
                feature="auto_reply",
                prompt_tokens=30,
                completion_tokens=20,
            )
            [status] = _ours(await collect_limits(session), KIND_QUEUE_TOKENS_DAY)
            assert status.exhausted
            rows = [r for r in await _outbox_rows(session) if r["subject_id"] == QUEUE_ID]
            assert len(rows) == 1
            assert rows[0]["ticket_id"] == 4711
            assert rows[0]["kind"] == KIND_QUEUE_TOKENS_DAY
            assert rows[0]["used"] == 110.0
            assert published

            # Further calls in the same window do not announce again.
            published.clear()
            await usage_service.record_usage(
                session, queue_id=QUEUE_ID, feature="auto_reply", prompt_tokens=10
            )
            assert await announce_exhausted_limits(session) == []
            rows = [r for r in await _outbox_rows(session) if r["subject_id"] == QUEUE_ID]
            assert len(rows) == 1
            assert not published
    finally:
        await engine.dispose()


async def test_provider_cost_budget_is_reported_per_window(
    mariadb_znuny_url: str, published: list[str]
) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            provider = await ai_providers.create_provider(
                session,
                settings=get_settings(),
                change_by=1,
                name=PROVIDER_NAME,
                kind="openai_compat",
                base_url="https://llm.example/v1",
                api_key=None,
                price_currency="USD",
                budget_cost_day=0.5,
                budget_cost_month=10.0,
            )
            await usage_service.record_usage(
                session, provider_id=provider.id, feature="summary", cost_hint=0.75
            )
            statuses = _ours(await collect_limits(session), KIND_PROVIDER_COST)
            by_window = {s.window: s for s in statuses}
            assert set(by_window) == {"day", "month"}  # no weekly cap configured
            assert by_window["day"].exhausted and by_window["day"].currency == "USD"
            assert not by_window["month"].exhausted
            rows = [
                r
                for r in await _outbox_rows(session)
                if r["kind"] == KIND_PROVIDER_COST and r["subject_name"] == PROVIDER_NAME
            ]
            assert [r["window"] for r in rows] == ["day"]
            assert rows[0]["ticket_id"] == 0  # the call had no ticket
    finally:
        await engine.dispose()
