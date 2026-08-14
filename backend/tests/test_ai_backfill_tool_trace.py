"""DB + unit tests for tiqora.ai.backfill_tool_trace (backfill CLI: plan
"Tool-Trace: Chat-Darstellung ... + Backfill alter Artikel", part 3).

Seed ids use the 96xx range via ``_seed_ticket`` (shared helper, see
``test_ai_runtime.py``'s module docstring) with ``ns`` in 50-53 — the highest
``ns`` used elsewhere in the suite is 91, so this range is free.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.test_ai_runtime import (
    ScriptedLlm,
    _mysql_async,
    _propose_response,
    _seed_ticket,
    _setup_policy,
)
from tiqora.ai.audit import FEATURE_AUTO_REPLY
from tiqora.ai.backfill_tool_trace import run_backfill
from tiqora.ai.models import AUTONOMY_FULL, SOURCE_AUTO, TiqoraAiArticleOrigin, TiqoraAiAuditLog
from tiqora.ai.runtime import TRIGGER_AUTO, run_ticket_agent
from tiqora.config import get_settings

pytestmark = pytest.mark.db


async def _null_out_trace(sync_url: str, *, article_id: int) -> None:
    """Simulate a pre-feature origin row: strip the trace/run_id a real run
    just stamped so the backfill has something to reconstruct."""
    from sqlalchemy import create_engine

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE tiqora_ai_article_origin SET tool_trace_json = NULL, run_id = NULL "
                "WHERE article_id = :aid"
            ),
            {"aid": article_id},
        )
    engine.dispose()


async def _run_auto_send(mariadb_znuny_url: str, *, ns: int, run_id: str) -> dict[str, Any]:
    seed = _seed_ticket(mariadb_znuny_url, ns=ns)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed, autonomy=AUTONOMY_FULL, enabled_auto_reply=True)

        llm = ScriptedLlm([_propose_response("reply", "Backfill-bound answer.")])
        async with factory() as session:
            result = await run_ticket_agent(
                session,
                settings=settings,
                llm=llm,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_AUTO,
                acting_user_id=None,
                run_id=run_id,
            )
        assert result.status == "sent"
        seed["article_id"] = result.article_id
    finally:
        await engine.dispose()
    return seed


async def test_backfill_dry_run_reports_without_writing(mariadb_znuny_url: str) -> None:
    """A single-round auto-send (the scripted LLM proposes on its first
    call) has exactly one audit row, and that row's ``request_json`` is the
    *input* sent to the model — the round's own tool result (recorded by the
    runtime AFTER the audit-logged call returns) is never in it. So the
    reconstructed trace is legitimately empty here; see
    ``test_backfill_reconstructs_multi_round_tool_calls`` for the case where
    a prior round's tool result IS recoverable. This test's job is the
    dry-run contract: correlate + report, write nothing."""
    seed = await _run_auto_send(mariadb_znuny_url, ns=50, run_id="run-50")
    await _null_out_trace(mariadb_znuny_url, article_id=seed["article_id"])

    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            result = await run_backfill(session, dry_run=True, ticket_id=seed["ticket_id"])
            await session.commit()  # no-op: dry-run mutates nothing

        assert len(result.written) == 1
        item = result.written[0]
        assert item.article_id == seed["article_id"]
        assert item.run_id == "run-50"
        assert item.tool_call_count == 0

        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT tool_trace_json, run_id FROM tiqora_ai_article_origin "
                        "WHERE article_id = :aid"
                    ),
                    {"aid": seed["article_id"]},
                )
            ).first()
            assert row is not None
            assert row[0] is None
            assert row[1] is None
    finally:
        await engine.dispose()


async def test_backfill_writes_trace_and_run_id(mariadb_znuny_url: str) -> None:
    seed = await _run_auto_send(mariadb_znuny_url, ns=51, run_id="run-51")
    await _null_out_trace(mariadb_znuny_url, article_id=seed["article_id"])

    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            result = await run_backfill(session, dry_run=False, ticket_id=seed["ticket_id"])
            await session.commit()

        assert len(result.written) == 1
        assert result.written[0].run_id == "run-51"

        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT tool_trace_json, run_id FROM tiqora_ai_article_origin "
                        "WHERE article_id = :aid"
                    ),
                    {"aid": seed["article_id"]},
                )
            ).first()
            assert row is not None
            assert row[1] == "run-51"
            # Origin previously had tool_trace_json IS NULL; the backfill
            # writes even an empty reconstruction (valid JSON "[]"), same as
            # the runtime itself does for a trace-less run (plan: "Identity-
            # Artikel hat ggf. leeren Trace -> ok").
            assert json.loads(row[0]) == []
    finally:
        await engine.dispose()


async def test_backfill_reconstructs_multi_round_tool_calls(mariadb_znuny_url: str) -> None:
    """A prior round's tool result IS recoverable: it was appended to
    ``messages`` before the *next* chat() call, so it shows up in that next
    call's audit-logged ``request_json``. Modeled directly against
    ``tiqora_ai_audit_log``/``tiqora_ai_article_origin`` (no real multi-round
    tool available in the scripted-LLM test fixtures) to isolate the
    correlation + extraction logic from the runtime's tool-calling loop."""
    seed = _seed_ticket(mariadb_znuny_url, ns=54)
    origin_created = datetime(2026, 8, 14, 11, 0, 0)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(
                TiqoraAiArticleOrigin(
                    article_id=seed["customer_article_id"],
                    source=SOURCE_AUTO,
                    queue_id=seed["queue_id"],
                    service_user_id=seed["agent_id"],
                    tool_trace_json=None,
                    created=origin_created,
                )
            )
            # Round 1's audit row: input has no tool messages yet.
            session.add(
                TiqoraAiAuditLog(
                    ts=origin_created - timedelta(seconds=3),
                    run_id="run-54",
                    feature=FEATURE_AUTO_REPLY,
                    ticket_id=seed["ticket_id"],
                    queue_id=seed["queue_id"],
                    request_json=json.dumps({"messages": [{"role": "system", "content": "sys"}]}),
                )
            )
            # Round 2's audit row (closest to origin.created): input now
            # carries round 1's tool result — this is what gets extracted.
            session.add(
                TiqoraAiAuditLog(
                    ts=origin_created - timedelta(seconds=1),
                    run_id="run-54",
                    feature=FEATURE_AUTO_REPLY,
                    ticket_id=seed["ticket_id"],
                    queue_id=seed["queue_id"],
                    request_json=json.dumps(
                        {
                            "messages": [
                                {"role": "system", "content": "sys"},
                                {
                                    "role": "tool",
                                    "tool_call_id": "call_1",
                                    "name": "kb_search",
                                    "content": "3 Treffer",
                                },
                            ]
                        }
                    ),
                )
            )
            await session.commit()

        async with factory() as session:
            result = await run_backfill(session, dry_run=False, ticket_id=seed["ticket_id"])
            await session.commit()

        assert len(result.written) == 1
        item = result.written[0]
        assert item.run_id == "run-54"
        assert item.tool_call_count == 1

        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT tool_trace_json FROM tiqora_ai_article_origin "
                        "WHERE article_id = :aid"
                    ),
                    {"aid": seed["customer_article_id"]},
                )
            ).scalar_one()
            trace = json.loads(row)
            assert trace == [
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "name": "kb_search",
                    "content": "3 Treffer",
                }
            ]
    finally:
        await engine.dispose()


