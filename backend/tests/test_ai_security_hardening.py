"""Unit tests for AI security hardening (plan A/B/C + #10).

No DB, no network — pure module tests for capabilities, prompt safety,
output guards, tool-chain alerts, MCP schema validation, and capability
gating of local tools.
"""

from __future__ import annotations

import pytest

from tiqora.ai.capabilities import (
    AgentCapabilities,
    capabilities_for_autonomy,
    resolve_capabilities,
)
from tiqora.ai.models import AUTONOMY_CLARIFY_ONLY, AUTONOMY_FULL, AUTONOMY_OFF
from tiqora.ai.output_guards import (
    MAX_CUSTOMER_BODY_CHARS,
    MAX_CUSTOMER_BODY_LINKS,
    CustomerMessageGuardError,
    strip_hallucinated_signoff,
    validate_customer_message,
)
from tiqora.ai.pii import PiiMapper
from tiqora.ai.prompt_safety import (
    UNTRUSTED_CONTENT_SYSTEM_BLOCK,
    UNTRUSTED_TOOL_RESULT_PREFIX,
    with_untrusted_tool_prefix,
)
from tiqora.ai.tool_chain import analyze_tool_chain
from tiqora.ai.tools import (
    MCP_CONTEXT_TICKET_ID,
    TOOL_ADD_INTERNAL_NOTE,
    TOOL_PROPOSE_CUSTOMER_MESSAGE,
    TOOL_UPDATE_TICKET_FIELDS,
    McpToolSpec,
    ToolArgumentError,
    ToolExecutor,
    ToolRegistry,
    validate_mcp_arguments_against_schema,
)

# ---------------------------------------------------------------------------
# #1 prompt safety
# ---------------------------------------------------------------------------


def test_untrusted_system_block_is_nonempty() -> None:
    assert "UNTRUSTED" in UNTRUSTED_CONTENT_SYSTEM_BLOCK
    assert "instructions" in UNTRUSTED_CONTENT_SYSTEM_BLOCK.lower()


def test_tool_result_prefix_idempotent() -> None:
    once = with_untrusted_tool_prefix('{"ok": true}')
    twice = with_untrusted_tool_prefix(once)
    assert once.startswith(UNTRUSTED_TOOL_RESULT_PREFIX)
    assert twice == once


# ---------------------------------------------------------------------------
# #2 / #5 capabilities
# ---------------------------------------------------------------------------


def test_capabilities_off_blocks_ticket_mutations() -> None:
    caps = capabilities_for_autonomy(AUTONOMY_OFF)
    assert caps.propose_message is True
    assert caps.allows_update_ticket_fields() is False
    assert caps.mcp_mutating is False
    assert caps.mcp_readonly is True


def test_capabilities_clarify_allows_state_not_customer() -> None:
    caps = capabilities_for_autonomy(AUTONOMY_CLARIFY_ONLY)
    assert caps.update_state is True
    assert caps.set_customer is False
    assert caps.update_priority is False
    assert caps.mcp_mutating is False


def test_capabilities_full_allows_mutations() -> None:
    caps = capabilities_for_autonomy(AUTONOMY_FULL)
    assert caps.allows_update_ticket_fields() is True
    assert caps.set_customer is True
    assert caps.mcp_mutating is True


def test_capabilities_json_override_tightens() -> None:
    caps = resolve_capabilities(
        AUTONOMY_FULL, capabilities_json='{"set_customer": false, "mcp_mutating": false}'
    )
    assert caps.set_customer is False
    assert caps.mcp_mutating is False
    assert caps.update_state is True  # still from full defaults


def test_registry_hides_update_fields_when_off() -> None:
    registry = ToolRegistry(autonomy=AUTONOMY_OFF)
    names = {s["function"]["name"] for s in registry.build_schemas()}
    assert TOOL_PROPOSE_CUSTOMER_MESSAGE in names
    assert TOOL_ADD_INTERNAL_NOTE in names
    assert TOOL_UPDATE_TICKET_FIELDS not in names
    assert not registry.is_known(TOOL_UPDATE_TICKET_FIELDS)


def test_registry_shows_update_fields_when_full() -> None:
    registry = ToolRegistry(autonomy=AUTONOMY_FULL)
    names = {s["function"]["name"] for s in registry.build_schemas()}
    assert TOOL_UPDATE_TICKET_FIELDS in names
    assert registry.is_known(TOOL_UPDATE_TICKET_FIELDS)


