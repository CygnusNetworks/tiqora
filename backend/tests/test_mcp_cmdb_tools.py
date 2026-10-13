"""MCP tools cmdb_search / cmdb_get / cmdb_impact (names ``itim56``).

Run on the CMDB test world (both dialects): user A reads GROUP_RW and GROUP_RO classes and may
see QUEUE_RW tickets; user B reads GROUP_RO classes only and sees no ticket queue at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextvars import ContextVar
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    CUSTOMER_A,
    GROUP_RO,
    GROUP_RW,
    SERVICE_ID,
    TICKET_CLOSED,
    TICKET_OPEN,
    USER_A,
    USER_B,
    CmdbWorld,
    make_ci,
    make_class,
    set_mode,
)
from tiqora.cmdb.links import LinkEnd, LinkService
from tiqora.cmdb.service import system_access
from tiqora.mcp_server import server
from tiqora.mcp_server.server import mcp

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset


@pytest.fixture
def world(cmdb_db: CmdbWorld) -> Iterator[CmdbWorld]:
    """The CMDB world as the MCP server's state, ITSM mode B."""
    set_mode(cmdb_db, "tiqora")
    previous = server._mcp_state
    server._mcp_state = SimpleNamespace(session_factory=cmdb_db.factory)  # type: ignore[assignment]
    try:
        yield cmdb_db
    finally:
        server._mcp_state = previous


def _as(user_id: int) -> Any:
    return patch("tiqora.mcp_server.server._get_user_id", side_effect=lambda ctx: user_id)


async def _call(name: str, args: dict[str, Any], user_id: int = USER_A) -> Any:
    with _as(user_id):
        async with Client(mcp) as client:
            result = await client.call_tool(name, args)
    return result.data


async def _fail(name: str, args: dict[str, Any], user_id: int = USER_A) -> str:
    with _as(user_id):
        async with Client(mcp) as client:
            with pytest.raises(ToolError) as exc:
                await client.call_tool(name, args)
    return str(exc.value)


async def _link(
    world: CmdbWorld, source: tuple[str, Any], target: tuple[str, Any], kind: str
) -> None:
    async with world.factory() as s:
        await LinkService(s).add(
            system_access(USER_A),
            source=LinkEnd(source[0], str(source[1])),  # type: ignore[arg-type]
            target=LinkEnd(target[0], str(target[1])),  # type: ignore[arg-type]
            link_type_key=kind,
            enforce_permissions=False,
        )
        await s.commit()


async def _two_classes(world: CmdbWorld) -> tuple[int, int]:
    """One CI in the readable class ``computer`` and one in the hidden class ``network``."""
    async with world.factory() as s:
        computer = await make_class(s, key="computer", group_id=GROUP_RO)
        network = await make_class(s, key="network", group_id=GROUP_RW)
        return (
            await make_ci(s, computer, "itim56-computer"),
            await make_ci(s, network, "itim56-switch"),
        )


async def test_cmdb_search_hides_foreign_classes(world: CmdbWorld) -> None:
    await _two_classes(world)
    hits_b = await _call("cmdb_search", {"query": "itim56"}, USER_B)
    hits_a = await _call("cmdb_search", {"query": "itim56"}, USER_A)
    assert {h["class"] for h in hits_b} == {"computer"}  # "network" CI of a foreign group hidden
    assert {h["class"] for h in hits_a} == {"computer", "network"}
    first = hits_b[0]
    assert set(first) == {
        "ci_id",
        "number",
        "name",
        "class",
        "deployment_state",
        "incident_state",
    }
    assert first["name"] == "itim56-computer"
    assert first["deployment_state"] == "Production"
    assert first["incident_state"] == "Operational"


async def test_cmdb_search_filters_and_clamps_limit(world: CmdbWorld) -> None:
    async with world.factory() as s:
        cid = await make_class(s, key="computer", group_id=GROUP_RO)
        for i in range(3):
            await make_ci(s, cid, f"itim56-many-{i}")
        await make_ci(s, cid, "itim56-retired", deployment="retired")
    by_class = await _call("cmdb_search", {"query": "itim56", "class_key": "computer"})
    other_class = await _call("cmdb_search", {"query": "itim56", "class_key": "nope"})
    retired = await _call("cmdb_search", {"query": "itim56", "deployment_state": "retired"})
    capped = await _call("cmdb_search", {"query": "itim56", "limit": 2})
    floor = await _call("cmdb_search", {"query": "itim56", "limit": -5})
    assert len(by_class) == 4 and other_class == []
    assert [h["name"] for h in retired] == ["itim56-retired"]
    assert len(capped) == 2 and len(floor) == 1  # limit clamped to 1..100