async def test_backfill_skips_when_no_audit_rows(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=52)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(
                TiqoraAiArticleOrigin(
                    article_id=seed["customer_article_id"],
                    source=SOURCE_AUTO,
                    queue_id=seed["queue_id"],
                    service_user_id=seed["agent_id"],
                    tool_trace_json=None,
                )
            )
            await session.commit()

        async with factory() as session:
            result = await run_backfill(session, dry_run=False, ticket_id=seed["ticket_id"])
            await session.commit()

        assert result.written == []
        assert len(result.skipped) == 1
        assert result.skipped[0].reason == "no_audit_rows"
    finally:
        await engine.dispose()


async def test_backfill_skips_when_two_runs_equally_near(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=53)
    origin_created = datetime(2026, 8, 14, 10, 0, 0)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(
                TiqoraAiArticleOrigin(
                    article_id=seed["customer_article_id"],
                    source=SOURCE_AUTO,
                    queue_id=seed["queue_id"],
                    service_user_id=seed["agent_id"],
                    tool_trace_json=None,
                    created=origin_created,
                )
            )
            for run_id in ("run-53-a", "run-53-b"):
                session.add(
                    TiqoraAiAuditLog(
                        ts=origin_created - timedelta(seconds=2),
                        run_id=run_id,
                        feature=FEATURE_AUTO_REPLY,
                        ticket_id=seed["ticket_id"],
                        queue_id=seed["queue_id"],
                        request_json=json.dumps(
                            {
                                "messages": [
                                    {
                                        "role": "tool",
                                        "tool_call_id": "call_1",
                                        "name": "propose_customer_message",
                                        "content": f"from {run_id}",
                                    }
                                ]
                            }
                        ),
                    )
                )
            await session.commit()

        async with factory() as session:
            result = await run_backfill(session, dry_run=False, ticket_id=seed["ticket_id"])
            await session.commit()

        assert result.written == []
        assert len(result.skipped) == 1
        assert result.skipped[0].reason == "ambiguous"
    finally:
        await engine.dispose()
