"""Plugin roles and the admin status summary (no database)."""

from __future__ import annotations

import pytest

from tiqora.plugins.state import PLUGIN_ROLES, RuntimeState, summarize_status


def test_ai_worker_role_listed() -> None:
    assert "ai-worker" in PLUGIN_ROLES
    assert PLUGIN_ROLES[:3] == ("api", "worker", "mcp")


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ({"api": RuntimeState.ACTIVE, "ai-worker": RuntimeState.FAILED}, "failed"),
        ({"api": RuntimeState.ACTIVE, "ai-worker": RuntimeState.QUARANTINED}, "quarantined"),
        ({"api": RuntimeState.ACTIVE, "ai-worker": RuntimeState.ACTIVE}, "active"),
        ({"api": RuntimeState.ACTIVE}, "active"),  # no ai-worker container: not a problem
    ],
)
def test_summarize_status_with_ai_worker(rows: dict[str, RuntimeState], expected: str) -> None:
    status = summarize_status(
        enabled=True,
        problem=None,
        compat_problem=None,
        api_state=rows.get("api"),
        other_states=[v for k, v in rows.items() if k != "api"],
    )
    assert status == expected
