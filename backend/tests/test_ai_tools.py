"""Unit tests for tiqora.ai.tools (plan §3.3/§3.4/§3.8). No DB, no network:
the executor is exercised only for the "reject unknown/blocked tool" path,
which never touches the session."""

from __future__ import annotations

import pytest

from tiqora.ai.models import AUTONOMY_CLARIFY_ONLY, AUTONOMY_FULL, AUTONOMY_OFF
from tiqora.ai.pii import PiiMapper
from tiqora.ai.tools import (
    TOOL_ADD_INTERNAL_NOTE,
    TOOL_ESCALATE_TO_HUMAN,
    TOOL_PROPOSE_CUSTOMER_MESSAGE,
    McpToolSpec,
    ToolExecutor,
    ToolRegistry,
    UnknownToolError,
    resolve_allowed_state_types,
)


def _mcp_spec(*, mutating: bool) -> McpToolSpec:
    return McpToolSpec(
        client_name="netadmin",
        client_url="https://mcp.example/netadmin",
        auth_token=None,
        tool_name="diagnose",
        mutating=mutating,
    )


def test_local_tools_always_present() -> None:
    registry = ToolRegistry(autonomy=AUTONOMY_OFF)
    names = {s["function"]["name"] for s in registry.build_schemas()}
    assert TOOL_PROPOSE_CUSTOMER_MESSAGE in names
    assert TOOL_ADD_INTERNAL_NOTE in names
    assert TOOL_ESCALATE_TO_HUMAN in names


def test_readonly_mcp_tool_available_in_every_autonomy_mode() -> None:
    spec = _mcp_spec(mutating=False)
    for autonomy in (AUTONOMY_OFF, AUTONOMY_CLARIFY_ONLY, AUTONOMY_FULL):
        registry = ToolRegistry(autonomy=autonomy, mcp_tools=[spec])
        names = {s["function"]["name"] for s in registry.build_schemas()}
        assert spec.full_name in names
        assert registry.is_known(spec.full_name)


def test_mutating_mcp_tool_only_available_at_full_autonomy() -> None:
    spec = _mcp_spec(mutating=True)
    for autonomy in (AUTONOMY_OFF, AUTONOMY_CLARIFY_ONLY):
        registry = ToolRegistry(autonomy=autonomy, mcp_tools=[spec])
        names = {s["function"]["name"] for s in registry.build_schemas()}
        assert spec.full_name not in names
        assert not registry.is_known(spec.full_name)

    registry = ToolRegistry(autonomy=AUTONOMY_FULL, mcp_tools=[spec])
    names = {s["function"]["name"] for s in registry.build_schemas()}
    assert spec.full_name in names
    assert registry.is_known(spec.full_name)


def test_kb_tools_hidden_when_kb_disabled() -> None:
    registry = ToolRegistry(autonomy=AUTONOMY_OFF, kb_enabled=False)
    names = {s["function"]["name"] for s in registry.build_schemas()}
    assert "kb_search" not in names
    assert "kb_get_article" not in names


@pytest.mark.asyncio
async def test_executor_rejects_unknown_tool_name() -> None:
    registry = ToolRegistry(autonomy=AUTONOMY_OFF)
    executor = ToolExecutor(
        session=None,  # type: ignore[arg-type]
        sysconfig=None,  # type: ignore[arg-type]
        registry=registry,
        ticket_id=1,
        acting_user_id=1,
        pii=PiiMapper(),
        escalation_rules=None,
    )
    with pytest.raises(UnknownToolError):
        await executor.execute("delete_everything", {})


@pytest.mark.asyncio
async def test_executor_rejects_mutating_mcp_tool_when_not_full_autonomy() -> None:
    spec = _mcp_spec(mutating=True)
    registry = ToolRegistry(autonomy=AUTONOMY_CLARIFY_ONLY, mcp_tools=[spec])
    executor = ToolExecutor(
        session=None,  # type: ignore[arg-type]
        sysconfig=None,  # type: ignore[arg-type]
        registry=registry,
        ticket_id=1,
        acting_user_id=1,
        pii=PiiMapper(),
        escalation_rules=None,
    )
    with pytest.raises(UnknownToolError):
        await executor.execute(spec.full_name, {})


# ---------------------------------------------------------------------------
# resolve_allowed_state_types (plan block 5)
# ---------------------------------------------------------------------------


def test_resolve_allowed_state_types_defaults_to_open_when_unset() -> None:
    assert resolve_allowed_state_types(None) == ["open"]
    assert resolve_allowed_state_types("") == ["open"]
    assert resolve_allowed_state_types("   ") == ["open"]


def test_resolve_allowed_state_types_explicit_empty_list_disables_all() -> None:
    assert resolve_allowed_state_types("[]") == []


def test_resolve_allowed_state_types_parses_json_array() -> None:
    assert resolve_allowed_state_types('["open", "pending reminder"]') == [
        "open",
        "pending reminder",
    ]


def test_resolve_allowed_state_types_parses_csv() -> None:
    assert resolve_allowed_state_types("open, closed") == ["open", "closed"]


class _FakeTextContent:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeCallToolResult:
    """Shape-compatible stand-in for fastmcp's CallToolResult."""

    def __init__(
        self,
        *,
        content: list[_FakeTextContent] | None = None,
        structured_content: object = None,
        data: object = None,
    ) -> None:
        self.content = content or []
        self.structured_content = structured_content
        self.data = data


def test_mcp_result_payload_prefers_structured_content() -> None:
    from tiqora.ai.tools import _mcp_result_payload

    raw = _FakeCallToolResult(
        content=[_FakeTextContent('{"status": "ok"}')],
        structured_content={"status": "disruption", "eta": "20:00"},
    )
    assert _mcp_result_payload(raw) == {"status": "disruption", "eta": "20:00"}


def test_mcp_result_payload_parses_json_text_parts() -> None:
    from tiqora.ai.tools import _mcp_result_payload

    raw = _FakeCallToolResult(content=[_FakeTextContent('{"status": "ok", "n": 3}')])
    assert _mcp_result_payload(raw) == {"status": "ok", "n": 3}


def test_mcp_result_payload_joins_plain_text_parts() -> None:
    from tiqora.ai.tools import _mcp_result_payload

    raw = _FakeCallToolResult(content=[_FakeTextContent("Zeile 1"), _FakeTextContent("Zeile 2")])
    assert _mcp_result_payload(raw) == "Zeile 1\nZeile 2"


def test_mcp_result_payload_passes_plain_data_through() -> None:
    from tiqora.ai.tools import _mcp_result_payload

    assert _mcp_result_payload({"a": 1}) == {"a": 1}
    assert _mcp_result_payload([1, 2]) == [1, 2]
    assert _mcp_result_payload("text") == "text"
    assert _mcp_result_payload(None) is None
