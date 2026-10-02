"""Per-ticket AI autopilot: stop, or start again for N automatic replies.

``tiqora_ai_ticket_state.ai_grant_remaining`` is a release an agent grants by
hand. While it is set it replaces the queue's per-ticket caps
(``max_auto_replies``/``max_clarifications``); every run that writes to the
customer (sent or drafted, identity questions excepted) uses one, and the
customer message after the last one hands the ticket to the team again.

Seed ids: ns=60-66 in the auto worker band (98xx), ns=77-79 in the runtime
band (96xx, HTTP). Every test cleans up after itself.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests import test_ai_auto_worker as aw
from tests._llm_routing_helpers import routing_cleanup_statements
from tests.test_ai_manual_draft_async import _cleanup_ticket, _client_for, _to_async_url
from tests.test_ai_runtime import ScriptedLlm, _propose_response
from tests.test_ai_runtime import _seed_ticket as _seed_ticket_raw
from tests.test_ai_runtime import _setup_policy as _runtime_setup_policy
from tests.test_ai_ticket_pause import (  # noqa: F401 — the fixture is autouse here too
    _cleanup_aw,
    _drop_global_settings,
    _notes,
    _teardown_args,
)
from tiqora.ai import handoff
from tiqora.ai.auto_worker import _cap_reason, run_auto_tick
from tiqora.ai.models import AUTONOMY_FULL, TiqoraAiTicketState
from tiqora.ai.runtime import STATUS_DRAFTED, STATUS_SENT, AgentRunResult
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    add_article,
    start_ai_autopilot,
    stop_ai_autopilot,
)
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _state(**kw: Any) -> SimpleNamespace:
    base = {
        "ai_escalated_at": None,
        "ai_paused_at": None,
        "auto_reply_count": 0,
        "clarification_count": 0,
        "ai_grant_remaining": None,
    }
    return SimpleNamespace(**{**base, **kw})


_POLICY = SimpleNamespace(
    max_auto_replies=5, max_clarifications=2, max_replies_per_hour=None, budget_tokens_day=None
)


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


async def test_grant_replaces_the_ticket_caps(mariadb_znuny_url: str) -> None:
    sync_engine = create_engine(mariadb_znuny_url)
    TiqoraBase.metadata.create_all(sync_engine)  # _cap_reason reads tiqora_settings
    sync_engine.dispose()
    engine = create_async_engine(aw._mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:

            async def reason(**kw: Any) -> str | None:
                return await _cap_reason(
                    session, ticket_id=1, queue_id=1, policy=_POLICY, state=_state(**kw)
                )

            assert await reason(clarification_count=2) == "max_clarifications"
            assert await reason(clarification_count=2, ai_grant_remaining=1) is None
            assert await reason(auto_reply_count=9, ai_grant_remaining=3) is None
            assert await reason(ai_grant_remaining=0) == "grant_used"
            # A pause or a handoff still wins over a release.
            assert await reason(ai_grant_remaining=2, ai_paused_at=_now()) == "ai_paused"
            assert (
                await reason(ai_grant_remaining=2, ai_escalated_at=_now()) == "escalated_to_human"
            )
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


async def _customer_message(sync_url: str, seed: dict[str, Any], body: str) -> int:
    article_id = aw._add_article(
        sync_url, ticket_id=seed["ticket_id"], sender_type="customer", body=body
    )
    aw._insert_outbox_event(
        sync_url, ticket_id=seed["ticket_id"], event_type="ArticleCreate", article_id=article_id
    )
    return article_id


async def test_release_answers_n_messages_then_hands_over(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prod ticket 43142: the clarification cap stopped the AI. A release of 2
    answers the next two customer messages despite the cap, the third goes to
    the team with a note, and the release is gone."""
    ns = 60
    seed = aw._seed_ticket(mariadb_znuny_url, ns=ns)
    engine = create_async_engine(aw._mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await aw._setup_policy(session, seed=seed, max_clarifications=2)
            session.add(
                TiqoraAiTicketState(
                    ticket_id=seed["ticket_id"],
                    clarification_count=2,
                    ai_grant_remaining=2,
                    ai_grant_total=2,
                )
            )
            await session.commit()

        for i in range(2):
            await _customer_message(mariadb_znuny_url, seed, f"Frage {i}")
            aw._patch_llm(
                monkeypatch, ScriptedLlm([_propose_response("clarify", f"Rückfrage {i}")])
            )
            totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
            assert totals["auto_replies"] == 1

        async with factory() as session:
            state = await session.get(TiqoraAiTicketState, seed["ticket_id"])
            assert state is not None
            assert state.ai_grant_remaining == 0

        await _customer_message(mariadb_znuny_url, seed, "Und jetzt?")
        llm = ScriptedLlm([_propose_response("clarify", "Sollte nicht laufen.")])
        aw._patch_llm(monkeypatch, llm)
        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["auto_replies"] == 0
        assert llm.calls == 0

        async with factory() as session:
            state = await session.get(
                TiqoraAiTicketState, seed["ticket_id"], populate_existing=True
            )
            assert state is not None
            assert state.ai_escalated_at is not None
            assert state.ai_escalated_reason == "grant_used"
            assert state.ai_grant_remaining is None
            assert state.ai_grant_total is None
        notes = [n for n in _notes(mariadb_znuny_url, seed["ticket_id"]) if n[2] == 0]
        assert notes[-1][1].startswith("Escalated to human:")
        assert "2 automatic replies" in notes[-1][1]
    finally:
        await engine.dispose()
        _cleanup_aw(mariadb_znuny_url, seed, ns)


async def test_start_answers_the_open_message_once(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Start with answer_latest on a handed-over ticket: pause and handoff are
    lifted, the unanswered customer message gets exactly one automatic
    answer, which uses one of the N replies."""
    ns = 61
    seed = aw._seed_ticket(mariadb_znuny_url, ns=ns)
    engine = create_async_engine(aw._mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await aw._setup_policy(session, seed=seed, max_clarifications=2)
        open_id = await _customer_message(mariadb_znuny_url, seed, "Ich bin verloren")
        async with factory() as session:
            session.add(
                TiqoraAiTicketState(
                    ticket_id=seed["ticket_id"],
                    clarification_count=2,
                    ai_escalated_at=_now(),
                    ai_escalated_reason="max_clarifications",
                    ai_paused_at=_now(),
                    last_customer_article_id=open_id,
                )
            )
            await session.commit()
        # The original event was consumed while the ticket was handed over.
        aw._patch_llm(monkeypatch, ScriptedLlm([]))
        await run_auto_tick(settings=get_settings(), session_factory=factory)

        async with factory() as session:
            await start_ai_autopilot(
                session,
                ticket_id=seed["ticket_id"],
                user_id=seed["agent_id"],
                sysconfig=SysConfig(session),
                runs=3,
                answer_latest=True,
            )
            await session.commit()

        async with factory() as session:
            state = await session.get(TiqoraAiTicketState, seed["ticket_id"])
            assert state is not None
            assert state.ai_paused_at is None
            assert state.ai_escalated_at is None
            assert state.ai_escalated_reason is None
            assert (state.ai_grant_remaining, state.ai_grant_total) == (3, 3)
            assert state.ai_grant_by == seed["agent_id"]

        llm = ScriptedLlm([_propose_response("reply", "Hier ist die Lösung.")])
        aw._patch_llm(monkeypatch, llm)
        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["auto_replies"] == 1
        # A second tick must not answer the same message again.
        aw._patch_llm(monkeypatch, ScriptedLlm([]))
        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["auto_replies"] == 0

        async with factory() as session:
            state = await session.get(TiqoraAiTicketState, seed["ticket_id"])
            assert state is not None
            assert state.ai_grant_remaining == 2
        subjects = [n[0] for n in _notes(mariadb_znuny_url, seed["ticket_id"]) if n[2] == 0]
        assert "KI-Autopilot eingeschaltet" in subjects
    finally:
        await engine.dispose()
        _cleanup_aw(mariadb_znuny_url, seed, ns)


async def test_start_does_not_answer_a_message_an_agent_already_answered(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ns = 62
    seed = aw._seed_ticket(mariadb_znuny_url, ns=ns)
    engine = create_async_engine(aw._mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await aw._setup_policy(session, seed=seed)
        await _customer_message(mariadb_znuny_url, seed, "Hilfe")
        aw._patch_llm(monkeypatch, ScriptedLlm([_propose_response("reply", "Erste Antwort.")]))
        await run_auto_tick(settings=get_settings(), session_factory=factory)

        async with factory() as session:
            await start_ai_autopilot(
                session,
                ticket_id=seed["ticket_id"],
                user_id=seed["agent_id"],
                sysconfig=SysConfig(session),
                runs=2,
                answer_latest=True,
            )
            await session.commit()
        llm = ScriptedLlm([_propose_response("reply", "Doppelt.")])
        aw._patch_llm(monkeypatch, llm)
        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["auto_replies"] == 0
        assert llm.calls == 0
    finally:
        await engine.dispose()
        _cleanup_aw(mariadb_znuny_url, seed, ns)


async def test_stop_ends_a_release_and_writes_one_note(mariadb_znuny_url: str) -> None:
    ns = 63
    seed = aw._seed_ticket(mariadb_znuny_url, ns=ns)
    engine = create_async_engine(aw._mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(
                TiqoraAiTicketState(
                    ticket_id=seed["ticket_id"], ai_grant_remaining=2, ai_grant_total=3
                )
            )
            await session.commit()
        for _ in range(2):
            async with factory() as session:
                await stop_ai_autopilot(
                    session,
                    ticket_id=seed["ticket_id"],
                    user_id=seed["agent_id"],
                    sysconfig=SysConfig(session),
                )
                await session.commit()
        async with factory() as session:
            state = await session.get(TiqoraAiTicketState, seed["ticket_id"])
            assert state is not None
            assert state.ai_paused_at is not None
            assert state.ai_grant_remaining is None
            assert state.ai_grant_total is None
        subjects = [n[0] for n in _notes(mariadb_znuny_url, seed["ticket_id"])]
        assert subjects.count("KI-Automatik pausiert") == 1
    finally:
        await engine.dispose()
        _cleanup_aw(mariadb_znuny_url, seed, ns)


async def test_visible_agent_reply_keeps_the_handoff(mariadb_znuny_url: str) -> None:
    """The handoff now stays until someone switches the autopilot back on —
    an agent's reply used to lift it silently, and the AI then answered the
    customer's next message under the queue caps again."""
    ns = 64
    seed = aw._seed_ticket(mariadb_znuny_url, ns=ns)
    engine = create_async_engine(aw._mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(TiqoraAiTicketState(ticket_id=seed["ticket_id"], ai_escalated_at=_now()))
            await session.commit()
        async with factory() as session:
            await add_article(
                session,
                ticket_id=seed["ticket_id"],
                article=ArticleIn(
                    sender_type="agent",
                    is_visible_for_customer=True,
                    subject="Re: Help",
                    body="Antwort",
                    channel="note",
                ),
                user_id=seed["agent_id"],
                sysconfig=SysConfig(session),
            )
            await session.commit()
        async with factory() as session:
            state = await session.get(TiqoraAiTicketState, seed["ticket_id"])
            assert state is not None
            assert state.ai_escalated_at is not None
    finally:
        await engine.dispose()
        _cleanup_aw(mariadb_znuny_url, seed, ns)


async def test_any_handoff_ends_a_release(mariadb_znuny_url: str) -> None:
    ns = 65
    seed = aw._seed_ticket(mariadb_znuny_url, ns=ns)
    engine = create_async_engine(aw._mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(
                TiqoraAiTicketState(
                    ticket_id=seed["ticket_id"], ai_grant_remaining=2, ai_grant_total=2
                )
            )
            await session.commit()
        async with factory() as session:
            await handoff.mark_ai_escalated(session, seed["ticket_id"], reason="escalate_to_human")
            await session.commit()
        async with factory() as session:
            state = await session.get(TiqoraAiTicketState, seed["ticket_id"])
            assert state is not None
            assert state.ai_escalated_reason == "escalate_to_human"
            assert state.ai_grant_remaining is None
    finally:
        await engine.dispose()
        _cleanup_aw(mariadb_znuny_url, seed, ns)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (AgentRunResult(status=STATUS_SENT), 1),
        (AgentRunResult(status=STATUS_DRAFTED), 1),
        (AgentRunResult(status=STATUS_SENT, identity_check=True), 2),
        (AgentRunResult(status="skipped"), 2),
    ],
)
async def test_only_customer_answers_use_the_release(
    mariadb_znuny_url: str, result: AgentRunResult, expected: int
) -> None:
    from tiqora.ai.auto_worker import _consume_grant

    ns = 66
    seed = aw._seed_ticket(mariadb_znuny_url, ns=ns)
    engine = create_async_engine(aw._mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(
                TiqoraAiTicketState(
                    ticket_id=seed["ticket_id"], ai_grant_remaining=2, ai_grant_total=2
                )
            )
            await session.commit()
        async with factory() as session:
            await _consume_grant(session, seed["ticket_id"], result)
        async with factory() as session:
            state = await session.get(TiqoraAiTicketState, seed["ticket_id"])
            assert state is not None
            assert state.ai_grant_remaining == expected
    finally:
        await engine.dispose()
        _cleanup_aw(mariadb_znuny_url, seed, ns)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


async def _seed_http(
    mariadb_znuny_url: str, *, ns: int, monkeypatch: pytest.MonkeyPatch, auto_reply: bool = True
):
    seed = _seed_ticket_raw(mariadb_znuny_url, ns=ns)
    async_engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as session:
        await _runtime_setup_policy(
            session, seed=seed, autonomy=AUTONOMY_FULL, enabled_auto_reply=auto_reply
        )
    await async_engine.dispose()
    client, client_engine = await _client_for(
        mariadb_znuny_url,
        user_id=seed["agent_id"],
        login=f"agent.aiautopilot.96{ns}",
        monkeypatch=monkeypatch,
    )
    return seed, client, client_engine


def _cleanup_http(sync_url: str, seed: dict[str, Any]) -> None:
    _cleanup_ticket(sync_url, **_teardown_args(seed))
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        name = f"fake-provider-{seed['queue_id']}"
        for stmt, params in routing_cleanup_statements(name):
            conn.execute(text(stmt), params)
        conn.execute(text("DELETE FROM tiqora_llm_provider WHERE name = :n"), {"n": name})
    engine.dispose()


async def test_autopilot_route_round_trip(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, client, client_engine = await _seed_http(
        mariadb_znuny_url, ns=77, monkeypatch=monkeypatch
    )
    tid = seed["ticket_id"]
    url = f"/api/v1/tickets/{tid}/ai/autopilot"
    try:
        state = (await client.get(f"/api/v1/tickets/{tid}/ai")).json()
        assert state["autopilot"]["mode"] == "active"
        assert state["autopilot"]["grant_remaining"] is None

        resp = await client.post(url, json={"action": "stop"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "stopped"
        assert body["by_name"]

        resp = await client.post(url, json={"action": "start", "runs": 3})
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "active"
        assert (body["grant_remaining"], body["grant_total"]) == (3, 3)
        assert body["by_name"]

        engine = create_engine(mariadb_znuny_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE tiqora_ai_ticket_state SET ai_escalated_at = :at,"
                    " ai_escalated_reason = 'max_clarifications' WHERE ticket_id = :tid"
                ),
                {"at": _now(), "tid": tid},
            )
        engine.dispose()
        auto = (await client.get(f"/api/v1/tickets/{tid}/ai")).json()["autopilot"]
        assert auto["mode"] == "handed_over"
        assert auto["reason"] == "max_clarifications"
    finally:
        await client.aclose()
        await client_engine.dispose()
        _cleanup_http(mariadb_znuny_url, seed)


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "start"},
        {"action": "start", "runs": 0},
        {"action": "start", "runs": 21},
        {"action": "maybe"},
    ],
)
async def test_autopilot_route_rejects_bad_input(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    seed, client, client_engine = await _seed_http(
        mariadb_znuny_url, ns=78, monkeypatch=monkeypatch
    )
    try:
        resp = await client.post(f"/api/v1/tickets/{seed['ticket_id']}/ai/autopilot", json=payload)
        assert resp.status_code == 422
    finally:
        await client.aclose()
        await client_engine.dispose()
        _cleanup_http(mariadb_znuny_url, seed)


async def test_autopilot_unavailable_without_auto_reply(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, client, client_engine = await _seed_http(
        mariadb_znuny_url, ns=79, monkeypatch=monkeypatch, auto_reply=False
    )
    tid = seed["ticket_id"]
    try:
        state = (await client.get(f"/api/v1/tickets/{tid}/ai")).json()
        assert state["autopilot"]["mode"] == "unavailable"
        resp = await client.post(
            f"/api/v1/tickets/{tid}/ai/autopilot", json={"action": "start", "runs": 2}
        )
        assert resp.status_code == 409
    finally:
        await client.aclose()
        await client_engine.dispose()
        _cleanup_http(mariadb_znuny_url, seed)
