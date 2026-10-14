"""Queue overview, team chat status, chat queue and AI status providers (both dialects).

Seed band 61280-61319: queue 61281 ("Q1c", A's group, no tickets of its own except the seeded
ones), tickets 61290-61299, chat configs 61300-61302, a provider named ``dash3-llm``. The world
of ``_dashboard3_helpers`` supplies users A (ro+rw Q1), B (ro Q2), ADMIN and queues Q1/Q2.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, delete, text

from tests._dashboard3_helpers import ADMIN, GROUP_1, Q1, Q2, A, B, Env3, FakeRedis, open_env3
from tests._dashboard_helpers import run_widget_fn
from tests._webchat_fixtures import insert_config
from tiqora.ai import policies as ai_policies
from tiqora.ai import providers as ai_providers
from tiqora.ai import usage as ai_usage
from tiqora.api.v1.agents import online_key
from tiqora.config import get_settings
from tiqora.dashboard.filters import DashboardFilters
from tiqora.dashboard.providers import DASHBOARD_PROVIDERS, core_providers
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.extensions.registry import Registry

pytestmark = pytest.mark.db

Q1C = 61281
T_BASE = 61290
CONFIG_ID = 61300
PROVIDER_NAME = "dash3-llm"
NOW = datetime(2026, 10, 1, 8, 0)
#: key -> (ticket id, queue, state name, escalation offset seconds or None)
TICKETS: dict[str, tuple[int, int, str, int | None]] = {
    "q1_new": (T_BASE + 0, Q1, "new", None),
    "q1_open": (T_BASE + 1, Q1, "open", None),
    "q1_breach": (T_BASE + 2, Q1, "open", -3600),
    "q1c_open": (T_BASE + 3, Q1C, "open", None),
    "q2_open": (T_BASE + 4, Q2, "open", None),
    "chat_q1": (T_BASE + 5, Q1, "open", None),
    "chat_q2": (T_BASE + 6, Q2, "open", None),
}


@dataclass
class W:
    id: str
    type_key: str
    settings: dict[str, Any] = field(default_factory=dict)
    type_version: int = 1
    pinned: bool = False


def registry() -> Registry:
    reg = Registry()
    for p in core_providers():
        reg.add(DASHBOARD_PROVIDERS, p)
    reg.freeze()
    return reg


def _tid(key: str) -> int:
    return TICKETS[key][0]


def _wipe(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM tiqora_chat_message WHERE session_id IN"
                " (SELECT id FROM tiqora_chat_session WHERE config_id BETWEEN 61300 AND 61302)"
            )
        )
        conn.execute(
            text("DELETE FROM tiqora_chat_session WHERE config_id BETWEEN 61300 AND 61302")
        )
        conn.execute(text("DELETE FROM tiqora_chat_config WHERE id BETWEEN 61300 AND 61302"))
        conn.execute(
            text("DELETE FROM tiqora_ai_usage WHERE queue_id IN (:a, :b) OR model = 'dash3-model'"),
            {"a": Q1, "b": Q2},
        )
        conn.execute(
            text("DELETE FROM tiqora_ai_queue_policy WHERE queue_id IN (:a, :b)"),
            {"a": Q1, "b": Q2},
        )
        conn.execute(text("DELETE FROM tiqora_llm_provider WHERE name = :n"), {"n": PROVIDER_NAME})
        # recording usage announces an exhausted budget: outbox row + "announced" marker
        conn.execute(
            text(
                "DELETE FROM tiqora_event_outbox WHERE event_type = 'AiLimitReached'"
                " AND payload LIKE '%dash3%'"
            )
        )
        conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key.like("ai.limit_alert.%")))
        conn.execute(text("DELETE FROM ticket WHERE id BETWEEN 61290 AND 61299"))
        conn.execute(text("DELETE FROM queue WHERE id = :q"), {"q": Q1C})
    engine.dispose()


def _seed(sync_url: str) -> None:
    _wipe(sync_url)
    now = int(time.time())
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'dash3-queue-q1c', :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": Q1C, "g": GROUP_1, "t": NOW},
        )
        states = {
            name: int(
                conn.execute(
                    text("SELECT id FROM ticket_state WHERE name = :n"), {"n": name}
                ).scalar_one()
            )
            for name in ("new", "open")
        }
        for key, (tid, queue, st, esc) in TICKETS.items():
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, :title, :q, 1, 1, 1, 1, 3, :s, NULL, NULL,"
                    " 0, 0, :esc, 0, 0, :esc, 0, :t, 1, :t, 1)"
                ),
                {
                    "id": tid,
                    "tn": f"2026101061{tid}",
                    "title": f"dash3 {key}",
                    "q": queue,
                    "s": states[st],
                    "esc": now + esc if esc is not None else 0,
                    "t": NOW,
                },
            )
    engine.dispose()


@pytest.fixture(autouse=True)
def _no_limit_pubsub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recording usage announces exhausted budgets: keep that off Redis."""

    async def _publish(_client: Any) -> None:
        return None

    monkeypatch.setattr("tiqora.events.pubsub.publish_ai_limit_changed", _publish)
    monkeypatch.setattr("tiqora.events.pubsub.get_pubsub_redis", lambda *_a, **_k: None)


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env3]:
    async with open_env3(request.getfixturevalue(request.param)) as e:
        _seed(e.sync_url)
        try:
            yield e
        finally:
            _wipe(e.sync_url)


