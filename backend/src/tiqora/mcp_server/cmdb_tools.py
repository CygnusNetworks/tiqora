"""Read-only CMDB tools of the Tiqora MCP server: ``cmdb_search``, ``cmdb_get``, ``cmdb_impact``.

Every tool resolves the agent, checks the key's tool allowlist, opens its own session and asks the
ITSM mode first. Class-group visibility is the CMDB's own: a CI of a class the agent may not read
is answered like an unknown one, and the impact analysis lists only customers, services and open
tickets reached through readable CIs and readable queues (MCP is an exfiltration path, so nothing
is filtered "by the model").

Mode B (``tiqora``) is served by the Tiqora CMDB, Mode A (``znuny_readthrough``) by the read-only
Znuny adapter for search and get; the impact analysis exists only in Mode B.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

import structlog
from fastmcp import Context
from fastmcp.exceptions import ToolError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.access import load_access
from tiqora.cmdb.errors import CmdbError
from tiqora.cmdb.links import LinkService
from tiqora.cmdb.provider import TiqoraCiProvider
from tiqora.itsm.ci_provider import (
    CiAttribute,
    CiDetail,
    CiError,
    CiProvider,
    CiQuery,
    CiRef,
    CiSummary,
)
from tiqora.itsm.importer.facades import CmdbServiceReader
from tiqora.itsm.mode import get_itsm_mode
from tiqora.itsm.znuny_adapter.provider import ZnunyCiProvider
from tiqora.mcp_server import server as _server
from tiqora.mcp_server.server import mcp
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

SEARCH_LIMIT_MAX: Final = 100
IMPACT_DEPTH_MAX: Final = 5  # cmdb ``impact_graph`` clamps to 1-5
ATTRIBUTE_VALUE_MAX: Final = 200
_NOT_FOUND: Final = "CI not found (or not readable for this API key's user)"
_OFF: Final = "ITSM is switched off on this site (itsm.mode = off)"
_READONLY: Final = {"readOnlyHint": True}


async def _provider(session: AsyncSession) -> CiProvider:
    """The CI provider of the current mode; ``ToolError`` while ITSM is off."""
    mode = (await get_itsm_mode(session)).mode
    if mode == "off":
        raise ToolError(_OFF)
    if mode == "znuny_readthrough":
        return ZnunyCiProvider(SysConfig(session))
    return TiqoraCiProvider()


def _summary_out(s: CiSummary) -> dict[str, Any]:
    return {
        "ci_id": s.ref.id,
        "number": s.number,
        "name": s.name,
        "class": s.class_name,
        "deployment_state": s.deployment_state.name if s.deployment_state else None,
        "incident_state": s.incident_state.name if s.incident_state else None,
    }


@mcp.tool(
    description=(
        "Search configuration items (CMDB) by number or name. Read-only. Only classes the "
        "agent may read are searched. 'class_key' is the class key, 'deployment_state' and "
        "'incident_state' are state keys. Returns ci_id, number, name, class (display name), "
        "deployment_state and incident_state (names)."
    ),
    annotations=_READONLY,
)
async def cmdb_search(
    ctx: Context,
    query: str = "",
    class_key: str | None = None,
    deployment_state: str | None = None,
    incident_state: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search CIs.

    Args:
        query: Text matched against CI number and name (empty: no text filter).
        class_key: Restrict to one class (class key).
        deployment_state: Restrict to one deployment state key (e.g. 'production').
        incident_state: Restrict to one incident state key (e.g. 'operational').
        limit: Maximum results (1-100).
    """
    user_id = _server._get_user_id(ctx)
    _server._assert_tool_allowed("cmdb_search")
    state = _server._get_state()
    async with state.session_factory() as session:
        provider = await _provider(session)
        page = await provider.search(
            session,
            CiQuery(
                text=query.strip() or None,
                class_keys=(class_key,) if class_key else (),
                deployment_state_keys=(deployment_state,) if deployment_state else (),
                incident_state_keys=(incident_state,) if incident_state else (),
                limit=max(1, min(int(limit), SEARCH_LIMIT_MAX)),
            ),
            user_id=user_id,
        )
        return [_summary_out(s) for s in page.items]


def _flatten(
    nodes: Sequence[CiAttribute], prefix: str, out: dict[str, list[str]], budget: list[int]
) -> None:
    """Collect ``label -> values`` depth first; ``budget[0]`` counts the values still allowed."""
    for node in nodes:
        label = f"{prefix}{node.label}"
        if node.display != "":
            if budget[0] <= 0:
                budget[1] = 1  # truncated
                return
            out.setdefault(label, []).append(node.display)
            budget[0] -= 1
        if node.children:
            _flatten(node.children, f"{label} / ", out, budget)
            if budget[1]:
                return


def _attributes_out(attributes: Sequence[CiAttribute]) -> tuple[dict[str, Any], bool]:
    collected: dict[str, list[str]] = {}
    budget = [ATTRIBUTE_VALUE_MAX, 0]
    _flatten(attributes, "", collected, budget)
    return {k: v[0] if len(v) == 1 else v for k, v in collected.items()}, bool(budget[1])


