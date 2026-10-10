from __future__ import annotations

from tiqora.channels.registry import CHANNELS
from tiqora.jobs.registry import JOB_HANDLERS
from tiqora.plugins.events import EVENT_SUBSCRIBERS
from tiqora.plugins.mcp import MCP_TOOLS
from tiqora.plugins.routes import PLUGIN_ROUTES
from tiqora.plugins.tasks import SCHEDULED_TASKS
from tiqora.settings_sections.section import SETTINGS_SECTIONS

EXPECTED = {
    "SDK_VERSION",
    "Plugin",
    "PluginRegistry",
    "RegistrationRejected",
    "PluginContext",
    "PluginHttp",
    "OutboundHostNotAllowed",
    "TicketSummary",
    "TicketEvent",
    "EventContext",
    "EventHandler",
    "TaskContext",
    "TaskRun",
    "JobHandler",
    "JobContext",
    "JobRetry",
    "enqueue_job",
    "CurrentAgent",
    "AdminAgent",
    "DbSession",
}


def test_surface_is_exactly_the_planned_one() -> None:
    import tiqora_sdk

    assert set(tiqora_sdk.__all__) == EXPECTED
    assert len(tiqora_sdk.__all__) == len(EXPECTED)
    for name in EXPECTED:
        assert getattr(tiqora_sdk, name) is not None
    assert tiqora_sdk.SDK_VERSION == "0.1.0"


def test_registry_protocol_matches_registrar() -> None:
    from tiqora.plugins.registrar import PluginRegistrar
    from tiqora_sdk import PluginRegistry

    public = {n for n in vars(PluginRegistrar) if not n.startswith("_") and n != "staged"}
    proto = {n for n in vars(PluginRegistry) if not n.startswith("_")}
    assert (
        public
        == proto
        == {
            "plugin_id",
            "context",
            "settings",
            "route",
            "on_event",
            "scheduled_task",
            "job_handler",
            "mcp_tool",
        }
    )


def test_mcp_helpers() -> None:
    from tiqora_sdk import mcp

    assert {"current_agent_id", "mcp_session_factory", "permission_engine"} == set(mcp.__all__)


def test_phase1_points_are_sdk_stable() -> None:
    assert all(
        p.sdk_stable
        for p in (
            SETTINGS_SECTIONS,
            JOB_HANDLERS,
            PLUGIN_ROUTES,
            EVENT_SUBSCRIBERS,
            SCHEDULED_TASKS,
            MCP_TOOLS,
        )
    )
    assert CHANNELS.sdk_stable is False


def test_core_mcp_tool_names_cover_server_source() -> None:
    import re
    from pathlib import Path

    import tiqora.mcp_server.server as server
    from tiqora.plugins.mcp import CORE_MCP_TOOL_NAMES

    src = Path(server.__file__).read_text()
    names = set(re.findall(r"@mcp\.tool\((?:[^@]*?)\)\n(?:async )?def (\w+)", src))
    assert names == CORE_MCP_TOOL_NAMES
