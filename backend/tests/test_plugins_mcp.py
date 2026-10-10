from __future__ import annotations

import asyncio
import inspect
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client, Context, FastMCP

from tests.plugins.helpers import Installer
from tiqora.extensions.core import register_core
from tiqora.extensions.registry import Registry
from tiqora.mcp_server import server as mcp_server
from tiqora.mcp_server.server import _current_api_key_scopes
from tiqora.mcp_server.server import mcp as core_mcp
from tiqora.plugins.context import make_plugin_context
from tiqora.plugins.discovery import discover_plugins
from tiqora.plugins.loader import commit_registrar, import_and_register
from tiqora.plugins.mcp import (
    CORE_MCP_TOOL_NAMES,
    MCP_TOOLS,
    McpTool,
    plugin_mcp_failures,
    register_plugin_mcp_tools,
)
from tiqora.plugins.state import PluginLoadResult, RuntimeState, set_loaded_plugins

PLUGIN = "org.example.plg49mcp"


@pytest.fixture(autouse=True)
def _reset_loaded() -> Iterator[None]:
    yield
    set_loaded_plugins([])


async def echo(ctx: Context, text: str, times: int = 1) -> dict[str, str]:
    return {"text": text * times}


async def sleepy(ctx: Context) -> dict[str, str]:
    await asyncio.sleep(2)
    return {}


async def boom(ctx: Context) -> dict[str, str]:
    raise RuntimeError("secret-token-123 /etc/settings")


def _tool(name: str, fn: Any, *, write: bool = False, timeout: int = 5) -> McpTool:
    return McpTool(
        name=name, plugin_id=PLUGIN, fn=fn, description="x", write=write, timeout_seconds=timeout
    )


def _active(state: RuntimeState = RuntimeState.ACTIVE) -> None:
    set_loaded_plugins([PluginLoadResult(PLUGIN, "1.0.0", state, None, ())])


def _registry(*tools: McpTool, state: RuntimeState = RuntimeState.ACTIVE) -> Registry:
    reg = Registry()
    for t in tools:
        reg.add(MCP_TOOLS, t, owner=t.plugin_id)
    _active(state)
    return reg


def _hello_registry(fixture_plugins: Installer) -> Registry:
    fixture_plugins()
    (p,) = discover_plugins()
    reg = Registry()
    register_core(reg)
    commit_registrar(reg, import_and_register(p, make_plugin_context(p)))
    set_loaded_plugins(
        [PluginLoadResult("org.example.hello", "1.0.0", RuntimeState.ACTIVE, None, ())]
    )
    return reg


async def _call(
    server: FastMCP, name: str, args: dict[str, Any], scopes: frozenset[str] | None = None
):
    token = _current_api_key_scopes.set(scopes)
    try:
        async with Client(server) as client:
            return await client.call_tool(name, args, raise_on_error=False)
    finally:
        _current_api_key_scopes.reset(token)


def _text(res: Any) -> str:
    return str(res.content[0].text)


async def test_core_names_constant_matches_server() -> None:
    async with Client(core_mcp) as client:
        assert {t.name for t in await client.list_tools()} == CORE_MCP_TOOL_NAMES


async def test_registers_plugin_tool_on_fresh_server(fixture_plugins: Installer) -> None:
    server = FastMCP("t")
    assert register_plugin_mcp_tools(server, _hello_registry(fixture_plugins)) == ["hello_echo"]
    async with Client(server) as client:
        (tool,) = [t for t in await client.list_tools() if t.name == "hello_echo"]
    assert set(tool.inputSchema["properties"]) == {"text", "times"}
    assert tool.annotations is not None and tool.annotations.readOnlyHint is True
    assert "The text to echo." in tool.inputSchema["properties"]["text"].get("description", "")


async def test_tool_call_respects_allowlist(fixture_plugins: Installer) -> None:
    server = FastMCP("t")
    register_plugin_mcp_tools(server, _hello_registry(fixture_plugins))
    denied = await _call(
        server, "hello_echo", {"text": "x"}, frozenset({"mcp:rw", "tool:ticket_get"})
    )
    assert denied.is_error and "allowlist" in _text(denied)
    allowed = await _call(
        server, "hello_echo", {"text": "ab", "times": 2}, frozenset({"tool:hello_echo"})
    )
    assert allowed.data == {"text": "abab"}
    unrestricted = await _call(server, "hello_echo", {"text": "ab", "times": 2})
    assert unrestricted.data == {"text": "abab"}


async def test_write_tool_requires_rw() -> None:
    server = FastMCP("t")
    register_plugin_mcp_tools(server, _registry(_tool("hello_put", echo, write=True)))
    res = await _call(server, "hello_put", {"text": "x"}, frozenset({"mcp:ro"}))
    assert res.is_error and "mcp:rw" in _text(res)
    ok = await _call(server, "hello_put", {"text": "x"})  # unrestricted key may write
    assert ok.data == {"text": "x"}
    ok_rw = await _call(server, "hello_put", {"text": "y"}, frozenset({"mcp:rw"}))
    assert ok_rw.data == {"text": "y"}