async def _resolve_ref(
    session: AsyncSession,
    provider: CiProvider,
    *,
    user_id: int,
    ci_id: int | None,
    number: str | None,
) -> CiRef:
    if ci_id is not None:
        return CiRef(provider.source, int(ci_id))
    wanted = (number or "").strip()
    if not wanted:
        raise ToolError("Give either ci_id or number")
    page = await provider.search(
        session, CiQuery(text=wanted, limit=SEARCH_LIMIT_MAX), user_id=user_id
    )
    for item in page.items:
        if item.number == wanted:
            return item.ref
    raise ToolError(_NOT_FOUND)


async def _links_out(session: AsyncSession, detail: CiDetail, user_id: int) -> list[dict[str, Any]]:
    """Links of a Tiqora CI as seen from it; hidden ends are left out. Mode A has none."""
    if detail.summary.ref.source != "tiqora":
        return []
    access = await load_access(session, user_id)
    rows = await LinkService(session).list_for(access, "ci", str(detail.summary.ref.id))
    return [
        {
            "kind": r.other.kind,
            "key": r.other.key,
            "link_type": r.link_type,
            "direction": r.direction,
        }
        for r in rows
        if not r.other.hidden
    ]


@mcp.tool(
    description=(
        "Get one configuration item by ci_id or by its number: states, current version, "
        "attributes (labels from the class definition, catalog values as names) and links to "
        "other objects. Read-only. A CI of a class the agent may not read looks like an "
        "unknown CI."
    ),
    annotations=_READONLY,
)
async def cmdb_get(
    ctx: Context,
    ci_id: int | None = None,
    number: str | None = None,
) -> dict[str, Any]:
    """Fetch a CI.

    Args:
        ci_id: The CI id (as returned by cmdb_search).
        number: The CI number (used when ci_id is not given).
    """
    user_id = _server._get_user_id(ctx)
    _server._assert_tool_allowed("cmdb_get")
    state = _server._get_state()
    async with state.session_factory() as session:
        provider = await _provider(session)
        ref = await _resolve_ref(session, provider, user_id=user_id, ci_id=ci_id, number=number)
        try:
            detail = await provider.get(session, ref, user_id=user_id)
            links = await _links_out(session, detail, user_id)
        except (CiError, CmdbError) as exc:
            logger.info("mcp_cmdb_get_refused", ci=str(ref), error=type(exc).__name__)
            raise ToolError(_NOT_FOUND) from exc
        attributes, truncated = _attributes_out(detail.attributes)
        out = _summary_out(detail.summary)
        out.update(
            {
                "version_id": detail.version.id if detail.version else None,
                "changed": detail.summary.changed_at.isoformat()
                if detail.summary.changed_at
                else None,
                "attributes": attributes,
                "links": links,
            }
        )
        if truncated:
            out["truncated"] = True
        return out


@mcp.tool(
    description=(
        "Impact analysis of a configuration item: the CIs that depend on it, the services and "
        "customers an incident there would reach, and the open tickets linked to them. "
        "Read-only. Needs the Tiqora CMDB (not available while Znuny still keeps the CMDB). "
        "Only readable classes and queues are included."
    ),
    annotations=_READONLY,
)
async def cmdb_impact(ctx: Context, ci_id: int, max_depth: int = 4) -> dict[str, Any]:
    """Impact of a CI.

    Args:
        ci_id: The CI id (as returned by cmdb_search).
        max_depth: How many dependency steps to follow (1-5).
    """
    user_id = _server._get_user_id(ctx)
    _server._assert_tool_allowed("cmdb_impact")
    state = _server._get_state()
    async with state.session_factory() as session:
        mode = (await get_itsm_mode(session)).mode
        if mode == "off":
            raise ToolError(_OFF)
        if mode != "tiqora":
            raise ToolError(
                "Impact analysis needs the Tiqora CMDB. This site still runs Znuny's CMDB "
                "(read-through mode); use cmdb_search and cmdb_get instead."
            )
        reader = CmdbServiceReader(session)
        try:
            root = await TiqoraCiProvider().get(
                session, CiRef("tiqora", int(ci_id)), user_id=user_id
            )
            view = await reader.impact(
                int(ci_id), max_depth=max(1, min(int(max_depth), IMPACT_DEPTH_MAX)), user_id=user_id
            )
        except (CiError, CmdbError) as exc:
            logger.info("mcp_cmdb_impact_refused", ci_id=ci_id, error=type(exc).__name__)
            raise ToolError(_NOT_FOUND) from exc
        return {
            "ci": _summary_out(root.summary),
            "services": [{"id": sid, "name": name} for sid, name in view.services],
            "customers": [{"login": cid, "name": label} for cid, label in view.customers],
            "open_tickets": [
                {"ticket_id": tid, "number": number, "title": title}
                for tid, number, title in view.open_tickets
            ],
            "cis": [
                {"ci_id": cid, "number": number, "name": name}
                for cid, number, name in view.cis
                if cid != int(ci_id)
            ],
            "truncated": view.truncated,
        }


__all__ = ["cmdb_get", "cmdb_impact", "cmdb_search"]
