"""Plugin MCP tool over the real HTTP transport with the real Bearer middleware (both dialects).

Seed band 49303-49349: ticket 49303 (queue 1), agents 49304 (ro on queue 1's group) and 49305
(no group). API keys are named ``plg49-mcp-*``.
"""

from __future__ import annotations

import hashlib
import socket
import threading
import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
import uvicorn
from fastmcp import Client, Context, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from sqlalchemy import create_engine, text
from starlette.middleware import Middleware

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.extensions.registry import Registry
from tiqora.mcp_server import server as mcp_server
from tiqora.plugins.context import PluginTickets
from tiqora.plugins.mcp import MCP_TOOLS, McpTool, register_plugin_mcp_tools
from tiqora.plugins.state import PluginLoadResult, RuntimeState, set_loaded_plugins
from tiqora_sdk.mcp import current_agent_id

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 10, 12, 0, 0)
TICKET, AGENT_RO, AGENT_NONE = 49303, 49304, 49305
KEYS = {AGENT_RO: "tiqora_plg49-mcp-ro", AGENT_NONE: "tiqora_plg49-mcp-none"}
PLUGIN = "org.example.plg49mcp"


def _async_url(sync_url: str) -> str:
    return (
        sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
        .replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    )


def _clean(conn: Any) -> None:
    conn.execute(text("DELETE FROM tiqora_api_key WHERE name LIKE 'plg49-mcp-%'"))
    conn.execute(text("DELETE FROM ticket WHERE id = :id"), {"id": TICKET})
    ids = {"a": AGENT_RO, "b": AGENT_NONE}
    conn.execute(text("DELETE FROM group_user WHERE user_id IN (:a, :b)"), ids)
    conn.execute(text("DELETE FROM users WHERE id IN (:a, :b)"), ids)


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn, checkfirst=True)
    with engine.begin() as conn:
        _clean(conn)
        group_id = int(conn.execute(text("SELECT group_id FROM queue WHERE id = 1")).scalar_one())
        for uid in (AGENT_RO, AGENT_NONE):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :login, 'x', 'Plg49', 'Agent', 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "login": f"plg49.mcp.{uid}", "t": NOW},
            )
            conn.execute(
                text(
                    "INSERT INTO tiqora_api_key (name, key_hash, user_id, valid)"
                    " VALUES (:n, :h, :u, :v)"
                ),
                {
                    "n": f"plg49-mcp-{uid}",
                    "h": hashlib.sha256(KEYS[uid].encode()).hexdigest(),
                    "u": uid,
                    "v": True,
                },
            )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :gid, 'ro', :t, 1, :t, 1)"
            ),
            {"uid": AGENT_RO, "gid": group_id, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'plg4949303', 'plg49 secret title', 1, 1, 1, 1, 1, 3, 1,"
                " 'plg49-cust', 'plg49.cust@example.org', 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {"id": TICKET, "t": NOW},
        )
    engine.dispose()


async def peek(ctx: Context, ticket_id: int) -> dict[str, Any]:
    """Look a ticket up the way plugin authors are told to."""
    ticket = await PluginTickets().get(ticket_id, as_agent=current_agent_id(ctx))
    return {"found": ticket is not None, "title": None if ticket is None else ticket.title}


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def mcp_url(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    state = mcp_server.McpState(Settings(environment="test", database_url=_async_url(sync_url)))
    monkeypatch.setattr(mcp_server, "_mcp_state", state)
    monkeypatch.setattr("tiqora.plugins.context.get_session_factory", lambda: state.session_factory)
    reg = Registry()
    reg.add(
        MCP_TOOLS,
        McpTool(
            name="plgmcp_peek",
            plugin_id=PLUGIN,
            fn=peek,
            description="Ticket-Titel nachsehen",
            write=False,
            timeout_seconds=20,
        ),
        owner=PLUGIN,
    )
    set_loaded_plugins([PluginLoadResult(PLUGIN, "1.0.0", RuntimeState.ACTIVE, None, ())])
    server = FastMCP("t")
    register_plugin_mcp_tools(server, reg)
    app = server.http_app(
        transport="streamable-http", middleware=[Middleware(mcp_server.TiqoraBearerAuth)]
    )
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not uv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert uv.started
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        uv.should_exit = True
        thread.join(timeout=10)
        state.engine.sync_engine.dispose(close=False)
        set_loaded_plugins([])
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            _clean(conn)
        engine.dispose()


async def _peek(url: str, agent: int) -> dict[str, Any]:
    transport = StreamableHttpTransport(url, headers={"Authorization": f"Bearer {KEYS[agent]}"})
    async with Client(transport) as client:
        res = await client.call_tool("plgmcp_peek", {"ticket_id": TICKET})
    assert isinstance(res.data, dict)
    return res.data


async def test_agent_with_queue_right_sees_the_ticket(mcp_url: str) -> None:
    assert await _peek(mcp_url, AGENT_RO) == {"found": True, "title": "plg49 secret title"}


async def test_agent_without_queue_right_gets_nothing(mcp_url: str) -> None:
    assert await _peek(mcp_url, AGENT_NONE) == {"found": False, "title": None}
