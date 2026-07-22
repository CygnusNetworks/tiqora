"""DB + unit tests for tiqora.ai.summary (plan §3.5).

Seed ids use the 97xx range (unique per test, ``ns`` offset) — disjoint from
the 96xx range used by ``test_ai_runtime.py`` — so the session-scoped
testcontainer DB is shared safely.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.ai import policies as ai_policies
from tiqora.ai import providers as ai_providers
from tiqora.ai.gate import OPERATION_MODE_TIQORA_PRIMARY, set_operation_mode
from tiqora.ai.llm import LlmMessage, LlmResponse, LlmUsage
from tiqora.ai.models import TiqoraAiTicketState
from tiqora.ai.summary import (
    STATUS_UP_TO_DATE,
    STATUS_UPDATED,
    TRIGGER_AUTO,
    TRIGGER_MANUAL,
    SummaryAclDeniedError,
    SummaryPolicyDisabledError,
    auto_summary_due,
    summarize_ticket,
)
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)


class ScriptedLlm:
    """Returns one scripted plain-text :class:`LlmResponse` per call, in order."""

    def __init__(self, contents: list[str | None]) -> None:
        self._contents = list(contents)
        self.calls = 0
        self.last_user_message: str | None = None

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
        self.last_user_message = next(
            (m.content for m in reversed(messages) if m.role == "user"), None
        )
        content = self._contents.pop(0)
        return LlmResponse(content=content, usage=LlmUsage(prompt_tokens=12, completion_tokens=6))


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _seed_ticket(sync_url: str, *, ns: int) -> dict[str, Any]:
    agent_id = 9700 + ns
    group_id = 9730 + ns
    queue_id = 9700 + ns
    ticket_id = 9770 + ns
    login = f"agent.aisummary.97{ns}"
    queue_name = f"AiSummaryQueue97{ns}"
    tn = f"20240601970{ns:03d}"

    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
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
            (
                "DELETE FROM tiqora_ai_article_origin WHERE queue_id = :id",
                {"id": queue_id},
            ),
            ("DELETE FROM tiqora_ai_queue_policy WHERE queue_id = :id", {"id": queue_id}),
            ("DELETE FROM tiqora_ai_acl WHERE subject_id = :id", {"id": agent_id}),
            (
                "DELETE FROM tiqora_llm_provider WHERE name = :n",
                {"n": f"fake-summary-provider-{queue_id}"},
            ),
        ):
            conn.execute(text(stmt), params)

        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, 'x', 'Summary', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": agent_id, "login": login, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, 1, :t, 1, :t, 1)"
            ),
            {"id": group_id, "name": f"aisummary-grp-97{ns}", "t": NOW},
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
                "title": f"AI summary ticket 97{ns}",
                "qid": queue_id,
                "uid": agent_id,
                "cid": f"CUST97{ns}",
                "cuid": f"customer97{ns}@example.com",
                "t": NOW,
            },
        )
    engine.dispose()
    return {"agent_id": agent_id, "queue_id": queue_id, "ticket_id": ticket_id}


def _add_article(
    sync_url: str,
    *,
    ticket_id: int,
    sender_type: str,
    body: str,
    subject: str = "Re: Help",
    ai_origin: bool = False,
    queue_id: int | None = None,
) -> int:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        st = conn.execute(
            text("SELECT id FROM article_sender_type WHERE name = :n LIMIT 1"), {"n": sender_type}
        ).scalar()
        ch = conn.execute(
            text("SELECT id FROM communication_channel WHERE name = 'Internal' LIMIT 1")
        ).scalar()
        fp = f"fp-aisummary-{ticket_id}-{sender_type}-{body[:8]}-{id(body)}"
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
                " VALUES (:aid, :subj, :body, 0, :t, 1, :t, 1)"
            ),
            {"aid": article_id, "subj": subject, "body": body, "t": NOW},
        )
        if ai_origin:
            conn.execute(
                text(
                    "INSERT INTO tiqora_ai_article_origin (article_id, source, queue_id,"
                    " service_user_id, created) VALUES (:aid, 'auto', :qid, NULL, :t)"
                ),
                {"aid": article_id, "qid": queue_id, "t": NOW},
            )
    engine.dispose()
    assert article_id is not None
    return int(article_id)


async def _setup_policy(
    session: AsyncSession,
    *,
    seed: dict[str, Any],
    enabled_summary: bool = True,
    summary_incremental_min_articles: int | None = None,
    summary_incremental_min_chars: int | None = None,
    summary_article_threshold: int | None = None,
    summary_char_threshold: int | None = None,
) -> None:
    await set_operation_mode(session, OPERATION_MODE_TIQORA_PRIMARY)
    provider = await ai_providers.create_provider(
        session,
        settings=get_settings(),
        change_by=1,
        name=f"fake-summary-provider-{seed['queue_id']}",
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
        enabled_summary=enabled_summary,
        llm_provider_id=provider.id,
        pii_masking=False,
        summary_incremental_min_articles=summary_incremental_min_articles,
        summary_incremental_min_chars=summary_incremental_min_chars,
        summary_article_threshold=summary_article_threshold,
        summary_char_threshold=summary_char_threshold,
    )


async def test_full_summary_when_no_previous_summary(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=1)
    _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed)

        llm = ScriptedLlm(["The customer needs help."])
        async with factory() as session:
            result = await summarize_ticket(
                session,
                llm=llm,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_MANUAL,
                acting_user_id=seed["agent_id"],
            )
        assert result.status == STATUS_UPDATED
        assert result.summary_body == "The customer needs help."
        assert llm.last_user_message is not None
        assert "full conversation" in llm.last_user_message

        async with factory() as session:
            state = await session.get(TiqoraAiTicketState, seed["ticket_id"])
            assert state is not None
            assert state.summary_body == "The customer needs help."
            assert state.last_summary_upto_article_id == result.upto_article_id
            assert state.last_summary_hash is not None
    finally:
        await engine.dispose()


async def test_incremental_summary_includes_previous_summary_and_only_new_articles(
    mariadb_znuny_url: str,
) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=2)
    _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="First msg"
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed)

        llm1 = ScriptedLlm(["Summary v1."])
        async with factory() as session:
            r1 = await summarize_ticket(
                session,
                llm=llm1,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_MANUAL,
                acting_user_id=seed["agent_id"],
            )
        assert r1.status == STATUS_UPDATED

        _add_article(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            sender_type="customer",
            body="Second msg",
        )

        llm2 = ScriptedLlm(["Summary v2."])
        async with factory() as session:
            r2 = await summarize_ticket(
                session,
                llm=llm2,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_MANUAL,
                acting_user_id=seed["agent_id"],
            )
        assert r2.status == STATUS_UPDATED
        assert llm2.last_user_message is not None
        assert "Summary v1." in llm2.last_user_message  # previous summary carried forward
        assert "First msg" not in llm2.last_user_message  # old article NOT resent
        assert "Second msg" in llm2.last_user_message  # only the new one
    finally:
        await engine.dispose()


async def test_no_new_articles_is_up_to_date_noop(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=3)
    _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed)

        llm1 = ScriptedLlm(["Summary v1."])
        async with factory() as session:
            await summarize_ticket(
                session,
                llm=llm1,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_MANUAL,
                acting_user_id=seed["agent_id"],
            )

        llm2 = ScriptedLlm(["Should never be called."])
        async with factory() as session:
            result = await summarize_ticket(
                session,
                llm=llm2,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_MANUAL,
                acting_user_id=seed["agent_id"],
            )
        assert result.status == STATUS_UP_TO_DATE
        assert result.summary_body == "Summary v1."
        assert llm2.calls == 0
    finally:
        await engine.dispose()


async def test_auto_trigger_incremental_threshold_no_op_but_manual_overrides(
    mariadb_znuny_url: str,
) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=4)
    _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(
                session,
                seed=seed,
                summary_incremental_min_articles=5,
                summary_incremental_min_chars=10_000,
            )

        llm1 = ScriptedLlm(["Summary v1."])
        async with factory() as session:
            await summarize_ticket(
                session,
                llm=llm1,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_MANUAL,
                acting_user_id=seed["agent_id"],
            )

        _add_article(
            mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="One more"
        )

        # Auto trigger: below the incremental threshold -> no-op.
        llm_auto = ScriptedLlm(["Should not be called."])
        async with factory() as session:
            auto_result = await summarize_ticket(
                session,
                llm=llm_auto,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_AUTO,
                acting_user_id=None,
            )
        assert auto_result.status == STATUS_UP_TO_DATE
        assert llm_auto.calls == 0

        # Manual: proceeds anyway (>= 1 new article).
        llm_manual = ScriptedLlm(["Summary v2."])
        async with factory() as session:
            manual_result = await summarize_ticket(
                session,
                llm=llm_manual,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_MANUAL,
                acting_user_id=seed["agent_id"],
            )
        assert manual_result.status == STATUS_UPDATED
        assert llm_manual.calls == 1
    finally:
        await engine.dispose()


async def test_ai_authored_articles_are_filtered_from_context_not_counted(
    mariadb_znuny_url: str,
) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=5)
    _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed)

        llm1 = ScriptedLlm(["Summary v1."])
        async with factory() as session:
            await summarize_ticket(
                session,
                llm=llm1,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_MANUAL,
                acting_user_id=seed["agent_id"],
            )

        _add_article(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            sender_type="agent",
            body="AI auto-reply text",
            ai_origin=True,
            queue_id=seed["queue_id"],
        )

        llm2 = ScriptedLlm(["Summary v2."])
        async with factory() as session:
            result = await summarize_ticket(
                session,
                llm=llm2,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_MANUAL,
                acting_user_id=seed["agent_id"],
            )
        assert result.status == STATUS_UPDATED
        assert llm2.last_user_message is not None
        assert "AI, previous own action" in llm2.last_user_message
        assert "AI auto-reply text" in llm2.last_user_message  # labeled, not removed
    finally:
        await engine.dispose()


async def test_summary_persists_only_in_ticket_state_no_article_created(
    mariadb_znuny_url: str,
) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=6)
    _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed)
            count_before = (
                await session.execute(
                    text("SELECT COUNT(*) FROM article WHERE ticket_id = :tid"),
                    {"tid": seed["ticket_id"]},
                )
            ).scalar()

        llm = ScriptedLlm(["A summary."])
        async with factory() as session:
            await summarize_ticket(
                session,
                llm=llm,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_MANUAL,
                acting_user_id=seed["agent_id"],
            )

        async with factory() as session:
            count_after = (
                await session.execute(
                    text("SELECT COUNT(*) FROM article WHERE ticket_id = :tid"),
                    {"tid": seed["ticket_id"]},
                )
            ).scalar()
        assert count_after == count_before
    finally:
        await engine.dispose()


async def test_hash_and_upto_are_correct(mariadb_znuny_url: str) -> None:
    import hashlib

    seed = _seed_ticket(mariadb_znuny_url, ns=7)
    article_id = _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed)

        llm = ScriptedLlm(["A deterministic summary."])
        async with factory() as session:
            result = await summarize_ticket(
                session,
                llm=llm,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_MANUAL,
                acting_user_id=seed["agent_id"],
            )
        assert result.upto_article_id == article_id

        async with factory() as session:
            state = await session.get(TiqoraAiTicketState, seed["ticket_id"])
            assert state is not None
            expected_hash = hashlib.sha256(b"A deterministic summary.").hexdigest()
            assert state.last_summary_hash == expected_hash
    finally:
        await engine.dispose()


async def test_acl_manual_deny_blocks_summarize(mariadb_znuny_url: str) -> None:
    from tiqora.ai.acl import create_acl

    seed = _seed_ticket(mariadb_znuny_url, ns=8)
    _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed)
            await create_acl(
                session,
                subject_type="user",
                subject_id=seed["agent_id"],
                feature="summary",
                allowed=False,
            )

        llm = ScriptedLlm(["Should not run."])
        async with factory() as session:
            with pytest.raises(SummaryAclDeniedError):
                await summarize_ticket(
                    session,
                    llm=llm,
                    ticket_id=seed["ticket_id"],
                    trigger=TRIGGER_MANUAL,
                    acting_user_id=seed["agent_id"],
                )
        assert llm.calls == 0
    finally:
        await engine.dispose()


async def test_summarize_runs_in_parallel_operation(mariadb_znuny_url: str) -> None:
    """Plan §3.0 v1.1 relaxation (Phase E): summaries are state-only and
    never gated by ``operation_mode`` — manual summarize must succeed in
    ``parallel`` operation just as it does in ``tiqora_primary``."""
    from tiqora.ai.gate import OPERATION_MODE_PARALLEL

    seed = _seed_ticket(mariadb_znuny_url, ns=9)
    _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed)
            await set_operation_mode(session, OPERATION_MODE_PARALLEL)

        llm = ScriptedLlm(["A summary written while in parallel operation."])
        async with factory() as session:
            result = await summarize_ticket(
                session,
                llm=llm,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_MANUAL,
                acting_user_id=seed["agent_id"],
            )
        assert result.status == STATUS_UPDATED
        assert llm.calls == 1
    finally:
        await engine.dispose()


async def test_policy_disabled_raises(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=10)
    _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed, enabled_summary=False)

        llm = ScriptedLlm(["Should not run."])
        async with factory() as session:
            with pytest.raises(SummaryPolicyDisabledError):
                await summarize_ticket(
                    session,
                    llm=llm,
                    ticket_id=seed["ticket_id"],
                    trigger=TRIGGER_MANUAL,
                    acting_user_id=seed["agent_id"],
                )
    finally:
        await engine.dispose()


async def test_auto_summary_due_thresholds(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=11)
    _add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_policy(session, seed=seed, summary_article_threshold=2)

        async with factory() as session:
            assert await auto_summary_due(session, seed["ticket_id"]) is False  # only 1 article

        _add_article(
            mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="More"
        )
        async with factory() as session:
            assert await auto_summary_due(session, seed["ticket_id"]) is True

        # NULL thresholds -> never due, even with plenty of articles.
        async with factory() as session:
            await ai_policies.update_queue_policy(
                session,
                (await ai_policies.get_queue_policy_by_queue(session, seed["queue_id"])),  # type: ignore[arg-type]
                change_by=1,
                summary_article_threshold=None,
                summary_char_threshold=None,
            )
        async with factory() as session:
            assert await auto_summary_due(session, seed["ticket_id"]) is False
    finally:
        await engine.dispose()
