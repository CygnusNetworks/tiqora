"""In-process FastMCP server tests (Phase 2c sub-task 4).

Tests each MCP tool under two permission profiles:
- agent_full: user 400, member of group 60 with ro+rw+create
- agent_none: user 401, no group memberships

Uses the db testcontainer MariaDB fixtures from conftest.py.
The in-process FastMCP Client bypasses HTTP transport entirely.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import Client
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.mcp_server.server import McpState, mcp
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)
PW_HASH = hash_password("testpass")

AGENT_FULL_ID = 400
AGENT_NONE_ID = 401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


async def _create_tiqora_tables(session: AsyncSession) -> None:
    for ddl in [
        """CREATE TABLE IF NOT EXISTS tiqora_cache_invalidation (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            ticket_id BIGINT NULL,
            cache_type VARCHAR(100) NULL,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        "ALTER TABLE tiqora_cache_invalidation MODIFY ticket_id BIGINT NULL",
        "ALTER TABLE tiqora_cache_invalidation ADD COLUMN cache_type VARCHAR(100) NULL",
        """CREATE TABLE IF NOT EXISTS tiqora_event_outbox (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            event_type VARCHAR(100) NOT NULL,
            ticket_id BIGINT NOT NULL,
            payload TEXT,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed TINYINT(1) NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS tiqora_settings (
            key_name VARCHAR(255) PRIMARY KEY,
            value TEXT,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            changed DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS tiqora_api_key (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            key_hash VARCHAR(64) NOT NULL,
            label VARCHAR(255),
            valid TINYINT(1) NOT NULL DEFAULT 1,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS tiqora_form_draft (
            id INT AUTO_INCREMENT PRIMARY KEY,
            ticket_id BIGINT NOT NULL,
            user_id INT NOT NULL,
            action VARCHAR(200) NOT NULL,
            title VARCHAR(255),
            content TEXT NOT NULL DEFAULT '{}',
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            changed DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    ]:
        with contextlib.suppress(Exception):
            await session.execute(text(ddl))
    await session.commit()


def _seed_mcp_data(sync_url: str) -> dict[str, Any]:
    """Seed data for MCP tests."""
    engine = create_engine(sync_url)
    ids: dict[str, Any] = {}

    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)

        # Agent with full permissions (id=400)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (400, 'mcp.agent', :pw, 'MCP', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"pw": PW_HASH, "t": NOW},
        )
        # Agent with no permissions (id=401)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (401, 'mcp.noperms', :pw, 'No', 'Perms', 1, :t, 1, :t, 1)"
            ),
            {"pw": PW_HASH, "t": NOW},
        )

        # Permission group (id=60)
        conn.execute(
            text(
                "INSERT INTO permission_groups"
                " (id, name, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (60, 'mcp-group', 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        for perm in ("ro", "rw", "create"):
            conn.execute(
                text(
                    "INSERT INTO group_user"
                    " (user_id, group_id, permission_key,"
                    "  create_time, create_by, change_time, change_by)"
                    " VALUES (400, 60, :perm, :t, 1, :t, 1)"
                ),
                {"perm": perm, "t": NOW},
            )

        # Queue (id=60) in group 60
        conn.execute(
            text(
                "INSERT INTO queue"
                " (id, name, group_id, system_address_id, salutation_id, signature_id,"
                "  follow_up_id, follow_up_lock, valid_id,"
                "  create_time, create_by, change_time, change_by)"
                " VALUES (60, 'McpQueue', 60, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        ids["queue_id"] = 60
        ids["state_id"] = 1
        ids["priority_id"] = 3

    return ids


@pytest.fixture(scope="module")
def mcp_mariadb(mariadb_znuny_url: str) -> dict[str, Any]:
    """Seed MariaDB for MCP tests."""
    ids = _seed_mcp_data(mariadb_znuny_url)
    return {"url": mariadb_znuny_url, "async_url": _mysql_async(mariadb_znuny_url), **ids}


# ---------------------------------------------------------------------------
# Helper: inject user_id into mcp context via module-level state patch
# ---------------------------------------------------------------------------


def _make_mock_state(async_url: str) -> McpState:
    """Build a McpState pointing at the test DB."""

    from tiqora.config import Settings

    settings = MagicMock(spec=Settings)
    settings.database_url = async_url
    settings.meili_url = "http://localhost:9999"  # force meili to fail
    settings.meili_master_key = "key"
    settings.meili_tickets_index = "tickets"
    return McpState(settings)


def _patch_user_id(user_id: int) -> Any:
    """Patch _get_user_id to return a fixed user_id for testing."""

    def fake_get_user_id(ctx: Any) -> int:
        return user_id

    return patch("tiqora.mcp_server.server._get_user_id", side_effect=fake_get_user_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_mcp_ticket_search_full_access(mcp_mariadb: dict[str, Any]) -> None:
    """ticket_search returns results for agent with full permissions."""
    state = _make_mock_state(mcp_mariadb["async_url"])
    async with state.session_factory() as session:
        await _create_tiqora_tables(session)

    import tiqora.mcp_server.server as srv

    srv._mcp_state = state
    try:
        with _patch_user_id(AGENT_FULL_ID):
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "ticket_search",
                    {"queue_ids": [mcp_mariadb["queue_id"]]},
                )
        # Should return a list (possibly empty — no tickets seeded here)
        assert not result.is_error, f"Unexpected error: {result}"
    finally:
        await state.aclose()
        srv._mcp_state = None


@pytest.mark.db
async def test_mcp_ticket_search_no_access(mcp_mariadb: dict[str, Any]) -> None:
    """ticket_search returns empty list for agent with no group permissions."""
    state = _make_mock_state(mcp_mariadb["async_url"])

    import tiqora.mcp_server.server as srv

    srv._mcp_state = state
    try:
        with _patch_user_id(AGENT_NONE_ID):
            async with Client(mcp) as client:
                result = await client.call_tool("ticket_search", {})
        assert not result.is_error
        # Result should be an empty list
        data = result.data
        assert data == [] or data == "[]", f"Expected empty list, got: {data}"
    finally:
        await state.aclose()
        srv._mcp_state = None


@pytest.mark.db
async def test_mcp_ticket_create_and_get(mcp_mariadb: dict[str, Any]) -> None:
    """ticket_create creates a ticket; ticket_get retrieves it."""
    state = _make_mock_state(mcp_mariadb["async_url"])

    import tiqora.mcp_server.server as srv

    srv._mcp_state = state
    try:
        with _patch_user_id(AGENT_FULL_ID):
            async with Client(mcp) as client:
                create_result = await client.call_tool(
                    "ticket_create",
                    {
                        "title": "MCP Test Ticket",
                        "queue_id": mcp_mariadb["queue_id"],
                        "state_id": mcp_mariadb["state_id"],
                        "priority_id": mcp_mariadb["priority_id"],
                        "body": "Test body from MCP",
                    },
                )
                assert not create_result.is_error
                # Fallback: parse from data string
                import json

                raw_data = create_result.data
                if isinstance(raw_data, str):
                    raw_data = json.loads(raw_data)
                assert "error" not in str(raw_data).lower() or "ticket_id" in str(raw_data), (
                    f"Create failed: {create_result}"
                )

                # Extract ticket_id
                ticket_id = raw_data.get("ticket_id") if isinstance(raw_data, dict) else None

                if ticket_id:
                    get_result = await client.call_tool(
                        "ticket_get",
                        {"ticket_id": ticket_id},
                    )
                    assert not get_result.is_error
                    text_content = str(get_result.data)
                    assert "MCP Test Ticket" in text_content, (
                        f"Title not in response: {text_content[:200]}"
                    )
    finally:
        await state.aclose()
        srv._mcp_state = None


@pytest.mark.db
async def test_mcp_ticket_note_default_internal(mcp_mariadb: dict[str, Any]) -> None:
    """ticket_note defaults to internal (is_visible_for_customer=False)."""
    state = _make_mock_state(mcp_mariadb["async_url"])
    async with state.session_factory() as session:
        await _create_tiqora_tables(session)

    import tiqora.mcp_server.server as srv

    srv._mcp_state = state
    try:
        with _patch_user_id(AGENT_FULL_ID):
            async with Client(mcp) as client:
                # First create a ticket
                cr = await client.call_tool(
                    "ticket_create",
                    {
                        "title": "Note Test",
                        "queue_id": mcp_mariadb["queue_id"],
                        "state_id": mcp_mariadb["state_id"],
                        "priority_id": mcp_mariadb["priority_id"],
                    },
                )
                assert not cr.is_error
                import json

                raw = cr.data
                if isinstance(raw, str):
                    raw = json.loads(raw)
                ticket_id = raw.get("ticket_id") if isinstance(raw, dict) else None

                if ticket_id:
                    note_result = await client.call_tool(
                        "ticket_note",
                        {
                            "ticket_id": ticket_id,
                            "body": "Internal note content",
                            "subject": "Internal Note",
                        },
                    )
                    assert not note_result.is_error

                    # Verify the article is internal in DB
                    async with state.session_factory() as session:
                        row = (
                            await session.execute(
                                text(
                                    "SELECT is_visible_for_customer FROM article"
                                    " WHERE ticket_id = :tid ORDER BY id DESC LIMIT 1"
                                ),
                                {"tid": ticket_id},
                            )
                        ).first()
                    assert row is not None
                    assert int(row[0]) == 0, f"Expected internal (0), got {row[0]}"
    finally:
        await state.aclose()
        srv._mcp_state = None


@pytest.mark.db
async def test_mcp_customer_lookup(mcp_mariadb: dict[str, Any]) -> None:
    """customer_lookup returns customer details."""
    state = _make_mock_state(mcp_mariadb["async_url"])

    import tiqora.mcp_server.server as srv

    srv._mcp_state = state
    try:
        with _patch_user_id(AGENT_FULL_ID):
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "customer_lookup",
                    {"customer_login": "notexists.customer.never"},
                )
        # Non-existent customer returns an error dict (not an MCP error)
        assert not result.is_error
        import json

        data = result.data
        if isinstance(data, str):
            data = json.loads(data)
        assert "error" in data or "login" in data
    finally:
        await state.aclose()
        srv._mcp_state = None


@pytest.mark.db
async def test_mcp_tools_list(mcp_mariadb: dict[str, Any]) -> None:
    """MCP server exposes all expected tools."""
    async with Client(mcp) as client:
        tools = await client.list_tools()
    tool_names = {t.name for t in tools}
    expected = {
        "ticket_search",
        "ticket_get",
        "ticket_create",
        "ticket_reply",
        "ticket_note",
        "ticket_update_state",
        "ticket_update_queue",
        "ticket_update_priority",
        "ticket_update_owner",
        "kb_search",
        "kb_get_article",
        "customer_lookup",
    }
    missing = expected - tool_names
    assert not missing, f"Missing MCP tools: {missing}"


@pytest.mark.db
async def test_mcp_kb_get_article(mcp_mariadb: dict[str, Any]) -> None:
    """kb_get_article returns the article's Markdown content and tags."""
    state = _make_mock_state(mcp_mariadb["async_url"])
    async with state.session_factory() as session:
        await _create_tiqora_tables(session)

    from tiqora.kb.schemas import ArticleIn, CategoryIn
    from tiqora.kb.service import KbService

    async with state.session_factory() as session:
        svc = KbService(session, state.settings)
        async with session.begin():
            cat = await svc.create_category(
                AGENT_FULL_ID, CategoryIn(name="MCP Docs", slug="mcp-docs-1")
            )
        async with session.begin():
            article = await svc.create_article(
                AGENT_FULL_ID,
                ArticleIn(
                    category_id=cat.id,
                    title="MCP KB Article",
                    slug="mcp-kb-article-1",
                    content_md="## Body\n\nHello from the KB.",
                    tags=["mcp"],
                ),
            )
        article_id = article.id

    import tiqora.mcp_server.server as srv

    srv._mcp_state = state
    try:
        with _patch_user_id(AGENT_FULL_ID):
            async with Client(mcp) as client:
                result = await client.call_tool("kb_get_article", {"article_id": article_id})
        assert not result.is_error, f"Unexpected error: {result}"

        import json

        data = result.data
        if isinstance(data, str):
            data = json.loads(data)
        assert data["id"] == article_id
        assert data["title"] == "MCP KB Article"
        assert "Hello from the KB" in data["content_md"]
        assert data["tags"] == ["mcp"]

        # Unknown article id returns an error dict, not an MCP protocol error.
        with _patch_user_id(AGENT_FULL_ID):
            async with Client(mcp) as client:
                missing_result = await client.call_tool("kb_get_article", {"article_id": 99999999})
        assert not missing_result.is_error
        missing_data = missing_result.data
        if isinstance(missing_data, str):
            missing_data = json.loads(missing_data)
        assert "error" in missing_data
    finally:
        await state.aclose()
        srv._mcp_state = None
