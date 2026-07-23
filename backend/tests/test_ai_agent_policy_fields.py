"""DB integration tests for the plan blocks 1/2/5 generic-agent extensions:
ticket-header metadata (:mod:`tiqora.ai.context`), the sender blocklist
(:mod:`tiqora.ai.senders` wired into :mod:`tiqora.ai.auto_worker`), and the
``update_ticket_fields`` state-name/whitelist guard
(:mod:`tiqora.ai.tools`).

Seed ids use the 890xx range (unique per test, ``ns`` offset) — disjoint from
the 96xx/97xx/98xx ranges used by the other ``test_ai_*`` DB test files — so
the session-scoped testcontainer DB is shared safely.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.test_ai_runtime import ScriptedLlm, _propose_response
from tiqora.ai import policies as ai_policies
from tiqora.ai import providers as ai_providers
from tiqora.ai.auto_worker import run_auto_tick
from tiqora.ai.context import ticket_snapshot
from tiqora.ai.gate import OPERATION_MODE_TIQORA_PRIMARY, set_operation_mode
from tiqora.ai.llm import LlmClient
from tiqora.ai.models import AUTONOMY_FULL, TiqoraAiTicketState
from tiqora.ai.pii import PiiMapper
from tiqora.ai.tools import ToolArgumentError, ToolExecutor, ToolRegistry
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)

STATE_OPEN_ID = 4  # ticket_state "open" (type "open") — Znuny base fixture data
STATE_CLOSED_ID = 2  # ticket_state "closed successful" (type "closed")


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _seed_ticket(sync_url: str, *, ns: int, ticket_state_id: int = STATE_OPEN_ID) -> dict[str, Any]:
    agent_id = 89000 + ns
    group_id = 89030 + ns
    queue_id = 89000 + ns
    ticket_id = 89070 + ns
    login = f"agent.aipolicy.890{ns}"
    queue_name = f"AiPolicyQueue890{ns}"
    tn = f"2024060189{ns:04d}"

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
            ("DELETE FROM tiqora_ai_article_origin WHERE queue_id = :id", {"id": queue_id}),
            ("DELETE FROM tiqora_ai_usage WHERE queue_id = :id", {"id": queue_id}),
            ("DELETE FROM tiqora_ai_queue_policy WHERE queue_id = :id", {"id": queue_id}),
            (
                "DELETE FROM tiqora_llm_provider WHERE name = :n",
                {"n": f"fake-policyfields-provider-{queue_id}"},
            ),
        ):
            conn.execute(text(stmt), params)

        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, 'x', 'Policy', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": agent_id, "login": login, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, 1, :t, 1, :t, 1)"
            ),
            {"id": group_id, "name": f"aipolicy-grp-890{ns}", "t": NOW},
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
                " :uid, 1, 3, :sid, :cid, :cuid,"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {
                "id": ticket_id,
                "tn": tn,
                "title": f"AI policy fields ticket 890{ns}",
                "qid": queue_id,
                "sid": ticket_state_id,
                "uid": agent_id,
                "cid": f"CUST890{ns}",
                "cuid": f"customer890{ns}@example.com",
                "t": NOW,
            },
        )
    engine.dispose()
    return {
        "agent_id": agent_id,
        "queue_id": queue_id,
        "ticket_id": ticket_id,
        "queue_name": queue_name,
    }


def _add_article(
    sync_url: str, *, ticket_id: int, sender_type: str, body: str, from_address: str | None = None
) -> int:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        st = conn.execute(
            text("SELECT id FROM article_sender_type WHERE name = :n LIMIT 1"), {"n": sender_type}
        ).scalar()
        ch = conn.execute(
            text("SELECT id FROM communication_channel WHERE name = 'Internal' LIMIT 1")
        ).scalar()
        fp = f"fp-aipolicy-{ticket_id}-{sender_type}-{id(body)}"
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
                "INSERT INTO article_data_mime (article_id, a_subject, a_from, a_body,"
                " incoming_time, create_time, create_by, change_time, change_by)"
                " VALUES (:aid, 'Help', :from_addr, :body, 0, :t, 1, :t, 1)"
            ),
            {"aid": article_id, "from_addr": from_address, "body": body, "t": NOW},
        )
    engine.dispose()
    assert article_id is not None
    return int(article_id)


def _insert_outbox_event(sync_url: str, *, ticket_id: int, article_id: int) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tiqora_event_outbox"
                " (event_type, ticket_id, payload, created, processed)"
                " VALUES ('ArticleCreate', :tid, :pl, current_timestamp, 0)"
            ),
            {"tid": ticket_id, "pl": json.dumps({"article_id": article_id})},
        )
    engine.dispose()


async def _setup_auto_reply_policy(
    session: AsyncSession, *, seed: dict[str, Any], ignored_senders: str | None
) -> None:
    await set_operation_mode(session, OPERATION_MODE_TIQORA_PRIMARY)
    provider = await ai_providers.create_provider(
        session,
        settings=get_settings(),
        change_by=1,
        name=f"fake-policyfields-provider-{seed['queue_id']}",
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
        autonomy=AUTONOMY_FULL,
        service_user_id=seed["agent_id"],
        llm_provider_id=provider.id,
        pii_masking=False,
        ignored_senders=ignored_senders,
    )


def _patch_llm(monkeypatch: pytest.MonkeyPatch, llm: LlmClient) -> None:
    async def _fake_build_llm_client(*_args: Any, **_kwargs: Any) -> LlmClient:
        return llm

    monkeypatch.setattr("tiqora.ai.auto_worker.build_llm_client", _fake_build_llm_client)


# ---------------------------------------------------------------------------
# Block 1 — ticket_snapshot() header fields
# ---------------------------------------------------------------------------


async def test_ticket_snapshot_includes_extended_header_fields(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=1)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            snap = await ticket_snapshot(session, seed["ticket_id"])
        assert snap.ticket_number.startswith("2024060189")
        assert snap.state_name == "open"
        assert snap.state_type == "open"
        assert snap.queue_name == seed["queue_name"]
        assert snap.customer_user_id == f"customer890{1}@example.com"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Block 2 — sender blocklist wired into the auto worker
# ---------------------------------------------------------------------------


async def test_auto_worker_skips_blocked_sender(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=2)
    article_id = _add_article(
        mariadb_znuny_url,
        ticket_id=seed["ticket_id"],
        sender_type="customer",
        body="Help!",
        from_address="System Notifier <noreply@blocked.example>",
    )
    _insert_outbox_event(mariadb_znuny_url, ticket_id=seed["ticket_id"], article_id=article_id)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_auto_reply_policy(
                session, seed=seed, ignored_senders="noreply@blocked.example"
            )

        _patch_llm(monkeypatch, ScriptedLlm([_propose_response("reply", "should never run")]))
        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["auto_replies"] == 0

        async with factory() as session:
            state = await session.get(TiqoraAiTicketState, seed["ticket_id"])
            assert state is None or state.last_customer_article_id is None
    finally:
        await engine.dispose()


async def test_auto_worker_runs_for_non_blocked_sender(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=3)
    article_id = _add_article(
        mariadb_znuny_url,
        ticket_id=seed["ticket_id"],
        sender_type="customer",
        body="Help!",
        from_address="someone@allowed.example",
    )
    _insert_outbox_event(mariadb_znuny_url, ticket_id=seed["ticket_id"], article_id=article_id)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _setup_auto_reply_policy(
                session, seed=seed, ignored_senders="noreply@blocked.example"
            )

        _patch_llm(monkeypatch, ScriptedLlm([_propose_response("reply", "Automated answer.")]))
        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["auto_replies"] == 1
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Block 5 — update_ticket_fields state name resolution + type whitelist
# ---------------------------------------------------------------------------


def _executor(
    session: AsyncSession, *, ticket_id: int, agent_id: int, allowed_state_types_raw: str | None
) -> ToolExecutor:
    registry = ToolRegistry(autonomy=AUTONOMY_FULL)
    return ToolExecutor(
        session=session,
        sysconfig=SysConfig(session),
        registry=registry,
        ticket_id=ticket_id,
        acting_user_id=agent_id,
        pii=PiiMapper(),
        escalation_rules=None,
        allowed_state_types_raw=allowed_state_types_raw,
    )


async def test_state_name_reopens_closed_ticket(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=4, ticket_state_id=STATE_CLOSED_ID)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            executor = _executor(
                session,
                ticket_id=seed["ticket_id"],
                agent_id=seed["agent_id"],
                allowed_state_types_raw=None,
            )
            outcome = await executor.execute("update_ticket_fields", {"state": "open"})
            assert "state" in outcome.content_for_model

            row = (
                await session.execute(
                    text("SELECT ticket_state_id FROM ticket WHERE id = :id"),
                    {"id": seed["ticket_id"]},
                )
            ).first()
            assert row is not None and int(row[0]) == STATE_OPEN_ID
    finally:
        await engine.dispose()


async def test_unknown_state_name_raises(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=5)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            executor = _executor(
                session,
                ticket_id=seed["ticket_id"],
                agent_id=seed["agent_id"],
                allowed_state_types_raw=None,
            )
            with pytest.raises(ToolArgumentError):
                await executor.execute("update_ticket_fields", {"state": "not-a-real-state"})
    finally:
        await engine.dispose()


async def test_state_and_state_id_together_raises(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=6)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            executor = _executor(
                session,
                ticket_id=seed["ticket_id"],
                agent_id=seed["agent_id"],
                allowed_state_types_raw=None,
            )
            with pytest.raises(ToolArgumentError):
                await executor.execute(
                    "update_ticket_fields", {"state": "open", "state_id": STATE_OPEN_ID}
                )
    finally:
        await engine.dispose()


async def test_closed_state_id_rejected_by_default_whitelist(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=7)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            executor = _executor(
                session,
                ticket_id=seed["ticket_id"],
                agent_id=seed["agent_id"],
                allowed_state_types_raw=None,
            )
            with pytest.raises(ToolArgumentError):
                await executor.execute("update_ticket_fields", {"state_id": STATE_CLOSED_ID})
    finally:
        await engine.dispose()


async def test_closed_state_id_allowed_by_extended_whitelist(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=8)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            executor = _executor(
                session,
                ticket_id=seed["ticket_id"],
                agent_id=seed["agent_id"],
                allowed_state_types_raw='["open", "closed"]',
            )
            await executor.execute("update_ticket_fields", {"state_id": STATE_CLOSED_ID})

            row = (
                await session.execute(
                    text("SELECT ticket_state_id FROM ticket WHERE id = :id"),
                    {"id": seed["ticket_id"]},
                )
            ).first()
            assert row is not None and int(row[0]) == STATE_CLOSED_ID
    finally:
        await engine.dispose()


async def test_empty_allowed_state_types_disables_all_state_changes(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=9)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            executor = _executor(
                session,
                ticket_id=seed["ticket_id"],
                agent_id=seed["agent_id"],
                allowed_state_types_raw="[]",
            )
            with pytest.raises(ToolArgumentError):
                await executor.execute("update_ticket_fields", {"state": "open"})
    finally:
        await engine.dispose()
