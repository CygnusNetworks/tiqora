"""MCP tools ``approval_list_mine`` / ``approval_get`` / ``approval_decide`` (Task 17, F13).

Own ids 971950-971979 are reserved; the helper world (agents AG1..AG7, ticket T1) supplies the
rows. The tools are called through the FastMCP client with the user id, the key scopes and the
session factory patched in; the shared ``approvals_session`` is rolled back at the end.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests._approvals_helpers import (
    AG1,
    AG3,
    AG4,
    AG5,
    AG7,
    T1,
    approvals_session,
    installed_registry,
    make_fake_subject_type,
)
from tiqora.approvals import delegation, service
from tiqora.approvals.contract import Actor, ApprovalDefinition
from tiqora.approvals.settings import APPROVALS_SECTION
from tiqora.config import Settings
from tiqora.jobs.service import utc_now
from tiqora.mcp_server import approval_tools, server
from tiqora.mcp_server.server import mcp
from tiqora.settings_sections.store import save_section_values

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
SETTINGS = Settings(environment="test")
SUBJECT = "test.subject"
RW_DECIDE = frozenset({"mcp:rw", "tool:approval_decide"})
LIST_ITEM_KEYS = {
    "approval_id",
    "ticket_id",
    "ticket_number",
    "title",
    "step",
    "rule",
    "due_at",
    "on_behalf_of",
}


async def _request(session: AsyncSession, approvers: Iterable[int] = (AG3, AG4)) -> int:
    definition = ApprovalDefinition.model_validate(
        {
            "steps": [
                {
                    "name": "Prüfung",
                    "approvers": [{"kind": "user", "ref": str(a)} for a in approvers],
                }
            ]
        }
    )
    info = await service.request_approval(
        session,
        ticket_id=T1,
        subject_type=SUBJECT,
        title="Freigabe appr97",
        description=None,
        actor=Actor.agent(AG1),
        definition=definition,
        approval_key="default",
        settings=SETTINGS,
    )
    await session.commit()  # the tool rolls back first: seeded rows must be committed
    return info.approval_id


async def _enable(session: AsyncSession, on: bool = True) -> None:
    await save_section_values(
        session, APPROVALS_SECTION, {"mcp_decide_enabled": on}, secret_key=SETTINGS.secret_key
    )
    await session.commit()


@asynccontextmanager
async def _as(
    session: AsyncSession, user_id: int, scopes: frozenset[str] | None
) -> AsyncIterator[None]:
    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield session

    state = SimpleNamespace(session_factory=factory, settings=SETTINGS)
    token = server._current_api_key_scopes.set(scopes)
    previous = server._mcp_state
    server._mcp_state = state  # type: ignore[assignment]
    try:
        with (
            patch("tiqora.mcp_server.server._get_user_id", side_effect=lambda ctx: user_id),
            patch.object(approval_tools, "_publish", return_value=None),
        ):
            yield
    finally:
        server._mcp_state = previous
        server._current_api_key_scopes.reset(token)


async def _call(name: str, args: dict[str, Any]) -> Any:
    async with Client(mcp) as client:
        return (await client.call_tool(name, args)).data


async def _fail(name: str, args: dict[str, Any]) -> str:
    async with Client(mcp) as client:
        with pytest.raises(ToolError) as exc:
            await client.call_tool(name, args)
    return str(exc.value)


async def _votes(session: AsyncSession, approval_id: int) -> list[tuple[str, str]]:
    rows = await session.execute(
        text("SELECT channel, decided_by FROM tiqora_approval_vote WHERE approval_id = :a"),
        {"a": approval_id},
    )
    return [(r[0], str(r[1])) for r in rows]


async def _vote_channel(session: AsyncSession, approval_id: int) -> list[str]:
    return [channel for channel, _ in await _votes(session, approval_id)]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_list_mine(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session)
            async with _as(session, AG3, frozenset({"mcp:ro"})):
                mine = await _call("approval_list_mine", {})
                decided = await _call("approval_list_mine", {"state": "decided"})
            async with _as(session, AG5, None):
                other = await _call("approval_list_mine", {})
            assert len(mine) == 1
            item = mine[0]
            assert set(item) == LIST_ITEM_KEYS
            assert (item["approval_id"], item["ticket_id"], item["ticket_number"]) == (
                aid,
                T1,
                f"20261015{T1}",
            )
            assert (item["title"], item["step"], item["rule"]) == (
                "Freigabe appr97",
                "Prüfung",
                "any",
            )
            assert (item["due_at"], item["on_behalf_of"]) == (None, None)
            assert decided == [] and other == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_get_respects_can_view(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session)
            async with _as(session, AG3, frozenset({"mcp:ro"})):
                seen = await _call("approval_get", {"approval_id": aid})
            async with _as(session, AG7, None):
                stranger = await _call("approval_get", {"approval_id": aid})
                missing = await _call("approval_get", {"approval_id": aid + 1000})
            assert stranger == {"error": "approval_not_found"}
            assert missing == {"error": "approval_not_found"}
            assert (seen["id"], seen["status"], seen["title"]) == (
                aid,
                "pending",
                "Freigabe appr97",
            )
            assert {frozenset(e) for e in seen["events"]} == {
                frozenset({"action", "at", "actor_label"})
            }
            assert seen["events"][0]["action"] == "requested"
            assert seen["events"][0]["actor_label"] == "Appr appr97-agent1"
            assert [a["display_name"] for a in seen["steps"][0]["approvers"]] == [
                "Appr appr97-agent3",
                "Appr appr97-agent4",
            ]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_decide_needs_write_scope(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session)
            await _enable(session)
            async with _as(session, AG3, frozenset({"mcp:ro", "tool:approval_decide"})):
                refused = await _call(
                    "approval_decide", {"approval_id": aid, "decision": "approved"}
                )
            assert refused == {"error": "write_scope_required"}
            assert await _vote_channel(session, aid) == []
            async with _as(session, AG3, RW_DECIDE):
                done = await _call(
                    "approval_decide",
                    {"approval_id": aid, "decision": "approved", "comment": "ok"},
                )
            assert done == {"ok": True, "status": "approved", "step_status": "approved"}
            assert await _votes(session, aid) == [("mcp", Actor.agent(AG3).key)]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_decide_refused_while_setting_is_off(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """F13: the setting is the master switch, even for write scope plus explicit tool scope."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session)
            async with _as(session, AG3, RW_DECIDE):
                default_off = await _call(
                    "approval_decide", {"approval_id": aid, "decision": "approved"}
                )
                await _enable(session, False)
                explicit_off = await _call(
                    "approval_decide", {"approval_id": aid, "decision": "approved"}
                )
            assert default_off == {"error": "mcp_decide_disabled"}
            assert explicit_off == {"error": "mcp_decide_disabled"}
            assert await _vote_channel(session, aid) == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_decide_needs_explicit_tool_scope(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """F13: a generic write scope (or none naming the tool) never reaches a vote."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session)
            await _enable(session)
            for scopes in (
                None,  # an unrestricted key is no explicit opt-in
                frozenset(),
                frozenset({"mcp:rw"}),
                frozenset({"write"}),
                frozenset({"mcp:rw", "tool:approval_list_mine"}),
            ):
                async with _as(session, AG3, scopes):
                    refused = await _call(
                        "approval_decide", {"approval_id": aid, "decision": "approved"}
                    )
                assert refused == {"error": "tool_not_allowed"}, scopes
            assert await _vote_channel(session, aid) == []
            async with _as(session, AG3, frozenset({"*"})):
                done = await _call("approval_decide", {"approval_id": aid, "decision": "rejected"})
            assert done == {"ok": True, "status": "rejected", "step_status": "rejected"}


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_tool_allowlist(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session)
            await _enable(session)
            only_list = frozenset({"mcp:rw", "tool:approval_list_mine"})
            async with _as(session, AG3, only_list):
                listed = await _call("approval_list_mine", {})
                decide = await _call(
                    "approval_decide", {"approval_id": aid, "decision": "approved"}
                )
                get = await _fail("approval_get", {"approval_id": aid})
            assert len(listed) == 1
            assert decide == {"error": "tool_not_allowed"}
            assert "approval_get" in get and "allowlist" in get
            assert await _vote_channel(session, aid) == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_decide_self_approval_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            # AG3 names the requester AG1 as substitute: AG1 may not vote in AG3's place
            await delegation.create_delegation(
                session,
                user_id=AG3,
                substitute_user_id=AG1,
                valid_from=utc_now() - timedelta(minutes=1),
                valid_until=None,
                created_by=AG3,
            )
            aid = await _request(session, approvers=(AG3,))
            await _enable(session)
            async with _as(session, AG1, RW_DECIDE):
                own = await _call("approval_decide", {"approval_id": aid, "decision": "approved"})
            async with _as(session, AG7, RW_DECIDE):
                stranger = await _call(
                    "approval_decide", {"approval_id": aid, "decision": "approved"}
                )
            async with _as(session, AG3, RW_DECIDE):
                bad = await _fail("approval_decide", {"approval_id": aid, "decision": "maybe"})
            assert own == {"error": "self_approval_forbidden"}
            assert stranger == {"error": "approval_not_found"}
            assert "decision" in bad
            assert await _vote_channel(session, aid) == []
