"""MCP client registry: CRUD + tool discovery (plan §3.3).

Discovery calls the external MCP server's ``tools/list`` via ``fastmcp``'s
``Client`` and upserts :class:`~tiqora.ai.models.TiqoraMcpToolPolicy` rows:
newly seen tools are inserted **disabled** (safe default) with a ``mutating``
prefill taken from the tool's ``annotations`` (``readOnlyHint`` /
``destructiveHint``) when the server provides them — those hints are
untrusted (they come from the external server) so they only seed the admin
UI's default, never an automatic enable. Existing admin decisions
(``enabled``, and any ``mutating`` value an admin already set) are never
overwritten by a later discovery run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.models import TiqoraMcpClient, TiqoraMcpToolPolicy
from tiqora.config import Settings
from tiqora.crypto.secret import decrypt_secret, encrypt_secret

_DISCOVER_TIMEOUT_SECONDS = 20.0


class DiscoveredTool(Protocol):
    """Structural type for the subset of ``mcp.types.Tool`` we use — lets
    tests pass plain objects/mocks without importing the real MCP SDK type."""

    name: str
    description: str | None
    annotations: Any


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    tool_names: list[str]
    added: list[str]
    removed: list[str]


async def list_mcp_clients(session: AsyncSession) -> list[TiqoraMcpClient]:
    rows = (
        (await session.execute(select(TiqoraMcpClient).order_by(TiqoraMcpClient.name)))
        .scalars()
        .all()
    )
    return list(rows)


async def get_mcp_client(session: AsyncSession, client_id: int) -> TiqoraMcpClient | None:
    return await session.get(TiqoraMcpClient, client_id)


async def create_mcp_client(
    session: AsyncSession,
    *,
    settings: Settings,
    change_by: int,
    name: str,
    url: str,
    auth_token: str | None,
    transport: str,
) -> TiqoraMcpClient:
    row = TiqoraMcpClient(
        name=name,
        url=url,
        auth_token_enc=encrypt_secret(settings.secret_key, auth_token) if auth_token else None,
        transport=transport,
        create_by=change_by,
        change_by=change_by,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def update_mcp_client(
    session: AsyncSession,
    row: TiqoraMcpClient,
    *,
    settings: Settings,
    change_by: int,
    name: str | None = None,
    url: str | None = None,
    auth_token: str | None = None,
    transport: str | None = None,
    valid_id: int | None = None,
) -> TiqoraMcpClient:
    if name is not None:
        row.name = name
    if url is not None:
        row.url = url
    if auth_token is not None and auth_token != "":
        row.auth_token_enc = encrypt_secret(settings.secret_key, auth_token)
    if transport is not None:
        row.transport = transport
    if valid_id is not None:
        row.valid_id = valid_id
    row.change_by = change_by
    row.change_time = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    await session.refresh(row)
    return row


async def delete_mcp_client(session: AsyncSession, row: TiqoraMcpClient) -> None:
    await session.delete(row)
    await session.commit()


def mcp_client_to_public_dict(row: TiqoraMcpClient) -> dict[str, object]:
    return {
        "id": row.id,
        "name": row.name,
        "url": row.url,
        "has_auth_token": bool(row.auth_token_enc),
        "transport": row.transport,
        "last_discovered_at": row.last_discovered_at,
        "valid_id": int(row.valid_id),
        "create_time": row.create_time,
        "change_time": row.change_time,
    }


def _prefill_mutating(annotations: Any) -> bool:
    """Conservative default: mutating unless the server explicitly hints
    read-only. Hints are untrusted input from the external server."""
    if annotations is None:
        return True
    read_only = getattr(annotations, "readOnlyHint", None)
    if read_only is None and isinstance(annotations, dict):
        read_only = annotations.get("readOnlyHint")
    if read_only is True:
        return False
    destructive = getattr(annotations, "destructiveHint", None)
    if destructive is None and isinstance(annotations, dict):
        destructive = annotations.get("destructiveHint")
    if destructive is True:
        return True
    return True


async def _fetch_tools_live(mcp_url: str, auth_token: str | None) -> list[DiscoveredTool]:
    """Real discovery via fastmcp's Client. Isolated in its own function so
    tests can monkeypatch it without touching network/fastmcp."""
    from fastmcp import Client

    async with Client(mcp_url, auth=auth_token, timeout=_DISCOVER_TIMEOUT_SECONDS) as client:
        tools = await client.list_tools()
    return list(tools)


async def refresh_tools(
    session: AsyncSession,
    row: TiqoraMcpClient,
    *,
    settings: Settings,
    fetch_tools: Any = None,
) -> DiscoveryResult:
    """Discover tools on the external MCP server and upsert tool policies.

    ``fetch_tools`` is an injectable ``async (url, token) -> list[DiscoveredTool]``
    used by tests to avoid any real network/MCP call; production uses
    :func:`_fetch_tools_live`.
    """
    auth_token = (
        decrypt_secret(settings.secret_key, row.auth_token_enc) if row.auth_token_enc else None
    )
    fetcher = fetch_tools or _fetch_tools_live
    discovered = await fetcher(row.url, auth_token)

    existing = (
        (
            await session.execute(
                select(TiqoraMcpToolPolicy).where(TiqoraMcpToolPolicy.mcp_client_id == row.id)
            )
        )
        .scalars()
        .all()
    )
    existing_by_name = {p.tool_name: p for p in existing}
    discovered_names = {t.name for t in discovered}

    added: list[str] = []
    for tool in discovered:
        policy = existing_by_name.get(tool.name)
        if policy is None:
            session.add(
                TiqoraMcpToolPolicy(
                    mcp_client_id=row.id,
                    tool_name=tool.name,
                    enabled=False,
                    mutating=_prefill_mutating(getattr(tool, "annotations", None)),
                    description_snapshot=getattr(tool, "description", None),
                )
            )
            added.append(tool.name)
        else:
            # Admin's enabled/mutating choices are never overwritten; only
            # refresh the informational description snapshot.
            policy.description_snapshot = getattr(tool, "description", None)

    removed: list[str] = []
    for name, policy in existing_by_name.items():
        if name not in discovered_names:
            removed.append(name)
            if policy.enabled:
                # Keep disabled tools that vanished from discovery so admins
                # retain history; but a tool that was never enabled can be
                # dropped outright to avoid unbounded growth.
                policy.enabled = False
                policy.description_snapshot = (
                    policy.description_snapshot or ""
                ) + " [removed from server]"
            else:
                await session.delete(policy)

    row.tools_json = json.dumps(sorted(discovered_names))
    row.last_discovered_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()

    return DiscoveryResult(tool_names=sorted(discovered_names), added=added, removed=removed)


async def list_tool_policies(session: AsyncSession, client_id: int) -> list[TiqoraMcpToolPolicy]:
    rows = (
        (
            await session.execute(
                select(TiqoraMcpToolPolicy)
                .where(TiqoraMcpToolPolicy.mcp_client_id == client_id)
                .order_by(TiqoraMcpToolPolicy.tool_name)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def set_tool_policy(
    session: AsyncSession,
    client_id: int,
    tool_name: str,
    *,
    enabled: bool | None = None,
    mutating: bool | None = None,
) -> TiqoraMcpToolPolicy | None:
    policy = (
        await session.execute(
            select(TiqoraMcpToolPolicy).where(
                TiqoraMcpToolPolicy.mcp_client_id == client_id,
                TiqoraMcpToolPolicy.tool_name == tool_name,
            )
        )
    ).scalar_one_or_none()
    if policy is None:
        return None
    if enabled is not None:
        policy.enabled = enabled
    if mutating is not None:
        policy.mutating = mutating
    await session.commit()
    await session.refresh(policy)
    return policy


__all__ = [
    "DiscoveredTool",
    "DiscoveryResult",
    "create_mcp_client",
    "delete_mcp_client",
    "get_mcp_client",
    "list_mcp_clients",
    "list_tool_policies",
    "mcp_client_to_public_dict",
    "refresh_tools",
    "set_tool_policy",
    "update_mcp_client",
]
