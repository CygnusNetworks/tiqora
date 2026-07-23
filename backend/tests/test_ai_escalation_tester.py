"""Unit tests for the admin escalation-rule tester endpoint (dry-run only).

The route is a pure function of its body — no DB session involved — so these
call the router function directly with a stub admin user.
"""

from __future__ import annotations

import json

import pytest

from tiqora.api.v1.admin import ai as admin_ai
from tiqora.api.v1.admin.ai_schemas import EscalationTestIn
from tiqora.domain.auth import AuthenticatedUser

RULES = json.dumps(
    [
        {
            "tool": "netadmin:diagnose_connection",
            "field": "lock_code",
            "match": "exact",
            "values": ["COPYR", "DDOSA"],
        }
    ]
)


def _admin() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


@pytest.mark.asyncio
async def test_escalation_tester_reports_a_hit() -> None:
    out = await admin_ai.escalation_test_route(
        EscalationTestIn(
            rules_json=RULES,
            tool="netadmin:diagnose_connection",
            sample_json=json.dumps({"lock_code": "COPYR", "note": "x"}),
        ),
        _admin(),
    )
    assert out.valid is True
    assert out.error is None
    assert out.hit is not None
    assert out.hit.rule_index == 0
    assert out.hit.value == "COPYR"


@pytest.mark.asyncio
async def test_escalation_tester_no_hit_for_other_tool_or_value() -> None:
    no_tool = await admin_ai.escalation_test_route(
        EscalationTestIn(
            rules_json=RULES,
            tool="other:tool",
            sample_json=json.dumps({"lock_code": "COPYR"}),
        ),
        _admin(),
    )
    assert no_tool.valid is True and no_tool.hit is None

    no_value = await admin_ai.escalation_test_route(
        EscalationTestIn(
            rules_json=RULES,
            tool="netadmin:diagnose_connection",
            sample_json=json.dumps({"lock_code": "FINE"}),
        ),
        _admin(),
    )
    assert no_value.valid is True and no_value.hit is None


@pytest.mark.asyncio
async def test_escalation_tester_invalid_rules() -> None:
    bad_json = await admin_ai.escalation_test_route(
        EscalationTestIn(rules_json="{not json", tool="a:b", sample_json="{}"), _admin()
    )
    assert bad_json.valid is False
    assert bad_json.error is not None and "rules_json" in bad_json.error

    not_array = await admin_ai.escalation_test_route(
        EscalationTestIn(rules_json='{"tool": "x"}', tool="a:b", sample_json="{}"), _admin()
    )
    assert not_array.valid is False

    bad_rule = await admin_ai.escalation_test_route(
        EscalationTestIn(
            rules_json=json.dumps([{"tool": "a:b", "match": "nope", "values": ["x"]}]),
            tool="a:b",
            sample_json="{}",
        ),
        _admin(),
    )
    assert bad_rule.valid is False
    assert bad_rule.error is not None and "match" in bad_rule.error


@pytest.mark.asyncio
async def test_escalation_tester_invalid_sample_json_keeps_rules_valid() -> None:
    out = await admin_ai.escalation_test_route(
        EscalationTestIn(
            rules_json=RULES, tool="netadmin:diagnose_connection", sample_json="{oops"
        ),
        _admin(),
    )
    assert out.valid is True
    assert out.error is not None and "sample_json" in out.error
    assert out.hit is None


@pytest.mark.asyncio
async def test_escalation_tester_empty_rules_never_hit() -> None:
    out = await admin_ai.escalation_test_route(
        EscalationTestIn(rules_json="", tool="a:b", sample_json='{"x": 1}'), _admin()
    )
    assert out.valid is True and out.hit is None
