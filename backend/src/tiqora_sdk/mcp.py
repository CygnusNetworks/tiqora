"""Helpers for plugin MCP tools (always check permissions with these)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import Context
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

if TYPE_CHECKING:
    from tiqora.permissions.engine import PermissionEngine

__all__ = ["current_agent_id", "mcp_session_factory", "permission_engine"]


def current_agent_id(ctx: Context) -> int:
    """The authenticated agent behind this MCP call (PermissionError when unauthenticated)."""
    from tiqora.mcp_server.server import _get_user_id

    return _get_user_id(ctx)


def mcp_session_factory() -> async_sessionmaker[AsyncSession]:
    """Session factory of the running MCP server."""
    from tiqora.mcp_server.server import _get_state

    return _get_state().session_factory


def permission_engine(session: AsyncSession) -> PermissionEngine:
    """Permission engine for queue/group checks (``await engine.check(agent, queue_id, "ro")``)."""
    from tiqora.permissions.engine import PermissionEngine

    return PermissionEngine(session)