async def test_cmdb_get_by_number_renders_catalog_names(world: CmdbWorld) -> None:
    async with world.factory() as s:
        cid = await make_class(s, key="computer", group_id=GROUP_RO)
        ci = await make_ci(
            s,
            cid,
            "itim56-host",
            attributes={
                "Vendor": [{"v": "ACME"}],
                "CPU": [{"v": 8}],
                "NIC": [
                    {"v": "eth0", "s": {"IPAddress": [{"v": "192.0.2.10"}, {"v": "192.0.2.11"}]}}
                ],
            },
            incident="warning",
        )
    number = (await _call("cmdb_search", {"query": "itim56-host"}))[0]["number"]
    out = await _call("cmdb_get", {"number": number})
    by_id = await _call("cmdb_get", {"ci_id": ci})
    assert out == by_id
    assert out["ci_id"] == ci and out["number"] == number and out["class"] == "computer"
    assert out["deployment_state"] == "Production" and out["incident_state"] == "Warning"
    assert out["attributes"]["Vendor"] == "ACME"
    assert out["attributes"]["CPU cores"] == "8"
    assert out["attributes"]["Network interface"] == "eth0"
    assert out["attributes"]["Network interface / IP address"] == ["192.0.2.10", "192.0.2.11"]
    assert out["version_id"] is not None and out["changed"]
    assert out["links"] == [] and "truncated" not in out


async def test_cmdb_get_lists_links_and_hides_unreadable_ends(world: CmdbWorld) -> None:
    visible, hidden = await _two_classes(world)
    await _link(world, ("ci", visible), ("ci", hidden), "ConnectedTo")
    await _link(world, ("ci", visible), ("service", SERVICE_ID), "Includes")
    as_a = await _call("cmdb_get", {"ci_id": visible}, USER_A)
    as_b = await _call("cmdb_get", {"ci_id": visible}, USER_B)
    keys_a = {(link["kind"], link["key"]) for link in as_a["links"]}
    assert keys_a == {("ci", str(hidden)), ("service", str(SERVICE_ID))}
    assert {link["link_type"] for link in as_a["links"]} == {"ConnectedTo", "Includes"}
    assert {link["direction"] for link in as_a["links"]} <= {"outgoing", "incoming", "undirected"}
    assert ("ci", str(hidden)) not in {(link["kind"], link["key"]) for link in as_b["links"]}


async def test_cmdb_get_refuses_unreadable_unknown_and_missing(world: CmdbWorld) -> None:
    visible, hidden = await _two_classes(world)
    denied = await _fail("cmdb_get", {"ci_id": hidden}, USER_B)
    unknown = await _fail("cmdb_get", {"ci_id": 987654}, USER_B)
    nothing = await _fail("cmdb_get", {}, USER_A)
    assert denied == unknown and "not found" in denied  # unreadable looks like unknown
    assert "ci_id or number" in nothing
    assert (await _call("cmdb_get", {"ci_id": visible}, USER_B))["name"] == "itim56-computer"


async def test_cmdb_impact_lists_customers_and_open_tickets(world: CmdbWorld) -> None:
    async with world.factory() as s:
        cid = await make_class(s, key="computer", group_id=GROUP_RW)
        switch = await make_ci(s, cid, "itim56-switch")
        server_ci = await make_ci(s, cid, "itim56-server")
    await _link(world, ("ci", server_ci), ("ci", switch), "DependsOn")
    await _link(world, ("ci", server_ci), ("customer_user", CUSTOMER_A), "RelevantTo")
    await _link(world, ("ci", server_ci), ("service", SERVICE_ID), "Includes")
    await _link(world, ("ticket", TICKET_OPEN), ("ci", server_ci), "RelevantTo")
    await _link(world, ("ticket", TICKET_CLOSED), ("ci", server_ci), "RelevantTo")
    out = await _call("cmdb_impact", {"ci_id": switch})
    assert out["ci"]["ci_id"] == switch and out["ci"]["name"] == "itim56-switch"
    assert out["customers"] == [{"login": CUSTOMER_A, "name": "Cora Customer"}]
    assert [t["ticket_id"] for t in out["open_tickets"]] == [TICKET_OPEN]
    assert out["open_tickets"][0]["title"] == f"cmdb4 ticket {TICKET_OPEN}"
    assert [c["ci_id"] for c in out["cis"]] == [server_ci]  # the root is "ci", not in "cis"
    assert out["truncated"] is False
    assert set(out) == {"ci", "services", "customers", "open_tickets", "cis", "truncated"}


