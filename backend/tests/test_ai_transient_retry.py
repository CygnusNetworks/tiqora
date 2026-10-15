"""Transient provider errors are retried before an AI run gives up."""

from __future__ import annotations

from typing import Any

import pytest

from tiqora.ai import runtime
from tiqora.ai.llm import LlmHttpError, LlmResponse, LlmTimeoutError, LlmUsage


class _FlakyLlm:
    def __init__(self, outcomes: list[Exception | LlmResponse]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    async def chat(self, **_: Any) -> LlmResponse:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


_OK = LlmResponse(content="ok", tool_calls=[], usage=LlmUsage(1, 1), finish_reason="stop")
_MODEL_GONE = LlmHttpError(404, '{"detail":"The model `X` does not exist."}')


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant(_: float) -> None:
        return None

    monkeypatch.setattr(runtime.asyncio, "sleep", _instant)


@pytest.mark.parametrize(
    "error", [_MODEL_GONE, LlmHttpError(503, "down"), LlmHttpError(429, "slow"), LlmTimeoutError()]
)
async def test_transient_error_is_retried(error: Exception) -> None:
    llm = _FlakyLlm([error, _OK])
    response = await runtime._chat_transient_retry(llm, messages=[])  # type: ignore[arg-type]
    assert response is _OK
    assert llm.calls == 2


async def test_permanent_error_is_not_retried() -> None:
    llm = _FlakyLlm([LlmHttpError(401, "bad key")])
    with pytest.raises(LlmHttpError):
        await runtime._chat_transient_retry(llm, messages=[])  # type: ignore[arg-type]
    assert llm.calls == 1


async def test_gives_up_after_the_last_retry() -> None:
    llm = _FlakyLlm([_MODEL_GONE] * 3)
    with pytest.raises(LlmHttpError):
        await runtime._chat_transient_retry(llm, messages=[])  # type: ignore[arg-type]
    assert llm.calls == 3
