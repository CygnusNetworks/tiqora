"""DB + unit tests for tiqora.ai.runtime (plan §3.4 steps 1-12).

Uses a FakeLlm (scripted tool_calls) — no real LLM/MCP endpoint is ever
called. Seed ids use the 96xx range (unique per test, ``ns`` offset) so the
session-scoped testcontainer DB is shared safely with other test files.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.ai import policies as ai_policies
from tiqora.ai.acl import create_acl
from tiqora.ai.gate import (
    OPERATION_MODE_PARALLEL,
    OPERATION_MODE_TIQORA_PRIMARY,
    set_operation_mode,
)
from tiqora.ai.llm import LlmMessage, LlmResponse, LlmUsage, ToolCall
from tiqora.ai.models import AUTONOMY_CLARIFY_ONLY, AUTONOMY_FULL, AUTONOMY_OFF, TiqoraAiTicketState
from tiqora.ai.runtime import (
    TRIGGER_AUTO,
    TRIGGER_MANUAL,
    AclDeniedError,
    AclLimitExceededError,
    AgentRunError,
    LockHeldError,
    PolicyDisabledError,
    _map_customer_message,
    run_ticket_agent,
)
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Pure unit test: autonomy matrix (plan §3.4 table)
# ---------------------------------------------------------------------------


def test_autonomy_matrix_manual_is_always_draft() -> None:
    for autonomy in (AUTONOMY_OFF, AUTONOMY_CLARIFY_ONLY, AUTONOMY_FULL):
        for kind in ("reply", "clarify"):
            assert (
                _map_customer_message(trigger=TRIGGER_MANUAL, autonomy=autonomy, kind=kind)
                == "draft"
            )


def test_autonomy_matrix_auto_off_is_always_draft() -> None:
    for kind in ("reply", "clarify"):
        result = _map_customer_message(trigger=TRIGGER_AUTO, autonomy=AUTONOMY_OFF, kind=kind)
        assert result == "draft"


def test_autonomy_matrix_auto_clarify_only_hard_blocks_reply() -> None:
    assert (
        _map_customer_message(trigger=TRIGGER_AUTO, autonomy=AUTONOMY_CLARIFY_ONLY, kind="reply")
        == "draft"
    )
    assert (
        _map_customer_message(trigger=TRIGGER_AUTO, autonomy=AUTONOMY_CLARIFY_ONLY, kind="clarify")
        == "send"
    )


def test_autonomy_matrix_auto_full_always_sends() -> None:
    for kind in ("reply", "clarify"):
        result = _map_customer_message(trigger=TRIGGER_AUTO, autonomy=AUTONOMY_FULL, kind=kind)
        assert result == "send"


# ---------------------------------------------------------------------------
# Fake LLM
# ---------------------------------------------------------------------------


class ScriptedLlm:
    """Returns one scripted :class:`LlmResponse` per call, in order."""

    def __init__(
        self,
        responses: list[LlmResponse],
        *,
        on_call: Callable[[], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._responses = list(responses)
        self._on_call = on_call
        self.calls = 0

    async def chat(
        self,
        *,
        messages: list[LlmMessage],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LlmResponse:
        self.calls += 1
        if self._on_call is not None:
            await self._on_call()
        return self._responses.pop(0)


def _propose_response(kind: str, body: str, subject: str = "Re: Help") -> LlmResponse:
    return LlmResponse(
        content=None,
        tool_calls=[
            ToolCall(
                id="call_1",
                name="propose_customer_message",
                arguments={"kind": kind, "subject": subject, "body": body},
            )
        ],
        usage=LlmUsage(prompt_tokens=10, completion_tokens=5),
    )


def _escalate_response(reason: str) -> LlmResponse:
    return LlmResponse(
        content=None,
        tool_calls=[ToolCall(id="call_1", name="escalate_to_human", arguments={"reason": reason})],
        usage=LlmUsage(prompt_tokens=8, completion_tokens=4),
    )


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _seed_ticket(sync_url: str, *, ns: int) -> dict[str, Any]:
    agent_id = 9600 + ns
    group_id = 9630 + ns
    queue_id = 9600 + ns
    ticket_id = 9670 + ns
    login = f"agent.airuntime.96{ns}"
    queue_name = f"AiRuntimeQueue96{ns}"
    tn = f"20240601960{ns:03d}"

    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    pw = hash_password("secret")
    with engine.begin() as conn:
        for stmt, params in (
            ("DELETE FROM ticket WHERE id = :id", {"id": ticket_id}),
            ("DELETE FROM queue WHERE id = :id", {"id": queue_id}),
            (
                "DELETE FROM group_user WHERE user_id = :uid OR group_id = :gid",
                {"uid": agent_id, "gid": group_id},
            ),
            ("DELETE FROM permission_groups WHERE id = :id", {"id": group_id}),
            ("DELETE FROM users WHERE id = :id", {"id": agent_id}),
            ("DELETE FROM tiqora_ai_ticket_state WHERE ticket_id = :id", {"id": ticket_id}),
            ("DELETE FROM tiqora_ai_draft WHERE ticket_id = :id", {"id": ticket_id}),
            (
                "DELETE FROM tiqora_ai_article_origin WHERE queue_id = :id",
                {"id": queue_id},
            ),
            ("DELETE FROM tiqora_ai_queue_policy WHERE queue_id = :id", {"id": queue_id}),
            ("DELETE FROM tiqora_ai_acl WHERE subject_id = :id", {"id": agent_id}),
            (
                "DELETE FROM tiqora_llm_provider WHERE name = :n",
                {"n": f"fake-provider-{queue_id}"},
            ),
        ):
            conn.execute(text(stmt), params)

        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, :pw, 'Runtime', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": agent_id, "login": login, "pw": pw, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, 1, :t, 1, :t, 1)"
            ),
            {"id": group_id, "name": f"airuntime-grp-96{ns}", "t": NOW},
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
                "title": f"AI runtime ticket 96{ns}",
                "qid": queue_id,
                "uid": agent_id,
                "cid": f"CUST96{ns}",
                "cuid": f"customer96{ns}@example.com",
                "t": NOW,
            },
        )
        cust_st = conn.execute(
            text("SELECT id FROM article_sender_type WHERE name = 'customer' LIMIT 1")
        ).scalar()
        note_ch = conn.execute(
            text("SELECT id FROM communication_channel WHERE name = 'Internal' LIMIT 1")
        ).scalar()
        fp = f"fp-airuntime-96{ns}"
        conn.execute(
            text(
                "INSERT INTO article (ticket_id, article_sender_type_id,"
                " communication_channel_id, is_visible_for_customer, search_index_needs_rebuild,"
                " insert_fingerprint, create_time, create_by, change_time, change_by)"
                " VALUES (:tid, :st, :ch, 1, 0, :fp, :t, 1, :t, 1)"
            ),
            {"tid": ticket_id, "st": cust_st, "ch": note_ch, "fp": fp, "t": NOW},
        )
        customer_article_id = conn.execute(
            text("SELECT id FROM article WHERE insert_fingerprint = :fp LIMIT 1"), {"fp": fp}
        ).scalar()
        conn.execute(
            text(
                "INSERT INTO article_data_mime (article_id, a_subject, a_body, incoming_time,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:aid, 'Help please', 'I need help with X', 0, :t, 1, :t, 1)"
            ),
            {"aid": customer_article_id, "t": NOW},
        )
    engine.dispose()
    return {
        "agent_id": agent_id,
        "queue_id": queue_id,
        "ticket_id": ticket_id,
        "customer_article_id": int(customer_article_id),
    }


async def _setup_policy(
    session: AsyncSession,
    *,
    seed: dict[str, Any],
    autonomy: str,
    enabled_manual_assist: bool = True,
    enabled_auto_reply: bool = False,
) -> None:
    await set_operation_mode(session, OPERATION_MODE_TIQORA_PRIMARY)
    provider_id = None
    if enabled_auto_reply:
        from tiqora.ai import providers as ai_providers

        provider = await ai_providers.create_provider(
            session,
            settings=get_settings(),
            change_by=1,
            name=f"fake-provider-{seed['queue_id']}",
            kind="openai_compat",
            base_url="https://llm.example/v1",
            default_model="fake-model",
            api_key=None,
            extra_json=None,
            supports_tools=True,
            supports_streaming=False,
            eu_hosted=True,
        )
        provider_id = provider.id
    await ai_policies.create_queue_policy(
        session,
        change_by=1,
        queue_id=seed["queue_id"],
        enabled_manual_assist=enabled_manual_assist,
        enabled_auto_reply=enabled_auto_reply,
        system_prompt="You are a helpful support agent.",
        autonomy=autonomy,
        service_user_id=seed["agent_id"] if enabled_auto_reply else None,
        llm_provider_id=provider_id,
        pii_masking=False,
    )


async def test_manual_assist_creates_draft_even_at_full_autonomy(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=1)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed, autonomy=AUTONOMY_FULL)

        llm = ScriptedLlm([_propose_response("reply", "Here is the answer to your question.")])
        async with factory() as session:
            result = await run_ticket_agent(
                session,
                settings=settings,
                llm=llm,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_MANUAL,
                acting_user_id=seed["agent_id"],
                run_id="run-1",
            )
        assert result.status == "drafted"
        assert result.draft_id is not None

        async with factory() as session:
            from tiqora.ai import drafts as ai_drafts

            drafts = await ai_drafts.list_for_ticket(session, seed["ticket_id"])
            assert len(drafts) == 1
            assert drafts[0].source == "manual"
            assert drafts[0].based_on_article_id == seed["customer_article_id"]
    finally:
        await engine.dispose()


async def test_auto_clarify_only_blocks_reply_but_sends_clarify(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=2)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    try:
        async with factory() as session:
            await _setup_policy(
                session, seed=seed, autonomy=AUTONOMY_CLARIFY_ONLY, enabled_auto_reply=True
            )

        llm = ScriptedLlm([_propose_response("reply", "A factual answer.")])
        async with factory() as session:
            result = await run_ticket_agent(
                session,
                settings=settings,
                llm=llm,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_AUTO,
                acting_user_id=None,
                run_id="run-2a",
            )
        assert result.status == "drafted"  # hard-blocked reply, even though this is the auto path

        llm2 = ScriptedLlm([_propose_response("clarify", "Can you clarify your issue?")])
        async with factory() as session:
            result2 = await run_ticket_agent(
                session,
                settings=settings,
                llm=llm2,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_AUTO,
                acting_user_id=None,
                run_id="run-2b",
            )
        assert result2.status == "sent"
        assert result2.article_id is not None

        async with factory() as session:
            origin = (
                await session.execute(
                    text("SELECT source FROM tiqora_ai_article_origin WHERE article_id = :aid"),
                    {"aid": result2.article_id},
                )
            ).first()
            assert origin is not None
            assert origin[0] == "auto"
    finally:
        await engine.dispose()


async def test_escalate_to_human_stops_run_and_writes_internal_note(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=3)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed, autonomy=AUTONOMY_OFF)

        llm = ScriptedLlm([_escalate_response("Cannot identify the customer's issue")])
        async with factory() as session:
            result = await run_ticket_agent(
                session,
                settings=settings,
                llm=llm,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_MANUAL,
                acting_user_id=seed["agent_id"],
                run_id="run-3",
            )
        assert result.status == "escalated"

        async with factory() as session:
            note = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM article_data_mime m JOIN article a"
                        " ON a.id = m.article_id WHERE a.ticket_id = :tid"
                        " AND m.a_subject = 'AI agent escalation'"
                    ),
                    {"tid": seed["ticket_id"]},
                )
            ).scalar()
            assert note == 1
    finally:
        await engine.dispose()


async def test_lock_held_blocks_second_run_but_stale_lock_is_stolen(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=4)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed, autonomy=AUTONOMY_OFF)
            session.add(
                TiqoraAiTicketState(
                    ticket_id=seed["ticket_id"],
                    run_lock_owner="other:stale-or-fresh",
                    run_lock_at=NOW,
                )
            )
            await session.commit()

        # Fresh lock (default NOW from the fixture is far in the past relative
        # to real "now", so use utcnow-1s to simulate a just-acquired lock).
        from datetime import UTC
        from datetime import datetime as dt

        fresh = dt.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        async with factory() as session:
            state = await session.get(TiqoraAiTicketState, seed["ticket_id"])
            assert state is not None
            state.run_lock_at = fresh
            await session.commit()

        llm = ScriptedLlm([_propose_response("reply", "Answer")])
        async with factory() as session:
            with pytest.raises(LockHeldError):
                await run_ticket_agent(
                    session,
                    settings=settings,
                    llm=llm,
                    ticket_id=seed["ticket_id"],
                    trigger=TRIGGER_MANUAL,
                    acting_user_id=seed["agent_id"],
                    run_id="run-4a",
                )

        # Now make the lock stale (older than 15 minutes) -> stolen, run succeeds.
        stale = dt.now(UTC).replace(tzinfo=None) - timedelta(minutes=20)
        async with factory() as session:
            state = await session.get(TiqoraAiTicketState, seed["ticket_id"])
            assert state is not None
            state.run_lock_at = stale
            await session.commit()

        llm2 = ScriptedLlm([_propose_response("reply", "Answer")])
        async with factory() as session:
            result = await run_ticket_agent(
                session,
                settings=settings,
                llm=llm2,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_MANUAL,
                acting_user_id=seed["agent_id"],
                run_id="run-4b",
            )
        assert result.status == "drafted"

        async with factory() as session:
            state = await session.get(TiqoraAiTicketState, seed["ticket_id"])
            assert state is not None
            assert state.run_lock_owner is None  # released after the run
    finally:
        await engine.dispose()


async def test_freshness_supersede_when_new_customer_article_arrives_mid_run(
    mariadb_znuny_url: str,
) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=5)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed, autonomy=AUTONOMY_OFF)

        async def _insert_newer_customer_article() -> None:
            async with factory() as inner_session:
                cust_st = (
                    await inner_session.execute(
                        text("SELECT id FROM article_sender_type WHERE name = 'customer' LIMIT 1")
                    )
                ).scalar()
                note_ch = (
                    await inner_session.execute(
                        text("SELECT id FROM communication_channel WHERE name = 'Internal' LIMIT 1")
                    )
                ).scalar()
                fp = f"fp-airuntime-96-5-race-{seed['ticket_id']}"
                await inner_session.execute(
                    text(
                        "INSERT INTO article (ticket_id, article_sender_type_id,"
                        " communication_channel_id, is_visible_for_customer,"
                        " search_index_needs_rebuild, insert_fingerprint,"
                        " create_time, create_by, change_time, change_by)"
                        " VALUES (:tid, :st, :ch, 1, 0, :fp, :t, 1, :t, 1)"
                    ),
                    {"tid": seed["ticket_id"], "st": cust_st, "ch": note_ch, "fp": fp, "t": NOW},
                )
                await inner_session.commit()

        llm = ScriptedLlm(
            [_propose_response("reply", "Answer to the original question")],
            on_call=_insert_newer_customer_article,
        )
        async with factory() as session:
            result = await run_ticket_agent(
                session,
                settings=settings,
                llm=llm,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_MANUAL,
                acting_user_id=seed["agent_id"],
                run_id="run-5",
            )
        assert result.status == "superseded"

        async with factory() as session:
            from tiqora.ai import drafts as ai_drafts

            drafts = await ai_drafts.list_for_ticket(session, seed["ticket_id"])
            assert drafts == []
    finally:
        await engine.dispose()


async def test_gate_closed_blocks_run(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=6)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed, autonomy=AUTONOMY_OFF)
            await set_operation_mode(session, OPERATION_MODE_PARALLEL)

        llm = ScriptedLlm([_propose_response("reply", "Answer")])
        async with factory() as session:
            with pytest.raises(AgentRunError):
                await run_ticket_agent(
                    session,
                    settings=settings,
                    llm=llm,
                    ticket_id=seed["ticket_id"],
                    trigger=TRIGGER_MANUAL,
                    acting_user_id=seed["agent_id"],
                    run_id="run-6",
                )
    finally:
        await engine.dispose()


async def test_policy_disabled_manual_assist_raises(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=7)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    try:
        async with factory() as session:
            await _setup_policy(
                session, seed=seed, autonomy=AUTONOMY_OFF, enabled_manual_assist=False
            )

        llm = ScriptedLlm([_propose_response("reply", "Answer")])
        async with factory() as session:
            with pytest.raises(PolicyDisabledError):
                await run_ticket_agent(
                    session,
                    settings=settings,
                    llm=llm,
                    ticket_id=seed["ticket_id"],
                    trigger=TRIGGER_MANUAL,
                    acting_user_id=seed["agent_id"],
                    run_id="run-7",
                )
    finally:
        await engine.dispose()


async def test_acl_deny_blocks_manual_assist(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=8)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed, autonomy=AUTONOMY_OFF)
            await create_acl(
                session,
                subject_type="user",
                subject_id=seed["agent_id"],
                feature="manual_assist",
                allowed=False,
            )

        llm = ScriptedLlm([_propose_response("reply", "Answer")])
        async with factory() as session:
            with pytest.raises(AclDeniedError):
                await run_ticket_agent(
                    session,
                    settings=settings,
                    llm=llm,
                    ticket_id=seed["ticket_id"],
                    trigger=TRIGGER_MANUAL,
                    acting_user_id=seed["agent_id"],
                    run_id="run-8",
                )
    finally:
        await engine.dispose()


async def test_acl_limit_exceeded_blocks_manual_assist(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=9)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed, autonomy=AUTONOMY_OFF)
            await create_acl(
                session,
                subject_type="user",
                subject_id=seed["agent_id"],
                feature="manual_assist",
                allowed=True,
                limit_requests_day=1,
            )

        # First run consumes the daily budget (records one usage row).
        llm1 = ScriptedLlm([_propose_response("reply", "Answer 1")])
        async with factory() as session:
            result = await run_ticket_agent(
                session,
                settings=settings,
                llm=llm1,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_MANUAL,
                acting_user_id=seed["agent_id"],
                run_id="run-9a",
            )
        assert result.status == "drafted"

        # Second run is rejected before ever calling the LLM.
        llm2 = ScriptedLlm([_propose_response("reply", "Answer 2")])
        async with factory() as session:
            with pytest.raises(AclLimitExceededError):
                await run_ticket_agent(
                    session,
                    settings=settings,
                    llm=llm2,
                    ticket_id=seed["ticket_id"],
                    trigger=TRIGGER_MANUAL,
                    acting_user_id=seed["agent_id"],
                    run_id="run-9b",
                )
        assert llm2.calls == 0
    finally:
        await engine.dispose()