async def test_read_tool_with_ro_key_is_allowed() -> None:
    server = FastMCP("t")
    register_plugin_mcp_tools(server, _registry(_tool("hello_read", echo)))
    res = await _call(server, "hello_read", {"text": "x"}, frozenset({"mcp:ro"}))
    assert not res.is_error and res.data == {"text": "x"}


async def test_tool_timeout() -> None:
    server = FastMCP("t")
    register_plugin_mcp_tools(server, _registry(_tool("hello_sleepy", sleepy, timeout=1)))
    started = time.monotonic()
    res = await _call(server, "hello_sleepy", {})
    assert res.is_error and "Zeitlimit von 1 Sekunden" in _text(res)
    assert time.monotonic() - started < 1.9


async def test_plugin_exception_is_a_clean_error_and_counts() -> None:
    server = FastMCP("t")
    register_plugin_mcp_tools(server, _registry(_tool("hello_boom", boom)))
    before = plugin_mcp_failures(PLUGIN)
    res = await _call(server, "hello_boom", {})
    assert res.is_error
    text = _text(res)
    assert "secret-token-123" not in text and "Traceback" not in text and "/etc" not in text
    assert plugin_mcp_failures(PLUGIN) == before + 1
    ok = await _call(server, "hello_boom", {})
    assert ok.is_error and plugin_mcp_failures(PLUGIN) == before + 2
    # a success resets the streak
    server2 = FastMCP("u")
    register_plugin_mcp_tools(server2, _registry(_tool("hello_fine", echo)))
    assert (await _call(server2, "hello_fine", {"text": "a"})).data == {"text": "a"}
    assert plugin_mcp_failures(PLUGIN) == 0


async def test_idempotent_and_inactive(fixture_plugins: Installer) -> None:
    server = FastMCP("t")
    reg = _hello_registry(fixture_plugins)
    assert register_plugin_mcp_tools(server, reg) == ["hello_echo"]
    assert register_plugin_mcp_tools(server, reg) == []
    other = FastMCP("u")
    for state in (RuntimeState.FAILED, RuntimeState.DISABLED, RuntimeState.QUARANTINED):
        set_loaded_plugins([PluginLoadResult("org.example.hello", "1.0.0", state, "x", ())])
        assert register_plugin_mcp_tools(other, reg) == []
        async with Client(other) as client:
            assert await client.list_tools() == []


async def test_tool_not_callable_after_plugin_goes_inactive() -> None:
    server = FastMCP("t")
    reg = _registry(_tool("hello_late", echo))
    register_plugin_mcp_tools(server, reg)
    assert (await _call(server, "hello_late", {"text": "a"})).data == {"text": "a"}
    _active(RuntimeState.QUARANTINED)
    res = await _call(server, "hello_late", {"text": "a"})
    assert res.is_error and "nicht verfügbar" in _text(res)


async def test_wrapper_reads_the_live_registry_after_content_swap() -> None:
    # Late boot: the registry object stays, its content is replaced in place.
    live = Registry()
    server = FastMCP("t")
    assert register_plugin_mcp_tools(server, live) == []
    fresh = _registry(_tool("hello_late", echo))
    live.replace_contents(fresh)
    assert register_plugin_mcp_tools(server, live) == ["hello_late"]
    assert (await _call(server, "hello_late", {"text": "z"})).data == {"text": "z"}

    async def echo_v2(ctx: Context, text: str, times: int = 1) -> dict[str, str]:
        return {"text": "v2"}

    swapped = Registry()
    swapped.add(MCP_TOOLS, _tool("hello_late", echo_v2), owner=PLUGIN)
    live.replace_contents(swapped)
    assert (await _call(server, "hello_late", {"text": "z"})).data == {"text": "v2"}
    live.replace_contents(Registry())  # tool gone from the live registry
    res = await _call(server, "hello_late", {"text": "z"})
    assert res.is_error and "nicht verfügbar" in _text(res)


async def test_core_tool_name_is_never_bound() -> None:
    server = FastMCP("t")
    reg = Registry()
    reg.add(MCP_TOOLS, _tool("ticket_get", echo), owner=PLUGIN)  # bypasses the registrar
    _active()
    assert register_plugin_mcp_tools(server, reg) == []
    async with Client(server) as client:
        assert await client.list_tools() == []


def test_lifespan_binds_plugin_tools() -> None:
    src = inspect.getsource(mcp_server._app_lifespan)
    assert "register_plugin_mcp_tools(mcp, registry)" in src
    assert re.search(r"registry\s*=\s*await init_registry", src)


def test_sdk_helpers_delegate_to_server() -> None:
    src = Path(inspect.getfile(__import__("tiqora_sdk.mcp", fromlist=["x"]))).read_text()
    assert (
        "_get_user_id" in src
        and "_get_state().session_factory" in src
        and "PermissionEngine" in src
    )
