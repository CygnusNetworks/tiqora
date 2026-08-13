"""Priority-ordered LLM provider fallback (plan block: LLM fallback).

:class:`FallbackLlmClient` implements the same :class:`~tiqora.ai.llm.LlmClient`
protocol as :class:`~tiqora.ai.llm.OpenAiCompatLlmClient` — callers (
:mod:`tiqora.ai.runtime`, :mod:`tiqora.ai.summary`) never need to know whether
a queue policy has one provider or several; they just call ``chat()``.

On any :class:`~tiqora.ai.llm.LlmError` (timeout, non-2xx HTTP, malformed
response) the next entry in the list is tried. Two pieces of cross-call state
make repeated failures cheap:

* **Stickiness** — a :class:`FallbackLlmClient` instance remembers the index
  of the last entry that succeeded and starts there on the next ``chat()``
  call (an agent run makes several sequential calls; no point re-trying a
  provider that just failed).
* **Cooldown** — a module-global ``{provider_id: fail_monotonic}`` cache
  (shared across instances/requests within one process) makes newly-created
  clients skip a provider that failed recently, instead of paying its
  timeout again on every single run. A provider is only ever skipped this
  way if at least one *other* entry is not in cooldown — if all entries are
  in cooldown, they are tried in order anyway (a fully-down list must still
  attempt something).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

from tiqora.ai.llm import LlmClient, LlmError, LlmMessage, LlmResponse

logger = structlog.get_logger(__name__)

# Module-global: {provider_id: monotonic time of last failure}. Shared across
# all FallbackLlmClient instances in this process (see module docstring).
_cooldown_fail_time: dict[int, float] = {}

_DEFAULT_COOLDOWN_SECONDS = 300.0


def reset_cooldowns() -> None:
    """Test helper: clear the module-global cooldown cache between tests."""
    _cooldown_fail_time.clear()


@dataclass(frozen=True, slots=True)
class FallbackEntry:
    provider_id: int
    model: str
    factory: Callable[[], LlmClient]


class FallbackLlmClient:
    """Tries ``entries`` in priority order (starting from the sticky index),
    skipping providers currently in cooldown. Implements the
    :class:`~tiqora.ai.llm.LlmClient` protocol."""

    def __init__(
        self,
        entries: list[FallbackEntry],
        *,
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not entries:
            raise ValueError("FallbackLlmClient requires at least one entry")
        self._entries = entries
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._sticky_index = 0
        # Public: caller reads these after chat() to learn which provider
        # actually served the response (usage/audit accounting).
        self.active_provider_id: int | None = None
        self.active_model: str | None = None

    def _in_cooldown(self, provider_id: int, *, now: float) -> bool:
        fail_time = _cooldown_fail_time.get(provider_id)
        return fail_time is not None and (now - fail_time) < self._cooldown_seconds

    def _attempt_order(self) -> list[int]:
        """Indices starting at the sticky index, wrapped around, with
        in-cooldown entries filtered out — unless that would leave nothing
        (all entries in cooldown), in which case the full order is tried."""
        n = len(self._entries)
        rotated = [(self._sticky_index + i) % n for i in range(n)]
        now = self._clock()
        available = [
            i for i in rotated if not self._in_cooldown(self._entries[i].provider_id, now=now)
        ]
        return available or rotated

    async def chat(
        self,
        *,
        messages: list[LlmMessage],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LlmResponse:
        last_error: LlmError | None = None
        for idx in self._attempt_order():
            entry = self._entries[idx]
            client = entry.factory()
            try:
                response = await client.chat(
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except LlmError as exc:
                last_error = exc
                _cooldown_fail_time[entry.provider_id] = self._clock()
                logger.warning(
                    "ai_llm_fallback_provider_failed",
                    provider_id=entry.provider_id,
                    error_class=type(exc).__name__,
                )
                continue
            _cooldown_fail_time.pop(entry.provider_id, None)
            self._sticky_index = idx
            self.active_provider_id = entry.provider_id
            self.active_model = entry.model
            return response
        assert last_error is not None  # entries is non-empty (checked in __init__)
        raise last_error


__all__ = [
    "FallbackEntry",
    "FallbackLlmClient",
    "reset_cooldowns",
]
