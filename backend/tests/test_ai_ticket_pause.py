"""Per-ticket AI pause (``tiqora_ai_ticket_state.ai_paused_at``).

An agent can stop all *automatic* AI actions (auto-reply, triage,
auto-summary) on one ticket. Unlike ``ai_escalated_at`` the flag is never
cleared automatically. Manual Assist stays available.

Seed ids: ns=90 (auto worker band 98xx), ns=91 (summary, 98xx), ns=30
(triage band 86xx), ns=85/86 (HTTP, 96xx band). Every test cleans up after
itself so the module passes under TIQORA_STRICT_DB_LEAKS.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests import test_ai_auto_worker as aw
from tests import test_ai_triage_worker as tw
from tests.test_ai_manual_draft_async import (
    _cleanup_ticket,
    _client_for,
    _group_id,
    _to_async_url,
)
from tests.test_ai_runtime import ScriptedLlm, _propose_response
from tests.test_ai_runtime import _seed_ticket as _seed_ticket_raw
from tests.test_ai_runtime import _setup_policy as _runtime_setup_policy
from tiqora.ai import handoff
from tiqora.ai import policies as ai_policies
from tiqora.ai.auto_worker import _cap_reason, run_auto_tick
from tiqora.ai.models import AUTONOMY_FULL, TiqoraAiTicketState
from tiqora.ai.summary import auto_summary_due
from tiqora.ai.triage_worker import run_triage_tick
from tiqora.config import get_settings
from tiqora.domain.settings_store import (
    KEY_AI_GLOBAL_REPLIES_PER_HOUR,
    KEY_AI_OUTBOX_WATERMARK,
    KEY_AI_TRIAGE_WATERMARK,
    KEY_OPERATION_MODE,
)
from tiqora.domain.ticket_write_service import ArticleIn, add_article
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

PAUSE_SUBJECT = "KI-Automatik pausiert"
PAUSE_BODY = (
    "Die KI-Automatik wurde für dieses Ticket pausiert. "
    "Neue Kundennachrichten werden nicht automatisch bearbeitet."
)
UNPAUSE_SUBJECT = "KI-Automatik fortgesetzt"
UNPAUSE_BODY = (
    "Die KI-Automatik wurde für dieses Ticket fortgesetzt. "
    "Nachrichten, die während der Pause eingegangen sind, "
    "werden nicht nachträglich bearbeitet."
)


@pytest.fixture(autouse=True)
def _drop_global_settings(mariadb_znuny_url: str) -> Iterator[None]:
    """The worker helpers write global tiqora_settings rows (operation mode,
    watermarks, reply cap); remove them so the module leaves nothing behind."""
    yield
    engine = create_engine(mariadb_znuny_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM tiqora_settings WHERE `key` IN (:a, :b, :c, :d)"),
                {
                    "a": KEY_AI_GLOBAL_REPLIES_PER_HOUR,
                    "b": KEY_AI_OUTBOX_WATERMARK,
                    "c": KEY_AI_TRIAGE_WATERMARK,
                    "d": KEY_OPERATION_MODE,
                },
            )
    except ProgrammingError:
        pass  # table not created yet (only the DB-less unit test ran so far)
    finally:
        engine.dispose()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _drop_aw_provider(sync_url: str, queue_id: int) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM tiqora_llm_provider WHERE name = :n"),
            {"n": f"fake-auto-provider-{queue_id}"},
        )
    engine.dispose()


def _cleanup_aw(sync_url: str, seed: dict[str, Any], ns: int) -> None:
    _cleanup_ticket(
        sync_url,
        ticket_id=seed["ticket_id"],
        queue_id=seed["queue_id"],
        agent_id=seed["agent_id"],
        group_id=9830 + ns,
    )
    _drop_aw_provider(sync_url, seed["queue_id"])


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


async def test_cap_reason_ai_paused_unit() -> None:
    state = SimpleNamespace(
        ai_escalated_at=None,
        ai_paused_at=_now(),
        auto_reply_count=0,
        clarification_count=0,
    )
    reason = await _cap_reason(None, ticket_id=1, queue_id=1, policy=None, state=state)  # type: ignore[arg-type]
    assert reason == "ai_paused"


async def test_paused_ticket_is_not_auto_answered_and_event_is_consumed(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ns = 90
    seed = aw._seed_ticket(mariadb_znuny_url, ns=ns)
    article_id = aw._add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Hallo?"
    )
    aw._insert_outbox_event(
        mariadb_znuny_url,
        ticket_id=seed["ticket_id"],
        event_type="ArticleCreate",
        article_id=article_id,
    )
    engine = create_async_engine(aw._mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await aw._setup_policy(session, seed=seed, autonomy="full")
            session.add(TiqoraAiTicketState(ticket_id=seed["ticket_id"], ai_paused_at=_now()))
            await session.commit()

        llm = ScriptedLlm([_propose_response("reply", "Should not run.")])
        aw._patch_llm(monkeypatch, llm)
        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["auto_replies"] == 0
        assert totals["events"] >= 1  # consumed, not stuck

        # Unpausing must not retroactively process the consumed event.
        async with factory() as session:
            await handoff.clear_ai_paused(session, seed["ticket_id"])
            await session.commit()
        aw._patch_llm(monkeypatch, ScriptedLlm([_propose_response("reply", "Late.")]))
        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["auto_replies"] == 0
    finally:
        await engine.dispose()
        _cleanup_aw(mariadb_znuny_url, seed, ns)


async def test_unpaused_ticket_is_still_answered(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate keys on the flag, not on the state row's mere existence."""
    ns = 92
    seed = aw._seed_ticket(mariadb_znuny_url, ns=ns)
    article_id = aw._add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Hallo?"
    )
    aw._insert_outbox_event(
        mariadb_znuny_url,
        ticket_id=seed["ticket_id"],
        event_type="ArticleCreate",
        article_id=article_id,
    )
    engine = create_async_engine(aw._mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await aw._setup_policy(session, seed=seed, autonomy="full")
            session.add(TiqoraAiTicketState(ticket_id=seed["ticket_id"], ai_paused_at=None))
            await session.commit()
        aw._patch_llm(monkeypatch, ScriptedLlm([_propose_response("reply", "Gerne!")]))
        totals = await run_auto_tick(settings=get_settings(), session_factory=factory)
        assert totals["auto_replies"] == 1
    finally:
        await engine.dispose()
        _cleanup_aw(mariadb_znuny_url, seed, ns)


async def test_runtime_refuses_auto_run_on_paused_ticket(mariadb_znuny_url: str) -> None:
    """Race guard: a pause landing between the worker gate and the run."""
    from tiqora.ai.runtime import TRIGGER_AUTO, PolicyDisabledError, run_ticket_agent

    ns = 93
    seed = aw._seed_ticket(mariadb_znuny_url, ns=ns)
    engine = create_async_engine(aw._mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await aw._setup_policy(session, seed=seed, autonomy="full")
            session.add(TiqoraAiTicketState(ticket_id=seed["ticket_id"], ai_paused_at=_now()))
            await session.commit()
        llm = ScriptedLlm([_propose_response("reply", "Should not run.")])
        async with factory() as session:
            with pytest.raises(PolicyDisabledError):
                await run_ticket_agent(
                    session,
                    settings=get_settings(),
                    llm=llm,
                    ticket_id=seed["ticket_id"],
                    trigger=TRIGGER_AUTO,
                    acting_user_id=None,
                    run_id="pause-test",
                    worker_instance="test",
                    kb_bundle=None,
                    kb_search_fn=None,
                    kb_get_article_fn=None,
                )
    finally:
        await engine.dispose()
        _cleanup_aw(mariadb_znuny_url, seed, ns)


async def test_triage_skips_paused_ticket(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ns = 30
    ids = tw._seed(mariadb_znuny_url, ns=ns, created=datetime.now())
    engine = create_async_engine(tw._mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = tw.TriageLlm(queue_key_value=tw.queue_key(ids["target_queue_id"]), confidence=95)
    tw._patch_llm(monkeypatch, llm)
    try:
        async with factory() as session:
            await tw._setup_policies(session, ids=ids, auto_threshold=80)
            session.add(TiqoraAiTicketState(ticket_id=ids["ticket_id"], ai_paused_at=_now()))
            await session.commit()
        await tw._reset_watermark(factory, mariadb_znuny_url)
        article_id = tw._add_article(
            mariadb_znuny_url, ticket_id=ids["ticket_id"], sender_type="customer", body="hilfe"
        )
        tw._insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)

        await run_triage_tick(session_factory=factory)

        assert llm.calls == 0
        assert tw._ticket_queue(mariadb_znuny_url, ids["ticket_id"]) == ids["source_queue_id"]
        assert tw._triage_row(mariadb_znuny_url, ids["ticket_id"]) is None
    finally:
        await engine.dispose()
        tw._cleanup(mariadb_znuny_url, ns)


async def test_triage_skip_reason_is_ai_paused(mariadb_znuny_url: str) -> None:
    from tiqora.ai.triage_worker import _skip_reason

    ns = 31
    ids = tw._seed(mariadb_znuny_url, ns=ns, created=datetime.now())
    engine = create_async_engine(tw._mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        article_id = tw._add_article(
            mariadb_znuny_url, ticket_id=ids["ticket_id"], sender_type="customer", body="hilfe"
        )
        async with factory() as session:
            assert (
                await _skip_reason(session, ticket_id=ids["ticket_id"], article_id=article_id)
                is None
            )
            session.add(TiqoraAiTicketState(ticket_id=ids["ticket_id"], ai_paused_at=_now()))
            await session.commit()
        async with factory() as session:
            assert (
                await _skip_reason(session, ticket_id=ids["ticket_id"], article_id=article_id)
                == "ai_paused"
            )
    finally:
        await engine.dispose()
        tw._cleanup(mariadb_znuny_url, ns)


async def test_auto_summary_not_due_for_paused_ticket(mariadb_znuny_url: str) -> None:
    ns = 91
    seed = aw._seed_ticket(mariadb_znuny_url, ns=ns)
    aw._add_article(
        mariadb_znuny_url, ticket_id=seed["ticket_id"], sender_type="customer", body="Help!"
    )
    engine = create_async_engine(aw._mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await ai_policies.create_queue_policy(
                session,
                change_by=1,
                queue_id=seed["queue_id"],
                enabled_summary=True,
                summary_article_threshold=1,
            )
        async with factory() as session:
            assert await auto_summary_due(session, seed["ticket_id"]) is True
        async with factory() as session:
            await handoff.set_ai_paused(session, seed["ticket_id"], seed["agent_id"])
            await session.commit()
        async with factory() as session:
            assert await auto_summary_due(session, seed["ticket_id"]) is False
        async with factory() as session:
            await handoff.clear_ai_paused(session, seed["ticket_id"])
            await session.commit()
        async with factory() as session:
            assert await auto_summary_due(session, seed["ticket_id"]) is True
    finally:
        await engine.dispose()
        _cleanup_aw(mariadb_znuny_url, seed, ns)


# ---------------------------------------------------------------------------
# Domain behaviour
# ---------------------------------------------------------------------------


async def test_visible_agent_reply_clears_escalation_but_not_pause(
    mariadb_znuny_url: str,
) -> None:
    ns = 94
    seed = aw._seed_ticket(mariadb_znuny_url, ns=ns)
    engine = create_async_engine(aw._mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(
                TiqoraAiTicketState(
                    ticket_id=seed["ticket_id"], ai_paused_at=_now(), ai_escalated_at=_now()
                )
            )
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
            assert state.ai_escalated_at is None
            assert state.ai_paused_at is not None
    finally:
        await engine.dispose()
        _cleanup_aw(mariadb_znuny_url, seed, ns)


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


async def _seed_http(mariadb_znuny_url: str, *, ns: int, monkeypatch: pytest.MonkeyPatch):
    seed = _seed_ticket_raw(mariadb_znuny_url, ns=ns)
    async_engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as session:
        await _runtime_setup_policy(
            session, seed=seed, autonomy=AUTONOMY_FULL, enabled_manual_assist=True
        )
    await async_engine.dispose()
    client, client_engine = await _client_for(
        mariadb_znuny_url,
        user_id=seed["agent_id"],
        login=f"agent.aipause.96{ns}",
        monkeypatch=monkeypatch,
    )
    return seed, client, client_engine


def _notes(sync_url: str, ticket_id: int) -> list[tuple[str, str, int]]:
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT m.a_subject, m.a_body, a.is_visible_for_customer FROM article a"
                " JOIN article_data_mime m ON m.article_id = a.id"
                " WHERE a.ticket_id = :tid ORDER BY a.id"
            ),
            {"tid": ticket_id},
        ).all()
    engine.dispose()
    return [(r[0], r[1], int(r[2])) for r in rows]


def _state_row(sync_url: str, ticket_id: int) -> Any:
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT ai_paused_at, ai_paused_by, ai_escalated_at"
                " FROM tiqora_ai_ticket_state WHERE ticket_id = :tid"
            ),
            {"tid": ticket_id},
        ).first()
    engine.dispose()
    return row


def _teardown_args(seed: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticket_id": seed["ticket_id"],
        "queue_id": seed["queue_id"],
        "agent_id": seed["agent_id"],
        "group_id": _group_id(seed),
    }


async def test_pause_route_sets_flag_writes_note_and_is_idempotent(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, client, client_engine = await _seed_http(
        mariadb_znuny_url, ns=85, monkeypatch=monkeypatch
    )
    tid = seed["ticket_id"]
    try:
        resp = await client.post(f"/api/v1/tickets/{tid}/ai/pause")
        assert resp.status_code == 204

        row = _state_row(mariadb_znuny_url, tid)
        assert row is not None
        assert row[0] is not None
        assert row[1] == seed["agent_id"]
        notes = [n for n in _notes(mariadb_znuny_url, tid) if n[0] == PAUSE_SUBJECT]
        assert notes == [(PAUSE_SUBJECT, PAUSE_BODY, 0)]

        # GET exposes the flag and the resolved name.
        state = (await client.get(f"/api/v1/tickets/{tid}/ai")).json()
        assert state["ai_paused_at"] is not None
        assert state["ai_paused_by_name"]

        first_ts = row[0]
        resp = await client.post(f"/api/v1/tickets/{tid}/ai/pause")
        assert resp.status_code == 204
        assert len([n for n in _notes(mariadb_znuny_url, tid) if n[0] == PAUSE_SUBJECT]) == 1
        assert _state_row(mariadb_znuny_url, tid)[0] == first_ts
    finally:
        await client.aclose()
        await client_engine.dispose()
        _cleanup_ticket(mariadb_znuny_url, **_teardown_args(seed))


async def test_get_state_without_pause_has_null_fields(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, client, client_engine = await _seed_http(
        mariadb_znuny_url, ns=86, monkeypatch=monkeypatch
    )
    try:
        state = (await client.get(f"/api/v1/tickets/{seed['ticket_id']}/ai")).json()
        assert state["ai_paused_at"] is None
        assert state["ai_paused_by_name"] is None
    finally:
        await client.aclose()
        await client_engine.dispose()
        _cleanup_ticket(mariadb_znuny_url, **_teardown_args(seed))


async def test_unpause_route_clears_flag_and_is_idempotent(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, client, client_engine = await _seed_http(
        mariadb_znuny_url, ns=85, monkeypatch=monkeypatch
    )
    tid = seed["ticket_id"]
    try:
        assert (await client.post(f"/api/v1/tickets/{tid}/ai/pause")).status_code == 204
        assert (await client.post(f"/api/v1/tickets/{tid}/ai/unpause")).status_code == 204

        row = _state_row(mariadb_znuny_url, tid)
        assert row[0] is None
        assert row[1] is None
        notes = [n for n in _notes(mariadb_znuny_url, tid) if n[0] == UNPAUSE_SUBJECT]
        assert notes == [(UNPAUSE_SUBJECT, UNPAUSE_BODY, 0)]

        assert (await client.post(f"/api/v1/tickets/{tid}/ai/unpause")).status_code == 204
        assert len([n for n in _notes(mariadb_znuny_url, tid) if n[0] == UNPAUSE_SUBJECT]) == 1
    finally:
        await client.aclose()
        await client_engine.dispose()
        _cleanup_ticket(mariadb_znuny_url, **_teardown_args(seed))


async def test_pause_and_escalation_are_independent(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, client, client_engine = await _seed_http(
        mariadb_znuny_url, ns=85, monkeypatch=monkeypatch
    )
    tid = seed["ticket_id"]
    try:
        engine = create_engine(mariadb_znuny_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tiqora_ai_ticket_state (ticket_id, ai_escalated_at)"
                    " VALUES (:tid, :at)"
                ),
                {"tid": tid, "at": _now()},
            )
        engine.dispose()

        # pause then unpause leaves the escalation alone
        assert (await client.post(f"/api/v1/tickets/{tid}/ai/pause")).status_code == 204
        assert (await client.post(f"/api/v1/tickets/{tid}/ai/unpause")).status_code == 204
        row = _state_row(mariadb_znuny_url, tid)
        assert row[0] is None
        assert row[2] is not None

        # resume (escalation) leaves the pause alone
        assert (await client.post(f"/api/v1/tickets/{tid}/ai/pause")).status_code == 204
        assert (await client.post(f"/api/v1/tickets/{tid}/ai/resume")).status_code == 204
        row = _state_row(mariadb_znuny_url, tid)
        assert row[0] is not None
        assert row[2] is None
    finally:
        await client.aclose()
        await client_engine.dispose()
        _cleanup_ticket(mariadb_znuny_url, **_teardown_args(seed))


@pytest.mark.parametrize("action", ["pause", "unpause"])
async def test_pause_routes_forbidden_without_note_permission(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    seed, client, client_engine = await _seed_http(
        mariadb_znuny_url, ns=85, monkeypatch=monkeypatch
    )
    try:
        # A user who can see the ticket (ro on its group) but has no note permission.
        reader_id = 9500 + 85
        engine = create_engine(mariadb_znuny_url)
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": reader_id})
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, 'agent.aipause.reader', 'x', 'Read', 'Only', 1,"
                    " current_timestamp, 1, current_timestamp, 1)"
                ),
                {"id": reader_id},
            )
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, :gid, 'ro', current_timestamp, 1, current_timestamp, 1)"
                ),
                {"uid": reader_id, "gid": _group_id(seed)},
            )
        engine.dispose()
        outsider, outsider_engine = await _client_for(
            mariadb_znuny_url,
            user_id=reader_id,
            login="agent.aipause.reader",
            monkeypatch=monkeypatch,
        )
        try:
            resp = await outsider.post(f"/api/v1/tickets/{seed['ticket_id']}/ai/{action}")
            assert resp.status_code == 403
            assert _state_row(mariadb_znuny_url, seed["ticket_id"]) is None
        finally:
            await outsider.aclose()
            await outsider_engine.dispose()
    finally:
        await client.aclose()
        await client_engine.dispose()
        _cleanup_ticket(mariadb_znuny_url, **_teardown_args(seed))
        engine = create_engine(mariadb_znuny_url)
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": 9500 + 85})
        engine.dispose()


async def test_set_ai_paused_survives_state_row_created_concurrently(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The auto worker creates the state row between our existence check and
    our insert: the pause must still apply (and errors must not be swallowed)."""
    ns = 95
    seed = aw._seed_ticket(mariadb_znuny_url, ns=ns)
    tid = seed["ticket_id"]
    engine = create_async_engine(aw._mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    real_exists = handoff._pause_row_exists

    async def racing_exists(session: Any, ticket_id: int) -> bool:
        assert await real_exists(session, ticket_id) is False
        # Same connection: a second connection would just block on the
        # update's gap lock. The row is "the worker's", the PK clash is real.
        await session.execute(
            text("INSERT INTO tiqora_ai_ticket_state (ticket_id) VALUES (:tid)"),
            {"tid": ticket_id},
        )
        return False

    monkeypatch.setattr(handoff, "_pause_row_exists", racing_exists)
    try:
        async with factory() as session:
            await handoff.set_ai_paused(session, tid, seed["agent_id"])
            await session.commit()
        row = _state_row(mariadb_znuny_url, tid)
        assert row is not None
        assert row[0] is not None
        assert row[1] == seed["agent_id"]
    finally:
        await engine.dispose()
        _cleanup_aw(mariadb_znuny_url, seed, ns)


async def test_set_and_clear_ai_paused_are_idempotent_and_keep_original(
    mariadb_znuny_url: str,
) -> None:
    ns = 96
    seed = aw._seed_ticket(mariadb_znuny_url, ns=ns)
    tid = seed["ticket_id"]
    engine = create_async_engine(aw._mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await handoff.clear_ai_paused(session, tid)  # no row: no-op
            await handoff.set_ai_paused(session, tid, 11)
            await session.commit()
        first = _state_row(mariadb_znuny_url, tid)
        async with factory() as session:
            await handoff.set_ai_paused(session, tid, 22)
            await session.commit()
        assert _state_row(mariadb_znuny_url, tid)[:2] == first[:2]
        assert first[1] == 11
    finally:
        await engine.dispose()
        _cleanup_aw(mariadb_znuny_url, seed, ns)
