"""Scripted LLM client and canned tool-call responses shared by the AI test modules.

Public names; ``test_ai_runtime.py`` keeps its own private copies of the same helpers.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from tiqora.ai.llm import LlmMessage, LlmResponse, LlmUsage, ToolCall


class ScriptedLlm:
    """Returns one scripted :class:`LlmResponse` per call, in order."""

    def __init__(
        self,
        responses: list[LlmResponse],
        *,
        on_call: Callable[[], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._responses = list(responses)
        self._on_call = on_call
        self.calls = 0
        self.last_messages: list[LlmMessage] = []
        self.max_tokens_seen: list[int] = []
        self.tools_seen: list[list[dict[str, Any]] | None] = []

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
        self.last_messages = messages
        self.max_tokens_seen.append(max_tokens)
        self.tools_seen.append(tools)
        if self._on_call is not None:
            await self._on_call()
        return self._responses.pop(0)

    @property
    def last_user_message(self) -> str | None:
        return next(
            (m.content for m in reversed(self.last_messages) if m.role == "user"),
            None,  # type: ignore[misc]
        )


def propose_response(
    kind: str, body: str, subject: str = "Re: Help", *, model: str | None = None
) -> LlmResponse:
    return LlmResponse(
        content=None,
        tool_calls=[
            ToolCall(
                id="call_1",
                name="propose_customer_message",
                arguments={"kind": kind, "subject": subject, "body": body},
            )
        ],
        usage=LlmUsage(prompt_tokens=10, completion_tokens=5),
        model=model,
    )


def escalate_response(reason: str) -> LlmResponse:
    return LlmResponse(
        content=None,
        tool_calls=[ToolCall(id="call_1", name="escalate_to_human", arguments={"reason": reason})],
        usage=LlmUsage(prompt_tokens=8, completion_tokens=4),
    )


def no_reply_response(reason: str) -> LlmResponse:
    return LlmResponse(
        content=None,
        tool_calls=[
            ToolCall(id="call_noreply", name="no_reply_needed", arguments={"reason": reason})
        ],
        usage=LlmUsage(prompt_tokens=9, completion_tokens=4),
    )
