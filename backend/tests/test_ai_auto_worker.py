"""DB + unit tests for tiqora.ai.auto_worker (plan §3.4/§3.9, Phase D).

Seed ids use the 98xx range (unique per test, ``ns`` offset) — disjoint from
the 96xx (``test_ai_runtime.py``) and 97xx (``test_ai_summary.py``) ranges —
so the session-scoped testcontainer DB is shared safely.

``build_llm_client``/``kb_bundle``/``kb_search_fn``/``kb_get_article_fn`` are
monkeypatched at the ``tiqora.ai.auto_worker`` module level (where they are
imported) so no real HTTP call is ever made.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.test_ai_runtime import ScriptedLlm, _propose_response
from tiqora.ai import policies as ai_policies
from tiqora.ai import providers as ai_providers
from tiqora.ai.auto_worker import run_auto_tick
from tiqora.ai.gate import (
    OPERATION_MODE_PARALLEL,
    OPERATION_MODE_TIQORA_PRIMARY,
    set_operation_mode,
)
from tiqora.ai.llm import LlmClient
from tiqora.ai.models import AUTONOMY_FULL, AUTONOMY_OFF, TiqoraAiTicketState
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.settings_store import (
    KEY_AI_GLOBAL_REPLIES_PER_HOUR,
    KEY_AI_OUTBOX_WATERMARK,
    get_setting_int,
    set_setting,
)

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _seed_ticket(sync_url: str, *, ns: int) -> dict[str, Any]:
    agent_id = 9800 + ns
    group_id = 9830 + ns
    queue_id = 9800 + ns
    ticket_id = 9870 + ns
    login = f"agent.aiauto.98{ns}"
    queue_name = f"AiAutoQueue98{ns}"
    tn = f"20240601980{ns:03d}"

    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt, params in (
            ("DELETE FROM tiqora_event_outbox WHERE ticket_id = :id", {"id": ticket_id}),
            ("DELETE FROM ticket WHERE id = :id", {"id": ticket_id}),
            ("DELETE FROM queue WHERE id = :id", {"id": queue_id}),
            (
                "DELETE FROM group_user WHERE user_id = :uid OR group_id = :gid",
                {"uid": agent_id, "gid": group_id},
            ),
            ("DELETE FROM permission_groups WHERE id = :id", {"id": group_id}),
            ("DELETE FROM users WHERE id = :id", {"id": agent_id}),
            ("DELETE FROM tiqora_ai_ticket_state WHERE ticket_id = :id", {"id": ticket_id}),
            (
                "DELETE FROM tiqora_ai_article_origin WHERE queue_id = :id",
                {"id": queue_id},
            ),
            ("DELETE FROM tiqora_ai_usage WHERE queue_id = :id", {"id": queue_id}),
            ("DELETE FROM tiqora_ai_queue_policy WHERE queue_id = :id", {"id": queue_id}),
            (
                "DELETE FROM tiqora_llm_provider WHERE name = :n",
                {"n": f"fake-auto-provider-{queue_id}"},
            ),
        ):
            conn.execute(text(stmt), params)

        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, 'x', 'Auto', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": agent_id, "login": login, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, 1, :t, 1, :t, 1)"
            ),
            {"id": group_id, "name": f"aiauto-grp-98{ns}", "t": NOW},
        )
        for key in ("ro", "rw", "note"):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, :gid, :k, :t, 1, :t, 1)"
                ),
                {"uid": agent_id, "gid": group_id, "k": key, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": queue_id, "name": queue_name, "gid": group_id, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :tn, :title, :qid, 1, 1,"
                " :uid, 1, 3, 4, :cid, :cuid,"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {
                "id": ticket_id,
                "tn": tn,
                "title": f"AI auto ticket 98{ns}",
                "qid": queue_id,
                "uid": agent_id,
                "cid": f"CUST98{ns}",
                "cuid": f"customer98{ns}@example.com",
                "t": NOW,
            },
        )
    engine.dispose()
    return {"agent_id": agent_id, "queue_id": queue_id, "ticket_id": ticket_id}


def _add_article(sync_url: str, *, ticket_id: int, sender_type: str, body: str) -> int:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        st = conn.execute(
            text("SELECT id FROM article_sender_type WHERE name = :n LIMIT 1"), {"n": sender_type}
        ).scalar()
        ch = conn.execute(
            text("SELECT id FROM communication_channel WHERE name = 'Internal' LIMIT 1")
        ).scalar()
        fp = f"fp-aiauto-{ticket_id}-{sender_type}-{body[:8]}-{id(body)}"
        conn.execute(
            text(
                "INSERT INTO article (ticket_id, article_sender_type_id,"
                " communication_channel_id, is_visible_for_customer, search_index_needs_rebuild,"
                " insert_fingerprint, create_time, create_by, change_time, change_by)"
                " VALUES (:tid, :st, :ch, 1, 0, :fp, :t, 1, :t, 1)"
            ),
            {"tid": ticket_id, "st": st, "ch": ch, "fp": fp, "t": NOW},
        )
        article_id = conn.execute(
            text("SELECT id FROM article WHERE insert_fingerprint = :fp LIMIT 1"), {"fp": fp}
        ).scalar()
        conn.execute(
            text(
                "INSERT INTO article_data_mime (article_id, a_subject, a_body, incoming_time,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:aid, 'Help', :body, 0, :t, 1, :t, 1)"
            ),
            {"aid": article_id, "body": body, "t": NOW},
        )
    engine.dispose()
    assert article_id is not None
    return int(article_id)


def _insert_outbox_event(
    sync_url: str, *, ticket_id: int, event_type: str, article_id: int | None
) -> int:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        import json

        payload = json.dumps({"article_id": article_id}) if article_id is not None else "{}"
        conn.execute(
            text(
                "INSERT INTO tiqora_event_outbox"
                " (event_type, ticket_id, payload, created, processed)"
                " VALUES (:et, :tid, :pl, current_timestamp, 0)"
            ),
            {"et": event_type, "tid": ticket_id, "pl": payload},
        )
        event_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    engine.dispose()
    assert event_id is not None
    return int(event_id)


async def _setup_policy(
    session: Any,
    *,
    seed: dict[str, Any],
    autonomy: str = AUTONOMY_FULL,
    max_auto_replies: int = 5,
    max_clarifications: int = 2,
    max_replies_per_hour: int | None = None,
    budget_tokens_day: int | None = None,
) -> None:
    await set_operation_mode(session, OPERATION_MODE_TIQORA_PRIMARY)
    # tiqora_settings is a single shared table across tests in this module;
    # reset the global cap so an earlier test's setting never leaks here.
    await set_setting(session, KEY_AI_GLOBAL_REPLIES_PER_HOUR, "")
    provider = await ai_providers.create_provider(
        session,
        settings=get_settings(),
        change_by=1,
        name=f"fake-auto-provider-{seed['queue_id']}",
        kind="openai_compat",
        base_url="https://llm.example/v1",
        default_model="fake-model",
        api_key=None,
        extra_json=None,
        supports_tools=True,
        supports_streaming=False,
        eu_hosted=True,
    )
    await ai_policies.create_queue_policy(
        session,
        change_by=1,
        queue_id=seed["queue_id"],
        enabled_auto_reply=True,
        autonomy=autonomy,
        service_user_id=seed["agent_id"],
        llm_provider_id=provider.id,
        pii_masking=False,
        max_auto_replies=max_auto_replies,
        max_clarifications=max_clarifications,
        max_replies_per_hour=max_replies_per_hour,
        budget_tokens_day=budget_tokens_day,
    )


def _patch_llm(monkeypatch: pytest.MonkeyPatch, llm: LlmClient) -> None:
    async def _fake_build_llm_client(*_args: Any, **_kwargs: Any) -> LlmClient:
        return llm

    monkeypatch.setattr("tiqora.ai.auto_worker.build_llm_client", _fake_build_llm_client)


async def test_watermark_advances_even_for_irrelevant_events(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=1)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed)
            watermark_before = await get_setting_int(session, KEY_AI_OUTBOX_WATERMARK, 0)

        _insert_outbox_event(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            event_type="TicketCreate",
            article_id=None,
        )

        # The DB (tiqora_event_outbox / KEY_AI_OUTBOX_WATERMARK) is a single
        # shared testcontainer used by the whole suite, so other tests may
        # have queued events since the last drain — assert against the
        # actual current max id, not this test's own event id in isolation.
        async with factory() as session:
            max_id = (
                await session.execute(text("SELECT MAX(id) FROM tiqora_event_outbox"))
            ).scalar()

        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["events"] >= 1
        assert totals["auto_replies"] == 0

        async with factory() as session:
            watermark = await get_setting_int(session, KEY_AI_OUTBOX_WATERMARK, 0)
        assert watermark == max_id
        assert watermark > watermark_before
    finally:
        await engine.dispose()


async def test_customer_article_triggers_auto_reply(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=2)
    article_id = _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    _insert_outbox_event(
        mariadb_znuny_url,
        ticket_id=seed["ticket_id"],
        event_type="ArticleCreate",
        article_id=article_id,
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed, autonomy=AUTONOMY_FULL)

        _patch_llm(monkeypatch, ScriptedLlm([_propose_response("reply", "Automated answer.")]))
        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["auto_replies"] == 1

        async with factory() as session:
            state = await session.get(TiqoraAiTicketState, seed["ticket_id"])
            assert state is not None
            assert state.auto_reply_count == 1
            assert state.last_customer_article_id == article_id
    finally:
        await engine.dispose()


async def test_non_customer_article_is_ignored(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=3)
    article_id = _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="agent", body="Internal note"
    )
    _insert_outbox_event(
        mariadb_znuny_url,
        ticket_id=seed["ticket_id"],
        event_type="ArticleCreate",
        article_id=article_id,
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed)

        _patch_llm(monkeypatch, ScriptedLlm([_propose_response("reply", "Should not be called.")]))
        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["auto_replies"] == 0
    finally:
        await engine.dispose()


async def test_loop_guard_skips_already_processed_article(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=4)
    article_id = _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    _insert_outbox_event(
        mariadb_znuny_url,
        ticket_id=seed["ticket_id"],
        event_type="ArticleCreate",
        article_id=article_id,
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed)
            session.add(
                TiqoraAiTicketState(
                    ticket_id=seed["ticket_id"], last_customer_article_id=article_id
                )
            )
            await session.commit()

        _patch_llm(monkeypatch, ScriptedLlm([_propose_response("reply", "Should not run.")]))
        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["auto_replies"] == 0
    finally:
        await engine.dispose()


async def test_max_auto_replies_cap_blocks_run(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=5)
    article_id = _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    _insert_outbox_event(
        mariadb_znuny_url,
        ticket_id=seed["ticket_id"],
        event_type="ArticleCreate",
        article_id=article_id,
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed, max_auto_replies=1)
            session.add(TiqoraAiTicketState(ticket_id=seed["ticket_id"], auto_reply_count=1))
            await session.commit()

        _patch_llm(monkeypatch, ScriptedLlm([_propose_response("reply", "Should not run.")]))
        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["auto_replies"] == 0
    finally:
        await engine.dispose()


async def test_queue_rate_limit_blocks_run(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.ai import usage as usage_service

    seed = _seed_ticket(mariadb_znuny_url, ns=6)
    article_id = _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    _insert_outbox_event(
        mariadb_znuny_url,
        ticket_id=seed["ticket_id"],
        event_type="ArticleCreate",
        article_id=article_id,
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed, max_replies_per_hour=1)
            await usage_service.record_usage(
                session,
                queue_id=seed["queue_id"],
                ticket_id=seed["ticket_id"],
                feature="auto_reply",
                success=True,
            )

        _patch_llm(monkeypatch, ScriptedLlm([_propose_response("reply", "Should not run.")]))
        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["auto_replies"] == 0
    finally:
        await engine.dispose()


async def test_global_rate_limit_blocks_run(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.ai import usage as usage_service

    seed = _seed_ticket(mariadb_znuny_url, ns=7)
    other_queue_id = 99070
    article_id = _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    _insert_outbox_event(
        mariadb_znuny_url,
        ticket_id=seed["ticket_id"],
        event_type="ArticleCreate",
        article_id=article_id,
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed)
            await set_setting(session, KEY_AI_GLOBAL_REPLIES_PER_HOUR, "1")
            # Global cap is reached by usage in a DIFFERENT queue.
            await usage_service.record_usage(
                session, queue_id=other_queue_id, feature="auto_reply", success=True
            )

        _patch_llm(monkeypatch, ScriptedLlm([_propose_response("reply", "Should not run.")]))
        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["auto_replies"] == 0
    finally:
        await engine.dispose()


async def test_budget_tokens_day_exceeded_blocks_run(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.ai import usage as usage_service

    seed = _seed_ticket(mariadb_znuny_url, ns=8)
    article_id = _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    _insert_outbox_event(
        mariadb_znuny_url,
        ticket_id=seed["ticket_id"],
        event_type="ArticleCreate",
        article_id=article_id,
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed, budget_tokens_day=100)
            await usage_service.record_usage(
                session,
                queue_id=seed["queue_id"],
                feature="auto_reply",
                prompt_tokens=80,
                completion_tokens=30,
                success=True,
            )

        _patch_llm(monkeypatch, ScriptedLlm([_propose_response("reply", "Should not run.")]))
        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["auto_replies"] == 0
    finally:
        await engine.dispose()


async def test_error_in_one_event_does_not_abort_batch(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed1 = _seed_ticket(mariadb_znuny_url, ns=9)
    seed2 = _seed_ticket(mariadb_znuny_url, ns=10)
    article1 = _add_article(
        mariadb_znuny_url, ticket_id=seed1["ticket_id"], sender_type="customer", body="Help 1"
    )
    article2 = _add_article(
        mariadb_znuny_url, ticket_id=seed2["ticket_id"], sender_type="customer", body="Help 2"
    )
    _insert_outbox_event(
        mariadb_znuny_url,
        ticket_id=seed1["ticket_id"],
        event_type="ArticleCreate",
        article_id=article1,
    )
    _insert_outbox_event(
        mariadb_znuny_url,
        ticket_id=seed2["ticket_id"],
        event_type="ArticleCreate",
        article_id=article2,
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed1)
            await _setup_policy(session, seed=seed2)

        calls = {"n": 0}

        async def _fake_build_llm_client(*_args: Any, **_kwargs: Any) -> LlmClient:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return ScriptedLlm([_propose_response("reply", "Second ticket answer.")])

        monkeypatch.setattr("tiqora.ai.auto_worker.build_llm_client", _fake_build_llm_client)

        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["events"] == 2
        assert totals["errors"] == 1
        assert totals["auto_replies"] == 1
    finally:
        await engine.dispose()


async def test_gate_regression_skips_batch_entirely(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=11)
    article_id = _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    _insert_outbox_event(
        mariadb_znuny_url,
        ticket_id=seed["ticket_id"],
        event_type="ArticleCreate",
        article_id=article_id,
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed)
            await set_operation_mode(session, OPERATION_MODE_PARALLEL)
            watermark_before = await get_setting_int(session, KEY_AI_OUTBOX_WATERMARK, 0)

        _patch_llm(monkeypatch, ScriptedLlm([_propose_response("reply", "Should not run.")]))
        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals == {"gate_open": 0}

        async with factory() as session:
            watermark_after = await get_setting_int(session, KEY_AI_OUTBOX_WATERMARK, 0)
        assert watermark_after == watermark_before  # gate closed: batch untouched, no advance
    finally:
        # Restore the gate and drop the never-drained event so it cannot
        # leak into a later test's batch (this key/table is shared globally).
        async with factory() as session:
            await set_operation_mode(session, OPERATION_MODE_TIQORA_PRIMARY)
            await session.execute(
                text("DELETE FROM tiqora_event_outbox WHERE ticket_id = :tid"),
                {"tid": seed["ticket_id"]},
            )
            await session.commit()
        await engine.dispose()


async def test_counters_incremented_only_on_real_send(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=12)
    article_id = _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    _insert_outbox_event(
        mariadb_znuny_url,
        ticket_id=seed["ticket_id"],
        event_type="ArticleCreate",
        article_id=article_id,
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            # autonomy=off -> reply is always drafted, never sent.
            await _setup_policy(session, seed=seed, autonomy=AUTONOMY_OFF)

        _patch_llm(monkeypatch, ScriptedLlm([_propose_response("reply", "Drafted only.")]))
        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["auto_replies"] == 0  # drafted, not sent

        async with factory() as session:
            state = await session.get(TiqoraAiTicketState, seed["ticket_id"])
            assert state is not None
            assert state.auto_reply_count == 0
    finally:
        await engine.dispose()