def test_registry_update_fields_schema_omits_customer_in_clarify_only() -> None:
    registry = ToolRegistry(autonomy=AUTONOMY_CLARIFY_ONLY)
    schema = next(
        s for s in registry.build_schemas() if s["function"]["name"] == TOOL_UPDATE_TICKET_FIELDS
    )
    props = schema["function"]["parameters"]["properties"]
    assert "state" in props
    assert "customer_id" not in props
    assert "priority_id" not in props


# ---------------------------------------------------------------------------
# #3 MCP context inject + #4 schema validation
# ---------------------------------------------------------------------------


def test_validate_mcp_args_rejects_unknown_when_schema_has_properties() -> None:
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    validate_mcp_arguments_against_schema({"query": "x"}, schema)
    with pytest.raises(ToolArgumentError, match="not in schema"):
        validate_mcp_arguments_against_schema({"query": "x", "extra": 1}, schema)
    with pytest.raises(ToolArgumentError, match="required"):
        validate_mcp_arguments_against_schema({}, schema)


def test_validate_mcp_args_noop_without_properties() -> None:
    validate_mcp_arguments_against_schema({"anything": 1}, None)
    validate_mcp_arguments_against_schema({"anything": 1}, {"type": "object"})


@pytest.mark.asyncio
async def test_mcp_injects_server_ticket_context() -> None:
    called: list[dict] = []

    async def _spy(url, token, tool, args):  # noqa: ANN001
        called.append(args)
        return {"ok": True}

    spec = McpToolSpec(
        client_name="netadmin",
        client_url="https://mcp.example/n",
        auth_token=None,
        tool_name="diagnose",
        mutating=False,
        parameters_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    registry = ToolRegistry(autonomy=AUTONOMY_OFF, mcp_tools=[spec])
    executor = ToolExecutor(
        session=None,  # type: ignore[arg-type]
        sysconfig=None,  # type: ignore[arg-type]
        registry=registry,
        ticket_id=42,
        acting_user_id=1,
        pii=PiiMapper(),
        escalation_rules=None,
        mcp_caller=_spy,
        ticket_customer_id="CUST-1",
        ticket_customer_user_id="user@example.com",
    )
    outcome = await executor.execute(spec.full_name, {"query": "status"})
    assert called == [
        {
            "query": "status",
            MCP_CONTEXT_TICKET_ID: 42,
            "_tiqora_customer_id": "CUST-1",
            "_tiqora_customer_user_id": "user@example.com",
        }
    ]
    assert outcome.content_for_model.startswith(UNTRUSTED_TOOL_RESULT_PREFIX)


@pytest.mark.asyncio
async def test_mcp_rejects_model_supplied_tiqora_context_keys() -> None:
    async def _spy(url, token, tool, args):  # noqa: ANN001
        return {}

    spec = McpToolSpec(
        client_name="netadmin",
        client_url="https://mcp.example/n",
        auth_token=None,
        tool_name="diagnose",
        mutating=False,
    )
    registry = ToolRegistry(autonomy=AUTONOMY_OFF, mcp_tools=[spec])
    executor = ToolExecutor(
        session=None,  # type: ignore[arg-type]
        sysconfig=None,  # type: ignore[arg-type]
        registry=registry,
        ticket_id=1,
        acting_user_id=1,
        pii=PiiMapper(),
        escalation_rules=None,
        mcp_caller=_spy,
    )
    with pytest.raises(ToolArgumentError, match="reserved"):
        await executor.execute(spec.full_name, {"_tiqora_ticket_id": 999})


# ---------------------------------------------------------------------------
# #7 output guards
# ---------------------------------------------------------------------------


def test_customer_message_guard_accepts_normal_reply() -> None:
    validate_customer_message(kind="reply", subject="Re: help", body="Hello, here is the fix.")


def test_customer_message_guard_rejects_oversized_body() -> None:
    with pytest.raises(CustomerMessageGuardError, match="exceeds"):
        validate_customer_message(
            kind="reply", subject="", body="x" * (MAX_CUSTOMER_BODY_CHARS + 1)
        )


def test_customer_message_guard_rejects_link_flood() -> None:
    body = "\n".join(f"see https://example.com/p/{i}" for i in range(MAX_CUSTOMER_BODY_LINKS + 1))
    with pytest.raises(CustomerMessageGuardError, match="too many links"):
        validate_customer_message(kind="reply", subject="", body=body)


def test_customer_message_guard_rejects_private_key_dump() -> None:
    with pytest.raises(CustomerMessageGuardError, match="blocked"):
        validate_customer_message(
            kind="reply",
            subject="",
            body="Here is the key:\n-----BEGIN RSA PRIVATE KEY-----\nMIIE\n",
        )


@pytest.mark.asyncio
async def test_propose_customer_message_runs_output_guard() -> None:
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
    with pytest.raises(ToolArgumentError, match="exceeds"):
        await executor.execute(
            TOOL_PROPOSE_CUSTOMER_MESSAGE,
            {"kind": "reply", "body": "y" * (MAX_CUSTOMER_BODY_CHARS + 1)},
        )


def test_strip_hallucinated_signoff_keeps_english_closing_drops_placeholder() -> None:
    body = (
        "Please try this: connect your router directly to the wall socket.\n\n"
        "Best regards,\n[Your Name]\nSTW Bonn – StudNet Support"
    )
    assert strip_hallucinated_signoff(body) == (
        "Please try this: connect your router directly to the wall socket.\n\n"
        "Best regards,"
    )


def test_strip_hallucinated_signoff_keeps_with_best_regards_drops_queue_footer() -> None:
    body = (
        "Please use a router from the tested list.\n\n"
        "With best regards\n\n"
        "--\n"
        "Alex Example - NetAdmin StudNet Bonn - netadmin@stw-bonn.de\n"
        "StudNet Hotline 0228-28627252"
    )
    assert strip_hallucinated_signoff(body) == (
        "Please use a router from the tested list.\n\nWith best regards"
    )


def test_strip_hallucinated_signoff_keeps_german_closing_drops_placeholder() -> None:
    body = "Bitte starte den Router neu.\n\nMit freundlichen Grüßen\n[Ihr Name]"
    assert strip_hallucinated_signoff(body) == (
        "Bitte starte den Router neu.\n\nMit freundlichen Grüßen"
    )


def test_strip_hallucinated_signoff_leaves_normal_body_untouched() -> None:
    body = "Hello,\n\nHere is the fix for your connection issue."
    assert strip_hallucinated_signoff(body) == body


def test_strip_hallucinated_signoff_bare_signoff_keeps_the_line() -> None:
    assert strip_hallucinated_signoff("Best regards,\n[Your Name]") == "Best regards,"


@pytest.mark.asyncio
async def test_propose_customer_message_strips_hallucinated_signoff() -> None:
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
    outcome = await executor.execute(
        TOOL_PROPOSE_CUSTOMER_MESSAGE,
        {
            "kind": "reply",
            "body": (
                "Hi Christina,\n\nI've checked your connection and there's no port "
                "lock in place.\n\nBest regards,\n[Your Name]\nSTW Bonn – StudNet "
                "Support"
            ),
        },
    )
    assert outcome.proposal is not None
    assert outcome.proposal["body"] == (
        "Hi Christina,\n\nI've checked your connection and there's no port "
        "lock in place.\n\nBest regards,"
    )


# ---------------------------------------------------------------------------
# #10 tool-chain alerts
# ---------------------------------------------------------------------------


def test_tool_chain_alerts_on_mcp_mutate_and_propose() -> None:
    alerts = analyze_tool_chain(
        ["netadmin:diagnose", TOOL_UPDATE_TICKET_FIELDS, TOOL_PROPOSE_CUSTOMER_MESSAGE]
    )
    codes = {a.code for a in alerts}
    assert "mcp_mutate_and_propose" in codes


def test_tool_chain_no_alert_for_simple_propose() -> None:
    assert analyze_tool_chain([TOOL_PROPOSE_CUSTOMER_MESSAGE]) == []


def test_capabilities_override_invalid_json_ignored() -> None:
    caps = resolve_capabilities(AUTONOMY_OFF, capabilities_json="not-json")
    assert caps == capabilities_for_autonomy(AUTONOMY_OFF)


def test_agent_capabilities_public_dict() -> None:
    d = AgentCapabilities(update_state=True).to_public_dict()
    assert d["update_state"] is True
    assert d["set_customer"] is False