async def data_of(env: Env3, uid: int, widget: W, **kw: Any) -> Any:
    out = await run_widget_fn(env.factory, registry(), uid, widget, **kw)
    assert out is not None and out.status == "ok", out
    assert out.data is not None
    return out.data


def _by_id(data: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {q["id"]: q for q in data["queues"]}


# --- queue overview ------------------------------------------------------------------------


async def test_queue_overview_counts_and_escalated(env: Env3) -> None:
    data = await data_of(env, A, W("q", "core.queue-overview"))
    rows = _by_id(data)
    assert rows[Q1]["open"] == 4 and rows[Q1]["new"] == 1 and rows[Q1]["escalated"] == 1
    assert rows[Q1]["name"] == "dash3-queue-61213"
    assert rows[Q1C]["open"] == 1 and rows[Q1C]["escalated"] == 0
    assert Q2 not in rows  # A has no ro on Q2
    assert [q["id"] for q in data["queues"]][0] == Q1  # busiest first
    assert data["total"] == len(data["queues"]) == 2
    assert set(data["queues"][0]) == {"id", "name", "open", "new", "escalated"}


async def test_queue_overview_respects_filter_and_only_with_work(env: Env3) -> None:
    flt = DashboardFilters(queue_ids=[Q1])
    narrowed = await data_of(env, A, W("q", "core.queue-overview"), filters=flt)
    assert [q["id"] for q in narrowed["queues"]] == [Q1] and narrowed["total"] == 1
    # a filter naming a queue the viewer cannot read yields nothing, never another user's numbers
    outside = await data_of(
        env, A, W("q", "core.queue-overview"), filters=DashboardFilters(queue_ids=[Q2])
    )
    assert outside == {"queues": [], "total": 0}
    # the busiest queue first, ``limit`` cuts the list but ``total`` still counts the rest
    limited = await data_of(env, A, W("q", "core.queue-overview", {"limit": 1}))
    assert [q["id"] for q in limited["queues"]] == [Q1] and limited["total"] == 2
    # a queue without work only shows up when ``only_with_work`` is off
    env.sql("DELETE FROM ticket WHERE id = :t", t=_tid("q1c_open"))
    with_work = await data_of(env, A, W("q", "core.queue-overview"))
    assert Q1C not in _by_id(with_work)
    everything = await data_of(env, A, W("q", "core.queue-overview", {"only_with_work": False}))
    assert _by_id(everything)[Q1C] == {
        "id": Q1C,
        "name": "dash3-queue-q1c",
        "open": 0,
        "new": 0,
        "escalated": 0,
    }


# --- team presence -------------------------------------------------------------------------


def _chat_session(env: Env3, sid: str, *, ticket: str, status: str, assigned: int | None) -> None:
    env.sql(
        "INSERT INTO tiqora_chat_session (id, config_id, topic_key, queue_id, ticket_id, status,"
        " token_hash, visitor_id, visitor_name, assigned_user_id, last_seq, waiting_since,"
        " visitor_last_seen, created) VALUES (:id, :cfg, 'support', :q, :tid, :st, 'h', 'v',"
        " 'Secret Visitor', :au, 0, :ws, :t, :t)",
        id=sid,
        cfg=CONFIG_ID,
        q=TICKETS[ticket][1],
        tid=_tid(ticket),
        st=status,
        au=assigned,
        ws=NOW + timedelta(minutes=len(sid)) if status == "waiting" else None,
        t=NOW,
    )


def _chat_config(env: Env3, *, valid: bool = True) -> None:
    engine = create_engine(env.sync_url)
    with engine.begin() as conn:
        insert_config(
            conn,
            config_id=CONFIG_ID,
            queue_id=Q1,
            public_key="dash3pk",
            origins=["https://example.org"],
            valid=valid,
        )
    engine.dispose()


async def test_team_presence_marks_agents_in_chat(env: Env3) -> None:
    _chat_config(env)
    _chat_session(env, "d3-active-b", ticket="chat_q2", status="active", assigned=B)
    # a finished chat of A does not count
    _chat_session(env, "d3-ended-a", ticket="chat_q1", status="ended", assigned=A)
    redis = FakeRedis({online_key(A): "{}", online_key(B): "{}"})
    out = await data_of(env, A, W("p", "core.team-presence"), redis=redis)
    status = {a["id"]: a["status"] for a in out["online"]}
    assert status == {A: "available", B: "in_chat"}
    assert out["total_online"] == 2
    assert out["by_status"] == {"available": 1, "in_chat": 1}
    # the limit cuts the people shown, never the counts: B (in a chat) is not shown but counted
    capped = await data_of(env, A, W("p", "core.team-presence", {"limit": 1}), redis=redis)
    assert [a["id"] for a in capped["online"]] == [A]
    assert capped["total_online"] == 2
    assert capped["by_status"] == {"available": 1, "in_chat": 1}


# --- chat queue ----------------------------------------------------------------------------


async def test_chat_queue_scoped_to_rw_queues_and_never_returns_visitor_name(env: Env3) -> None:
    _chat_config(env)
    _chat_session(env, "d3-wait-q1", ticket="chat_q1", status="waiting", assigned=None)
    _chat_session(env, "d3-wait-q2-longer-id", ticket="chat_q2", status="waiting", assigned=None)
    _chat_session(env, "d3-mine", ticket="q1_open", status="active", assigned=A)
    out = await data_of(env, A, W("c", "core.chat-queue"))
    assert out["enabled"] is True and out["waiting"] == 1 and out["mine"] == 1
    assert [i["session_id"] for i in out["items"]] == ["d3-wait-q1"]
    assert out["items"][0]["ticket_id"] == _tid("chat_q1")
    assert out["oldest_waiting_since"] == out["items"][0]["waiting_since"]
    assert out["oldest_waiting_since"].endswith("Z")
    assert "visitor_name" not in json.dumps(out) and "Secret Visitor" not in json.dumps(out)
    # B holds no rw anywhere: nothing waiting is visible to B
    other = await data_of(env, B, W("c", "core.chat-queue"))
    assert other["waiting"] == 0 and other["items"] == []
    # ``limit`` cuts the items, not the count
    _chat_session(env, "d3-wait-q1-b", ticket="q1_open", status="waiting", assigned=None)
    capped = await data_of(env, A, W("c", "core.chat-queue", {"limit": 1}))
    assert capped["waiting"] == 2 and len(capped["items"]) == 1


async def test_chat_queue_counts_bot_chats_apart_from_waiting(env: Env3) -> None:
    """M5: a chat the AI answers is not waiting for a human. It is a count of its own (``bot``,
    same ``rw`` filter as ``waiting``) and never an item."""
    _chat_config(env)
    _chat_session(env, "d3-wait-q1", ticket="chat_q1", status="waiting", assigned=None)
    _chat_session(env, "d3-bot-q1", ticket="q1_open", status="bot", assigned=None)
    _chat_session(env, "d3-bot-q1-second", ticket="chat_q1", status="bot", assigned=None)
    _chat_session(env, "d3-bot-q2", ticket="chat_q2", status="bot", assigned=None)
    out = await data_of(env, A, W("c", "core.chat-queue"))
    assert out["waiting"] == 1 and out["bot"] == 2
    assert [i["session_id"] for i in out["items"]] == ["d3-wait-q1"]
    assert "d3-bot" not in json.dumps(out)
    assert out["oldest_waiting_since"] == out["items"][0]["waiting_since"]
    # B holds no rw anywhere: no bot chat is visible to B either
    other = await data_of(env, B, W("c", "core.chat-queue"))
    assert other["bot"] == 0 and other["waiting"] == 0


async def test_chat_queue_disabled_when_no_chat_config(env: Env3) -> None:
    assert await data_of(env, A, W("c", "core.chat-queue")) == {
        "enabled": False,
        "waiting": 0,
        "oldest_waiting_since": None,
        "mine": 0,
        "bot": 0,
        "items": [],
    }
    _chat_config(env, valid=False)  # an invalid configuration counts as none
    assert (await data_of(env, A, W("c", "core.chat-queue")))["enabled"] is False
    env.sql("UPDATE tiqora_chat_config SET valid = :v WHERE id = :i", v=True, i=CONFIG_ID)
    assert (await data_of(env, A, W("c", "core.chat-queue")))["enabled"] is True


# --- AI status -----------------------------------------------------------------------------


async def _seed_ai(env: Env3) -> None:
    async with env.factory() as s:
        for q in (Q1, Q2):
            await ai_policies.create_queue_policy(s, change_by=1, queue_id=q, budget_tokens_day=100)
        provider = await ai_providers.create_provider(
            s,
            settings=get_settings(),
            change_by=1,
            name=PROVIDER_NAME,
            kind="openai_compat",
            base_url="https://llm.example.org/v1",
            api_key=None,
            price_currency="EUR",
            budget_cost_day=5.0,
        )
        for q, feature, tokens in (
            (Q1, "auto_reply", 60),
            (Q1, "triage", 50),
            (Q1, "summary", 1),
            (Q2, "auto_reply", 10),
        ):
            await ai_usage.record_usage(
                s,
                queue_id=q,
                feature=feature,
                provider_id=provider.id,
                model="dash3-model",
                prompt_tokens=tokens,
                cost_hint=0.0,
            )
        await s.commit()


def _ours(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        b
        for b in data["budgets"]
        if b["subject_name"] in ("dash3-queue-61213", "dash3-queue-61214", PROVIDER_NAME)
    ]


async def test_ai_status_budgets_scoped_and_provider_costs_admin_only(env: Env3) -> None:
    await _seed_ai(env)
    agent = _ours(await data_of(env, A, W("s", "core.ai-status")))
    assert [(b["kind"], b["subject_name"]) for b in agent] == [
        ("queue_tokens_day", "dash3-queue-61213")
    ]
    q1 = agent[0]
    assert q1["used"] == 110 and q1["limit"] == 100 and q1["exhausted"] is True
    assert q1["window"] == "day" and q1["resets_at"].endswith("Z")
    assert set(q1) == {
        "kind",
        "subject_name",
        "window",
        "used",
        "limit",
        "exhausted",
        "resets_at",
        "currency",
    }
    admin = _ours(await data_of(env, ADMIN, W("s", "core.ai-status")))
    # ADMIN holds no queue rights here, so only the provider cap shows
    assert [(b["kind"], b["subject_name"]) for b in admin] == [("provider_cost", PROVIDER_NAME)]
    assert admin[0]["currency"] == "EUR" and admin[0]["window"] == "day"
    # B reads Q2 only
    only_b = _ours(await data_of(env, B, W("s", "core.ai-status")))
    assert [b["subject_name"] for b in only_b] == ["dash3-queue-61214"]


async def test_ai_status_counts_runs_today_for_viewer_queues(env: Env3) -> None:
    await _seed_ai(env)
    a = await data_of(env, A, W("s", "core.ai-status"))
    assert a["runs_today"] == {"auto_reply": 1, "triage": 1, "summary": 1}
    b = await data_of(env, B, W("s", "core.ai-status"))
    assert b["runs_today"] == {"auto_reply": 1}
    # the dashboard's queue filter narrows the counts (a queue A cannot read adds nothing)
    narrowed = await data_of(
        env, A, W("s", "core.ai-status"), filters=DashboardFilters(queue_ids=[Q2])
    )
    assert narrowed["runs_today"] == {} and narrowed["budgets"] == []
    # rows of yesterday are not today's
    env.sql(
        "UPDATE tiqora_ai_usage SET ts = :t WHERE feature = 'summary' AND model = 'dash3-model'",
        t=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2),
    )
    again = await data_of(env, A, W("s", "core.ai-status"))
    assert again["runs_today"] == {"auto_reply": 1, "triage": 1}
    assert isinstance(again["ai_escalated"], int)
