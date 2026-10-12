from __future__ import annotations

import json
from pathlib import Path

from tiqora.channels.registry import CHANNELS
from tiqora.jobs.registry import JOB_HANDLERS
from tiqora.plugins.events import EVENT_SUBSCRIBERS
from tiqora.plugins.mcp import MCP_TOOLS
from tiqora.plugins.routes import PLUGIN_ROUTES
from tiqora.plugins.sync_hooks import SYNC_HOOKS
from tiqora.plugins.tasks import SCHEDULED_TASKS
from tiqora.process.actions.registry import TRANSITION_ACTIONS
from tiqora.process.condition_modules import CONDITION_MODULES
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
    assert tiqora_sdk.SDK_VERSION == "0.2.0"


#: exact ``__all__`` of every SDK submodule; later tasks add one entry each
SUBMODULES: dict[str, set[str]] = {
    "tiqora_sdk.tickets": {
        "TicketDraft",
        "PluginWriteError",
        "PluginPermissionDenied",
        "CustomerSummary",
    },
    "tiqora_sdk.hooks": {
        "SyncOperation",
        "HookContext",
        "TicketCreateOp",
        "ArticleOp",
        "QueueMoveOp",
        "StateChangeOp",
        "DynamicFieldsOp",
        "TicketCreatePatch",
        "OperationVetoed",
        "SyncHandler",
    },
    "tiqora_sdk.process": {"ActionContext", "ActionParam", "ConditionRun", "TransitionActionRun"},
}


def test_submodules_are_exact() -> None:
    import importlib

    for module_name, expected in SUBMODULES.items():
        module = importlib.import_module(module_name)
        assert set(module.__all__) == expected, module_name
        assert len(module.__all__) == len(expected), module_name
        for name in expected:
            assert getattr(module, name) is not None, (module_name, name)


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
            "before",
            "transition_action",
            "condition_module",
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
            SYNC_HOOKS,
            TRANSITION_ACTIONS,
            CONDITION_MODULES,
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


def test_no_unexported_public_names() -> None:
    import tiqora_sdk

    public = {n for n in dir(tiqora_sdk) if not n.startswith("_")}
    # "annotations" comes from `from __future__`; "mcp" and the SUBMODULES are submodules.
    submodules = {name.split(".", 1)[1] for name in SUBMODULES}
    assert public <= set(tiqora_sdk.__all__) | {"annotations", "mcp"} | submodules


def test_python_and_npm_sdk_versions_match() -> None:
    import tiqora_sdk

    package_json = Path(__file__).parents[2] / "packages" / "plugin-sdk" / "package.json"
    pkg = json.loads(package_json.read_text(encoding="utf-8"))
    assert pkg["version"] == tiqora_sdk.SDK_VERSION
