"""GDPR erasure finds the parameter fields of plugin built-in reports regardless of plugin health.

Catalogue keys never touch the session, so no database is needed here.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest
import structlog.testing
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.extensions.core import register_core, set_registry_for_tests
from tiqora.extensions.registry import Registry
from tiqora.gdpr.saved_criteria import _parameter_fields
from tiqora.plugins.state import is_active, set_loaded_plugins
from tiqora.reporting.catalogue import BUILTIN_REPORTS, BuiltinReport, active_builtin
from tiqora.reporting.definition import ReportDefinition

PLUGIN = "org.example.gone"
KEY = f"plugin.{PLUGIN}.by_customer"
NO_SESSION = cast(AsyncSession, None)


@pytest.fixture
def registry() -> Iterator[Registry]:
    reg = Registry()
    register_core(reg)
    definition = ReportDefinition.model_validate(
        {
            "source": "tickets",
            "dimensions": [{"key": "queue"}],
            "measures": [{"key": "count"}],
            "parameters": [{"key": "p_kunde", "field": "customer_user"}],
        }
    )
    reg.add(
        BUILTIN_REPORTS,
        BuiltinReport(key=KEY, category="customers", definition=definition, plugin_id=PLUGIN),
        owner=PLUGIN,
    )
    reg.freeze()
    set_registry_for_tests(reg)
    set_loaded_plugins([])  # the plugin failed / is disabled in this process
    yield reg
    set_registry_for_tests(None)


async def test_failed_plugin_report_overrides_are_still_scrubbed(registry: Registry) -> None:
    assert not is_active(PLUGIN) and active_builtin(registry, KEY) is None
    assert await _parameter_fields(NO_SESSION, KEY) == {"p_kunde": "customer_user"}


async def test_unknown_plugin_report_is_logged(registry: Registry) -> None:
    with structlog.testing.capture_logs() as logs:
        fields = await _parameter_fields(NO_SESSION, "plugin.org.example.never.x")
    assert fields == {}
    events: list[dict[str, Any]] = [
        e for e in logs if e["event"] == "gdpr_report_parameters_unknown"
    ]
    assert [e["report"] for e in events] == ["plugin.org.example.never.x"]
