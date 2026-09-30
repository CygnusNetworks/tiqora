"""Unit tests for tiqora.ai.llm_fallback (FallbackLlmClient). The chain
building on top of it lives in tiqora.ai.llm_routing (test_llm_routing.py).
"""

from __future__ import annotations

from typing import Any

import pytest

from tiqora.ai.llm import LlmError, LlmHttpError, LlmMessage, LlmResponse, LlmTimeoutError, LlmUsage
from tiqora.ai.llm_fallback import FallbackEntry, FallbackLlmClient, reset_cooldowns

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_cooldowns() -> None:
    reset_cooldowns()
    yield
    reset_cooldowns()


# ---------------------------------------------------------------------------
# FallbackLlmClient
# ---------------------------------------------------------------------------


class _FakeClient:
    """Scripted LlmClient: pops one entry per chat() call. Entries are either
    an exception instance (raised) or a return value."""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls = 0

    async def chat(self, **_kwargs: Any) -> LlmResponse:
        self.calls += 1
        if not self._script:
            raise AssertionError("no more scripted responses")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _response(content: str) -> LlmResponse:
    return LlmResponse(content=content, usage=LlmUsage(prompt_tokens=1, completion_tokens=1))


def _entry(
    provider_id: int, model: str, client: _FakeClient, *, llm_model_id: int | None = None
) -> FallbackEntry:
    return FallbackEntry(
        llm_model_id=provider_id if llm_model_id is None else llm_model_id,
        provider_id=provider_id,
        model=model,
        factory=lambda: client,
    )


async def test_timeout_fails_over_to_second_entry() -> None:
    first = _FakeClient([LlmTimeoutError("slow")])
    second = _FakeClient([_response("ok")])
    fb = FallbackLlmClient([_entry(1, "m1", first), _entry(2, "m2", second)])

    response = await fb.chat(messages=[LlmMessage(role="user", content="hi")])

    assert response.content == "ok"
    assert fb.active_provider_id == 2
    assert fb.active_model == "m2"
    assert first.calls == 1
    assert second.calls == 1


async def test_http_500_fails_over_and_all_failing_raises_last_error() -> None:
    first = _FakeClient([LlmHttpError(500, "boom")])
    second_error = LlmHttpError(503, "still down")
    second = _FakeClient([second_error])
    fb = FallbackLlmClient([_entry(1, "m1", first), _entry(2, "m2", second)])

    with pytest.raises(LlmHttpError) as excinfo:
        await fb.chat(messages=[LlmMessage(role="user", content="hi")])

    assert excinfo.value is second_error
    assert fb.active_provider_id is None


async def test_stickiness_second_call_starts_at_working_entry() -> None:
    first = _FakeClient([LlmTimeoutError("slow")])
    second = _FakeClient([_response("ok-1"), _response("ok-2")])
    fb = FallbackLlmClient([_entry(1, "m1", first), _entry(2, "m2", second)])

    r1 = await fb.chat(messages=[LlmMessage(role="user", content="hi")])
    r2 = await fb.chat(messages=[LlmMessage(role="user", content="hi again")])

    assert r1.content == "ok-1"
    assert r2.content == "ok-2"
    assert first.calls == 1  # never retried once entry 2 became sticky
    assert second.calls == 2
    assert fb.active_provider_id == 2