async def test_cmdb_impact_hides_tickets_of_foreign_queue(world: CmdbWorld) -> None:
    async with world.factory() as s:
        cid = await make_class(s, key="computer", group_id=GROUP_RO)
        switch = await make_ci(s, cid, "itim56-switch")
        server_ci = await make_ci(s, cid, "itim56-server")
    await _link(world, ("ci", server_ci), ("ci", switch), "DependsOn")
    await _link(world, ("ticket", TICKET_OPEN), ("ci", server_ci), "RelevantTo")
    as_a = await _call("cmdb_impact", {"ci_id": switch}, USER_A)
    as_b = await _call("cmdb_impact", {"ci_id": switch}, USER_B)
    assert [t["ticket_id"] for t in as_a["open_tickets"]] == [TICKET_OPEN]
    assert as_b["open_tickets"] == []  # B holds no right on QUEUE_RW
    assert [c["ci_id"] for c in as_b["cis"]] == [server_ci]  # the CI itself is readable


async def test_cmdb_impact_hides_customers_reached_through_hidden_ci(world: CmdbWorld) -> None:
    async with world.factory() as s:
        open_class = await make_class(s, key="computer", group_id=GROUP_RO)
        secret_class = await make_class(s, key="network", group_id=GROUP_RW)
        switch = await make_ci(s, open_class, "itim56-switch")
        secret = await make_ci(s, secret_class, "itim56-secret")
    await _link(world, ("ci", secret), ("ci", switch), "DependsOn")
    await _link(world, ("ci", secret), ("customer_user", CUSTOMER_A), "RelevantTo")
    await _link(world, ("ci", secret), ("service", SERVICE_ID), "Includes")
    as_a = await _call("cmdb_impact", {"ci_id": switch}, USER_A)
    as_b = await _call("cmdb_impact", {"ci_id": switch}, USER_B)
    assert [c["login"] for c in as_a["customers"]] == [CUSTOMER_A]
    assert as_b["customers"] == [] and as_b["services"] == []
    assert as_b["cis"] == []  # the hidden CI is neither listed nor named
    assert "itim56-secret" not in str(as_b)


async def test_cmdb_impact_refuses_unreadable_root_and_clamps_depth(world: CmdbWorld) -> None:
    _, hidden = await _two_classes(world)
    assert "not found" in await _fail("cmdb_impact", {"ci_id": hidden}, USER_B)
    chain_class = None
    async with world.factory() as s:
        chain_class = await make_class(s, key="chain", group_id=GROUP_RO)
        ids = [await make_ci(s, chain_class, f"itim56-chain-{i}") for i in range(8)]
    for a, b in zip(ids[1:], ids, strict=False):  # ids[i+1] DependsOn ids[i]
        await _link(world, ("ci", a), ("ci", b), "DependsOn")
    deep = await _call("cmdb_impact", {"ci_id": ids[0], "max_depth": 99})
    shallow = await _call("cmdb_impact", {"ci_id": ids[0], "max_depth": 0})
    assert len(deep["cis"]) == 5  # clamped to 5 steps
    assert len(shallow["cis"]) == 1  # clamped up to 1


async def test_cmdb_impact_in_mode_a_explains(world: CmdbWorld) -> None:
    set_mode(world, "znuny_readthrough")
    message = await _fail("cmdb_impact", {"ci_id": 1})
    assert "Tiqora CMDB" in message and "read-through" in message


async def test_cmdb_tools_say_so_when_itsm_is_off(world: CmdbWorld) -> None:
    set_mode(world, "off")
    for name, args in (
        ("cmdb_search", {"query": "x"}),
        ("cmdb_get", {"ci_id": 1}),
        ("cmdb_impact", {"ci_id": 1}),
    ):
        assert "switched off" in await _fail(name, args)


async def test_cmdb_tools_are_read_only_annotated() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    for name in ("cmdb_search", "cmdb_get", "cmdb_impact"):
        assert tools[name].annotations is not None
        assert tools[name].annotations.readOnlyHint is True
        assert "read-only" in (tools[name].description or "").lower()


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [("cmdb_search", {"query": "x"}), ("cmdb_get", {"ci_id": 1}), ("cmdb_impact", {"ci_id": 1})],
)
async def test_cmdb_tools_respect_tool_allowlist(tool_name: str, args: dict[str, Any]) -> None:
    """A key limited to ``tool:ticket_history`` is refused before any database access."""
    scopes: ContextVar[frozenset[str] | None] = ContextVar(
        "scopes", default=frozenset({"mcp:rw", "tool:ticket_history"})
    )
    state = MagicMock()
    previous = server._mcp_state
    server._mcp_state = state
    try:
        with _as(1), patch("tiqora.mcp_server.server._current_api_key_scopes", scopes):
            async with Client(mcp) as client:
                result = await client.call_tool(tool_name, args, raise_on_error=False)
        assert result.is_error, f"{tool_name} ran despite the tool allowlist"
        state.session_factory.assert_not_called()
    finally:
        server._mcp_state = previous
