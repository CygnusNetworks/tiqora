"""Unit tests for tiqora.ai.auto_run: the cap check and the run preparation both callers share.

No DB is touched: the counters, the customer-pause lookup and the LLM/KB wiring are patched at
the ``auto_run`` module (where they are imported), so the order of the checks is what is tested.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests._ai_scripted_llm import ScriptedLlm, escalate_response, propose_response
from tiqora.ai import auto_run
from tiqora.ai.auto_run import (
    QUEUE_CAP_REASONS,
    TICKET_CAP_REASONS,
    NoAgentProfile,
    auto_reply_cap_reason,
    auto_reply_queue_cap_reason,
    prepare_auto_run,
)
from tiqora.ai.llm_routing import NoUsableModel

pytestmark = pytest.mark.asyncio


def _policy(**over: Any) -> Any:
    base: dict[str, Any] = {
        "max_auto_replies": 3,
        "max_clarifications": 2,
        "max_replies_per_hour": None,
        "budget_tokens_day": None,
        "service_user_id": 7,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _state(**over: Any) -> Any:
    base: dict[str, Any] = {
        "ai_escalated_at": None,
        "ai_paused_at": None,
        "ai_grant_remaining": None,
        "auto_reply_count": 0,
        "clarification_count": 0,
    }
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def counters(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch the counters behind the queue limits and the customer-pause lookup."""
    values: dict[str, Any] = {"hour_queue": 0, "hour_global": 0, "global_cap": None, "tokens": 0}

    async def hour(_s: Any, *, queue_id: int | None) -> int:
        return int(values["hour_queue"] if queue_id is not None else values["hour_global"])

    async def global_cap(_s: Any) -> int | None:
        return values["global_cap"]  # type: ignore[no-any-return]

    async def tokens(_s: Any, _queue_id: int) -> int:
        return int(values["tokens"])

    async def pause(_s: Any, _ticket_id: int) -> Any:
        return values.get("customer_pause")

    monkeypatch.setattr(auto_run, "auto_replies_last_hour", hour)
    monkeypatch.setattr(auto_run, "global_replies_per_hour_cap", global_cap)
    monkeypatch.setattr(auto_run, "tokens_used_today", tokens)
    monkeypatch.setattr(auto_run, "active_pause_for_ticket", pause)
    return values


async def _reason(policy: Any, state: Any) -> str | None:
    return await auto_reply_cap_reason(
        None,  # type: ignore[arg-type]
        ticket_id=1,
        queue_id=5,
        policy=policy,
        state=state,
    )


async def test_cap_reason_order_unchanged(counters: dict[str, Any]) -> None:
    assert await _reason(_policy(), _state()) is None

    # Everything blocks at once: the first check in order wins, then each is peeled off.
    counters.update(
        customer_pause=object(), hour_queue=9, hour_global=9, global_cap=4, tokens=10**6
    )
    policy = _policy(max_replies_per_hour=2, budget_tokens_day=100)
    state = _state(ai_escalated_at=1, ai_paused_at=1, ai_grant_remaining=0, auto_reply_count=3)
    assert await _reason(policy, state) == "escalated_to_human"
    state.ai_escalated_at = None
    assert await _reason(policy, state) == "ai_paused"
    state.ai_paused_at = None
    assert await _reason(policy, state) == auto_run.CUSTOMER_PAUSE_SKIP_REASON
    counters["customer_pause"] = None
    assert await _reason(policy, state) == "grant_used"
    state.ai_grant_remaining = None
    assert await _reason(policy, state) == "max_auto_replies"
    state.auto_reply_count = 0
    state.clarification_count = 2
    assert await _reason(policy, state) == "max_clarifications"
    state.clarification_count = 0
    assert await _reason(policy, state) == "queue_rate_limit"
    counters["hour_queue"] = 0
    assert await _reason(policy, state) == "global_rate_limit"
    counters["hour_global"] = 0
    assert await _reason(policy, state) == "budget_tokens_day"
    counters["tokens"] = 0
    assert await _reason(policy, state) is None


async def test_open_grant_replaces_per_ticket_caps_but_not_rate_limits(
    counters: dict[str, Any],
) -> None:
    state = _state(ai_grant_remaining=2, auto_reply_count=99, clarification_count=99)
    assert await _reason(_policy(), state) is None
    counters["hour_queue"] = 5
    assert await _reason(_policy(max_replies_per_hour=5), state) == "queue_rate_limit"


async def test_queue_cap_reason_ignores_ticket_caps(counters: dict[str, Any]) -> None:
    policy = _policy(max_auto_replies=0, max_clarifications=0)
    # A ticket this exhausted would stop on a per-ticket cap; the queue check does not see it.
    assert await _reason(policy, _state()) == "max_auto_replies"
    assert await auto_reply_queue_cap_reason(None, queue_id=5, policy=policy) is None  # type: ignore[arg-type]
    counters.update(tokens=500)
    reason = await auto_reply_queue_cap_reason(
        None,  # type: ignore[arg-type]
        queue_id=5,
        policy=_policy(budget_tokens_day=500),
    )
    assert reason == "budget_tokens_day"
    assert reason in QUEUE_CAP_REASONS
    assert not (QUEUE_CAP_REASONS & TICKET_CAP_REASONS)


async def test_prepare_auto_run_builds_agent_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = ScriptedLlm([propose_response("answer", "Hallo"), escalate_response("x")])
    task_llm = SimpleNamespace(client=llm, models=["m1", "m2"])
    seen: dict[str, Any] = {}

    async def fake_build(session: Any, settings: Any, policy: Any, task: str) -> Any:
        seen["build"] = (session, settings, policy, task)
        return task_llm

    async def fake_bundle(session: Any, settings: Any, user_id: Any, policy: Any) -> str:
        seen["bundle"] = (session, user_id, policy)
        return "KB-BUNDLE"

    def fake_search(session: Any, settings: Any, user_id: Any) -> str:
        return f"search:{user_id}"

    def fake_get(session: Any, settings: Any, user_id: Any) -> str:
        return f"get:{user_id}"

    monkeypatch.setattr(auto_run, "build_task_llm", fake_build)
    monkeypatch.setattr(auto_run, "kb_bundle", fake_bundle)
    monkeypatch.setattr(auto_run, "kb_search_fn", fake_search)
    monkeypatch.setattr(auto_run, "kb_get_article_fn", fake_get)
    session, settings, policy = object(), object(), _policy()

    prepared = await prepare_auto_run(session, settings, policy)  # type: ignore[arg-type]

    assert seen["build"] == (session, settings, policy, "agent")
    assert seen["bundle"][0] is session  # the KB is wired to the session the run will use
    assert prepared.as_kwargs() == {
        "llm": llm,
        "llm_models": ["m1", "m2"],
        "kb_bundle": "KB-BUNDLE",
        "kb_search_fn": "search:7",
        "kb_get_article_fn": "get:7",
    }


async def test_prepare_auto_run_without_profile_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def none(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(auto_run, "build_task_llm", none)
    with pytest.raises(NoAgentProfile):
        await prepare_auto_run(object(), object(), _policy())  # type: ignore[arg-type]


async def test_prepare_auto_run_lets_no_usable_model_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(*_a: Any, **_k: Any) -> None:
        raise NoUsableModel("agent-profile", ["budget"])

    monkeypatch.setattr(auto_run, "build_task_llm", boom)
    with pytest.raises(NoUsableModel):
        await prepare_auto_run(object(), object(), _policy())  # type: ignore[arg-type]