async def test_cooldown_skips_recently_failed_provider_new_instance() -> None:
    clock = {"t": 0.0}

    def fake_clock() -> float:
        return clock["t"]

    first = _FakeClient([LlmTimeoutError("slow")])
    second = _FakeClient([_response("from-2")])
    fb1 = FallbackLlmClient(
        [_entry(1, "m1", first), _entry(2, "m2", second)],
        cooldown_seconds=300.0,
        clock=fake_clock,
    )
    r1 = await fb1.chat(messages=[LlmMessage(role="user", content="hi")])
    assert r1.content == "from-2"
    assert first.calls == 1

    # A brand-new instance/run: provider 1 is still in cooldown, so it must
    # not be attempted at all.
    first_again = _FakeClient([])  # would raise AssertionError if called
    second_again = _FakeClient([_response("from-2-again")])
    clock["t"] = 10.0  # well within the 300s cooldown
    fb2 = FallbackLlmClient(
        [_entry(1, "m1", first_again), _entry(2, "m2", second_again)],
        cooldown_seconds=300.0,
        clock=fake_clock,
    )
    r2 = await fb2.chat(messages=[LlmMessage(role="user", content="hi")])
    assert r2.content == "from-2-again"
    assert first_again.calls == 0

    # Advance the clock past the cooldown: a fresh instance goes back to
    # priority 1.
    clock["t"] = 400.0
    first_recovered = _FakeClient([_response("from-1-recovered")])
    second_unused = _FakeClient([])
    fb3 = FallbackLlmClient(
        [_entry(1, "m1", first_recovered), _entry(2, "m2", second_unused)],
        cooldown_seconds=300.0,
        clock=fake_clock,
    )
    r3 = await fb3.chat(messages=[LlmMessage(role="user", content="hi")])
    assert r3.content == "from-1-recovered"
    assert fb3.active_provider_id == 1
    assert second_unused.calls == 0


async def test_all_entries_in_cooldown_still_attempted_in_order() -> None:
    clock = {"t": 0.0}

    def fake_clock() -> float:
        return clock["t"]

    first = _FakeClient([LlmTimeoutError("slow")])
    second = _FakeClient([LlmTimeoutError("slow-too")])
    fb1 = FallbackLlmClient(
        [_entry(1, "m1", first), _entry(2, "m2", second)], cooldown_seconds=300.0, clock=fake_clock
    )
    with pytest.raises(LlmError):
        await fb1.chat(messages=[LlmMessage(role="user", content="hi")])

    # Both providers are now in cooldown. A new instance must still try them
    # (in priority order) rather than raising immediately.
    first_retry = _FakeClient([_response("ok-despite-cooldown")])
    second_retry = _FakeClient([])
    fb2 = FallbackLlmClient(
        [_entry(1, "m1", first_retry), _entry(2, "m2", second_retry)],
        cooldown_seconds=300.0,
        clock=fake_clock,
    )
    response = await fb2.chat(messages=[LlmMessage(role="user", content="hi")])
    assert response.content == "ok-despite-cooldown"
    assert first_retry.calls == 1


async def test_cooldown_is_per_model_not_per_provider() -> None:
    """One model of a provider failing must not cool down its other models."""
    clock = {"t": 0.0}

    def fake_clock() -> float:
        return clock["t"]

    failing = _FakeClient([LlmTimeoutError("slow")])
    sibling = _FakeClient([_response("sibling")])
    fb1 = FallbackLlmClient(
        [_entry(1, "big", failing, llm_model_id=10), _entry(1, "small", sibling, llm_model_id=11)],
        cooldown_seconds=300.0,
        clock=fake_clock,
    )
    assert (await fb1.chat(messages=[LlmMessage(role="user", content="hi")])).content == "sibling"
    assert fb1.active_llm_model_id == 11

    # New run: the sibling (same provider, other model) is tried first, the
    # other provider's model is not needed.
    clock["t"] = 10.0
    sibling_again = _FakeClient([_response("sibling-again")])
    other = _FakeClient([])
    fb2 = FallbackLlmClient(
        [
            _entry(1, "small", sibling_again, llm_model_id=11),
            _entry(2, "elsewhere", other, llm_model_id=20),
        ],
        cooldown_seconds=300.0,
        clock=fake_clock,
    )
    response = await fb2.chat(messages=[LlmMessage(role="user", content="hi")])
    assert response.content == "sibling-again"
    assert other.calls == 0

    # The failed model itself is still in cooldown.
    failing_again = _FakeClient([])
    third = _FakeClient([_response("third")])
    fb3 = FallbackLlmClient(
        [_entry(1, "big", failing_again, llm_model_id=10), _entry(2, "x", third, llm_model_id=20)],
        cooldown_seconds=300.0,
        clock=fake_clock,
    )
    assert (await fb3.chat(messages=[LlmMessage(role="user", content="hi")])).content == "third"
    assert failing_again.calls == 0
