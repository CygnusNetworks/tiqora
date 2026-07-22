"""OpenAI-compatible LLM client (plan §3.2).

No ``openai`` SDK dependency — ``httpx`` is already a dependency of this
repo and the OpenAI-compatible ``/chat/completions`` wire format is simple
enough to hand-roll (same approach as ``tiqora.ai.providers.test_provider_connection``).

:class:`LlmClient` is a ``Protocol`` so :class:`~tiqora.ai.runtime.AgentRuntime`
and its tests can inject a scripted ``FakeLlmClient`` instead of
:class:`OpenAiCompatLlmClient` — no test ever calls a real endpoint.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

_DEFAULT_TIMEOUT_SECONDS = 60.0


class LlmError(Exception):
    """Base class for all LLM client errors."""


class LlmTimeoutError(LlmError):
    """The request timed out."""


class LlmHttpError(LlmError):
    """The provider returned a non-2xx HTTP status."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"HTTP {status_code}: {body[:500]}")
        self.status_code = status_code
        self.body = body


class LlmSchemaError(LlmError):
    """The provider's response did not have the expected shape."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LlmMessage:
    """One message in the wire-format chat history.

    ``role`` is one of ``system`` | ``user`` | ``assistant`` | ``tool``.
    ``tool_calls`` is only set on assistant messages that invoked tools;
    ``tool_call_id`` (+ optionally ``name``) is only set on ``tool`` role
    messages (the tool result being fed back to the model).
    """

    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_wire(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            msg["content"] = self.content
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id is not None:
            msg["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            msg["name"] = self.name
        return msg


@dataclass(frozen=True, slots=True)
class LlmUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True, slots=True)
class LlmResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: LlmUsage = field(default_factory=LlmUsage)
    finish_reason: str | None = None
    model: str | None = None


class LlmClient(Protocol):
    """Injectable chat-completion interface (production: OpenAI-compatible;
    tests: a scripted fake)."""

    async def chat(
        self,
        *,
        messages: list[LlmMessage],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LlmResponse: ...


def _parse_tool_calls(raw_tool_calls: list[dict[str, Any]] | None) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for raw in raw_tool_calls or []:
        fn = raw.get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        raw_args = fn.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except (json.JSONDecodeError, TypeError):
            arguments = {"_unparsable_arguments": raw_args}
        call_id = raw.get("id") or f"call_{len(calls)}"
        calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
    return calls


class OpenAiCompatLlmClient:
    """Calls ``POST {base_url}/chat/completions`` (OpenAI wire format)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._client = http_client
        self._owns_client = http_client is None
        self._timeout_seconds = timeout_seconds

    async def chat(
        self,
        *,
        messages: list[LlmMessage],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LlmResponse:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [m.to_wire() for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice

        client = self._client or httpx.AsyncClient(timeout=self._timeout_seconds)
        try:
            try:
                response = await client.post(
                    f"{self._base_url}/chat/completions", headers=headers, json=payload
                )
            except httpx.TimeoutException as exc:
                raise LlmTimeoutError(str(exc)) from exc
            except httpx.HTTPError as exc:
                raise LlmError(str(exc)) from exc

            if response.status_code >= 400:
                raise LlmHttpError(response.status_code, response.text)

            try:
                data = response.json()
            except ValueError as exc:
                raise LlmSchemaError(f"Response was not valid JSON: {exc}") from exc

            choices = data.get("choices")
            if not choices:
                raise LlmSchemaError("Response had no 'choices'")
            choice = choices[0]
            message = choice.get("message") or {}
            usage_raw = data.get("usage") or {}
            return LlmResponse(
                content=message.get("content"),
                tool_calls=_parse_tool_calls(message.get("tool_calls")),
                usage=LlmUsage(
                    prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
                    completion_tokens=int(usage_raw.get("completion_tokens") or 0),
                ),
                finish_reason=choice.get("finish_reason"),
                model=data.get("model"),
            )
        finally:
            if self._owns_client:
                await client.aclose()


__all__ = [
    "LlmClient",
    "LlmError",
    "LlmHttpError",
    "LlmMessage",
    "LlmResponse",
    "LlmSchemaError",
    "LlmTimeoutError",
    "LlmUsage",
    "OpenAiCompatLlmClient",
    "ToolCall",
]
