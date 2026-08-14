"""Unit tests for tiqora.ai.llm_fallback (FallbackLlmClient) and its wiring
into tiqora.ai.kb_wiring.build_llm_client / tiqora.ai.policies validation.

No real DB/network: providers are resolved through a minimal fake session
(``.get(Model, id) -> obj|None``) — build_llm_client and the policy
validators only ever call ``session.get(TiqoraLlmProvider, id)``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from tiqora.ai import kb_wiring, policies
from tiqora.ai.llm import LlmError, LlmHttpError, LlmMessage, LlmResponse, LlmTimeoutError, LlmUsage
from tiqora.ai.llm_fallback import FallbackEntry, FallbackLlmClient, reset_cooldowns
from tiqora.ai.policies import QueuePolicyValidationError

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


def _entry(provider_id: int, model: str, client: _FakeClient) -> FallbackEntry:
    return FallbackEntry(provider_id=provider_id, model=model, factory=lambda: client)


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


# ---------------------------------------------------------------------------
# build_llm_client wiring
# ---------------------------------------------------------------------------


def _provider(id_: int, *, model: str = "gpt-x") -> SimpleNamespace:
    return SimpleNamespace(
        id=id_, base_url="https://example.invalid", api_key_enc=None, default_model=model
    )


class _FakeSession:
    def __init__(self, providers: dict[int, Any]) -> None:
        self._providers = providers

    async def get(self, _model: Any, id_: int) -> Any:
        return self._providers.get(id_)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(secret_key="test-secret", llm_timeout_seconds=30.0)


async def test_build_llm_client_single_provider_returns_plain_client() -> None:
    session = _FakeSession({1: _provider(1)})
    client = await kb_wiring.build_llm_client(session, _settings(), 1, None, None)
    from tiqora.ai.llm import OpenAiCompatLlmClient

    assert isinstance(client, OpenAiCompatLlmClient)


async def test_build_llm_client_with_fallback_returns_fallback_client() -> None:
    session = _FakeSession({1: _provider(1), 2: _provider(2)})
    fallback_json = json.dumps([{"provider_id": 2, "model": None}])
    client = await kb_wiring.build_llm_client(session, _settings(), 1, None, fallback_json)

    assert isinstance(client, FallbackLlmClient)
    assert [e.provider_id for e in client._entries] == [1, 2]  # noqa: SLF001


async def test_build_llm_client_skips_missing_fallback_provider() -> None:
    session = _FakeSession({1: _provider(1)})  # provider 2 does not exist
    fallback_json = json.dumps([{"provider_id": 2, "model": None}])
    client = await kb_wiring.build_llm_client(session, _settings(), 1, None, fallback_json)

    from tiqora.ai.llm import OpenAiCompatLlmClient

    assert isinstance(client, OpenAiCompatLlmClient)


# ---------------------------------------------------------------------------
# Policy validation
# ---------------------------------------------------------------------------


async def test_validate_llm_fallback_json_rejects_invalid_json() -> None:
    session = _FakeSession({})
    with pytest.raises(QueuePolicyValidationError):
        await policies._validate_llm_fallback_json(session, "{not json")  # noqa: SLF001


async def test_validate_llm_fallback_json_rejects_unknown_provider() -> None:
    session = _FakeSession({1: _provider(1)})
    raw = json.dumps([{"provider_id": 999, "model": None}])
    with pytest.raises(QueuePolicyValidationError):
        await policies._validate_llm_fallback_json(session, raw)  # noqa: SLF001


async def test_validate_llm_fallback_json_accepts_valid_entries() -> None:
    session = _FakeSession({1: _provider(1), 2: _provider(2)})
    raw = json.dumps([{"provider_id": 2, "model": "gpt-y"}])
    await policies._validate_llm_fallback_json(session, raw)  # noqa: SLF001 — must not raise
