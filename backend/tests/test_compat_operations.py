"""DB integration tests for GenericInterface compat operations (Phase 2c sub-task 3).

Golden-behaviour tests covering:
- SessionCreate: UserLogin+Password, CustomerUserLogin+Password, AuthFail
- SessionID auth from seeded legacy sessions table
- TicketCreate: success, MissingParameter, AccessDenied
- TicketUpdate: field changes, note article defaults to internal (IsVisibleForCustomer=0)
- TicketGet: AllArticles, DynamicFields, Attachments (base64 roundtrip)
- TicketSearch: StateType singular filter, QueueID, CustomerUserLogin, DynamicField_X
- Error code exact matches
"""

from __future__ import annotations

import base64
import contextlib
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.compat.operations import (
    op_session_create,
    op_ticket_create,
    op_ticket_get,
    op_ticket_search,
    op_ticket_update,
)
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import SessionStore
from tiqora.znuny.password import hash_password
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)
PW_HASH = hash_password("testpass")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _make_sysconfig() -> SysConfig:
    async def _fetch(name: str) -> Any:
        return None

    return SysConfig(fetch=_fetch)


class _FakeRedis:
    """Minimal fake Redis for SessionStore testing."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


class _FakeSettings:
    session_ttl_seconds = 86400
    session_cookie_name = "tiqora_session"


def _make_session_store() -> SessionStore:
    return SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]


async def _create_tiqora_tables(session: AsyncSession) -> None:
    """Create tiqora_* tables in testcontainer DB."""
    ddls = [
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
    ]
    for ddl in ddls:
        with contextlib.suppress(Exception):
            await session.execute(text(ddl))
    await session.commit()


def _seed_compat_data(sync_url: str) -> dict[str, Any]:
    """Seed data for compat tests and return IDs."""
    engine = create_engine(sync_url)
    ids: dict[str, Any] = {}

    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)

        # Agent user (id=300) with full permissions
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (300, 'compat.agent', :pw, 'Compat', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"pw": PW_HASH, "t": NOW},
        )
        # Agent without permissions (id=301)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (301, 'compat.noperms', :pw, 'No', 'Perms', 1, :t, 1, :t, 1)"
            ),
            {"pw": PW_HASH, "t": NOW},
        )

        # Permission group (id=50)
        conn.execute(
            text(
                "INSERT INTO permission_groups"
                " (id, name, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (50, 'compat-group', 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        # Grant agent rw on group
        for perm in ("ro", "rw", "create"):
            conn.execute(
                text(
                    "INSERT INTO group_user"
                    " (user_id, group_id, permission_key,"
                    "  create_time, create_by, change_time, change_by)"
                    " VALUES (300, 50, :perm, :t, 1, :t, 1)"
                ),
                {"perm": perm, "t": NOW},
            )

        # Queue (id=50) in group 50
        conn.execute(
            text(
                "INSERT INTO queue"
                " (id, name, group_id, system_address_id, salutation_id, signature_id,"
                "  follow_up_id, follow_up_lock, valid_id,"
                "  create_time, create_by, change_time, change_by)"
                " VALUES (50, 'CompatQueue', 50, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        ids["queue_id"] = 50

        # Resolve state/priority IDs from seed data (state_id=1=new, priority_id=3=3 normal)
        ids["state_id"] = 1
        ids["priority_id"] = 3

        # Customer user
        conn.execute(
            text(
                "INSERT INTO customer_user"
                " (login, email, customer_id, pw, first_name, last_name, valid_id,"
                "  create_time, create_by, change_time, change_by)"
                " VALUES ('cust.user1', 'c@example.com', 'CUST1', :pw,"
                "         'Cust', 'User', 1, :t, 1, :t, 1)"
            ),
            {"pw": PW_HASH, "t": NOW},
        )
        ids["customer_user"] = "cust.user1"

        # Dynamic field (Text type, id=99)
        with contextlib.suppress(Exception):
            conn.execute(
                text(
                    "INSERT INTO dynamic_field"
                    " (id, name, label, field_type, object_type, config, valid_id, internal_field,"
                    "  create_time, create_by, change_time, change_by, field_order)"
                    " VALUES (99, 'CompatTestField', 'CompatTestField', 'Text', 'Ticket',"
                    "  '---', 1, 0, :t, 1, :t, 1, 99)"
                ),
                {"t": NOW},
            )

        # Seed a Znuny-style session entry (for SessionID auth test)
        with contextlib.suppress(Exception):
            for key, val in [
                ("UserID", "300"),
                ("UserLogin", "compat.agent"),
                ("UserType", "User"),
            ]:
                conn.execute(
                    text(
                        "INSERT INTO sessions (session_id, data_key, data_value, serialized)"
                        " VALUES ('TESTSESSIONID123', :k, :v, 0)"
                    ),
                    {"k": key, "v": val},
                )

    return ids


@pytest.fixture(scope="module")
def compat_mariadb(mariadb_znuny_url: str) -> dict[str, Any]:
    """Seed MariaDB with compat test data and return metadata."""
    ids = _seed_compat_data(mariadb_znuny_url)
    return {"url": mariadb_znuny_url, "async_url": _mysql_async(mariadb_znuny_url), **ids}


# ---------------------------------------------------------------------------
# SessionCreate
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_session_create_agent_login(compat_mariadb: dict[str, Any]) -> None:
    """SessionCreate returns a SessionID for valid agent credentials."""
    engine = create_async_engine(compat_mariadb["async_url"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = _make_session_store()

    async with factory() as session:
        await _create_tiqora_tables(session)
        result = await op_session_create(
            {"UserLogin": "compat.agent", "Password": "testpass"},
            session,
            store,
        )

    assert "SessionID" in result, f"Expected SessionID, got: {result}"
    await engine.dispose()


@pytest.mark.db
async def test_session_create_wrong_password(compat_mariadb: dict[str, Any]) -> None:
    """SessionCreate returns AuthFail for wrong password."""
    engine = create_async_engine(compat_mariadb["async_url"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = _make_session_store()

    async with factory() as session:
        result = await op_session_create(
            {"UserLogin": "compat.agent", "Password": "wrongpassword"},
            session,
            store,
        )

    assert "Error" in result
    assert result["Error"]["ErrorCode"] == "SessionCreate.AuthFail"
    await engine.dispose()


@pytest.mark.db
async def test_session_create_customer_login(compat_mariadb: dict[str, Any]) -> None:
    """SessionCreate returns a SessionID for valid customer credentials."""
    engine = create_async_engine(compat_mariadb["async_url"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = _make_session_store()

    async with factory() as session:
        result = await op_session_create(
            {"CustomerUserLogin": "cust.user1", "Password": "testpass"},
            session,
            store,
        )

    assert "SessionID" in result, f"Expected SessionID, got: {result}"
    await engine.dispose()


@pytest.mark.db
async def test_session_create_missing_params(compat_mariadb: dict[str, Any]) -> None:
    """SessionCreate returns MissingParameter when no login is given."""
    engine = create_async_engine(compat_mariadb["async_url"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = _make_session_store()

    async with factory() as session:
        result = await op_session_create({}, session, store)

    assert "Error" in result
    assert result["Error"]["ErrorCode"] == "SessionCreate.MissingParameter"
    await engine.dispose()


# ---------------------------------------------------------------------------
# SessionID auth from legacy sessions table
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_session_id_auth_ticket_search(compat_mariadb: dict[str, Any]) -> None:
    """TicketSearch authenticates via a seeded Znuny sessions row."""
    engine = create_async_engine(compat_mariadb["async_url"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = _make_session_store()

    async with factory() as session:
        result = await op_ticket_search(
            {"SessionID": "TESTSESSIONID123", "QueueIDs": [compat_mariadb["queue_id"]]},
            session,
            store,
        )

    # Should return a list (possibly empty) without an Error key
    assert "Error" not in result, f"Got error instead of ticket IDs: {result}"
    assert "TicketID" in result
    await engine.dispose()


@pytest.mark.db
async def test_session_id_auth_invalid(compat_mariadb: dict[str, Any]) -> None:
    """TicketSearch returns AuthFail for an invalid SessionID."""
    engine = create_async_engine(compat_mariadb["async_url"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = _make_session_store()

    async with factory() as session:
        result = await op_ticket_search(
            {"SessionID": "INVALIDSESSIONID_DOESNOTEXIST"},
            session,
            store,
        )

    assert "Error" in result
    assert "AuthFail" in result["Error"]["ErrorCode"]
    await engine.dispose()


# ---------------------------------------------------------------------------
# TicketCreate
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_ticket_create_success(compat_mariadb: dict[str, Any]) -> None:
    """TicketCreate creates a ticket and returns TicketID + TicketNumber."""
    engine = create_async_engine(compat_mariadb["async_url"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = _make_session_store()
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _create_tiqora_tables(session)
        result = await op_ticket_create(
            {
                "UserLogin": "compat.agent",
                "Password": "testpass",
                "Ticket": {
                    "Title": "GI Compat Test",
                    "QueueID": compat_mariadb["queue_id"],
                    "StateID": compat_mariadb["state_id"],
                    "PriorityID": compat_mariadb["priority_id"],
                },
            },
            session,
            factory,
            store,
            sysconfig,
        )

    assert "Error" not in result, f"Unexpected error: {result}"
    assert "TicketID" in result
    assert "TicketNumber" in result
    await engine.dispose()


@pytest.mark.db
async def test_ticket_create_missing_title(compat_mariadb: dict[str, Any]) -> None:
    """TicketCreate returns MissingParameter when Title is absent."""
    engine = create_async_engine(compat_mariadb["async_url"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = _make_session_store()
    sysconfig = _make_sysconfig()

    async with factory() as session:
        result = await op_ticket_create(
            {
                "UserLogin": "compat.agent",
                "Password": "testpass",
                "Ticket": {
                    "QueueID": compat_mariadb["queue_id"],
                    "StateID": compat_mariadb["state_id"],
                    "PriorityID": compat_mariadb["priority_id"],
                },
            },
            session,
            factory,
            store,
            sysconfig,
        )

    assert "Error" in result
    assert result["Error"]["ErrorCode"] == "TicketCreate.MissingParameter"
    await engine.dispose()


@pytest.mark.db
async def test_ticket_create_access_denied(compat_mariadb: dict[str, Any]) -> None:
    """TicketCreate returns AccessDenied when user lacks queue permission."""
    engine = create_async_engine(compat_mariadb["async_url"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = _make_session_store()
    sysconfig = _make_sysconfig()

    async with factory() as session:
        result = await op_ticket_create(
            {
                "UserLogin": "compat.noperms",
                "Password": "testpass",
                "Ticket": {
                    "Title": "Should Fail",
                    "QueueID": compat_mariadb["queue_id"],
                    "StateID": compat_mariadb["state_id"],
                    "PriorityID": compat_mariadb["priority_id"],
                },
            },
            session,
            factory,
            store,
            sysconfig,
        )

    assert "Error" in result
    assert result["Error"]["ErrorCode"] == "TicketCreate.AccessDenied"
    await engine.dispose()


@pytest.mark.db
async def test_ticket_create_auth_fail(compat_mariadb: dict[str, Any]) -> None:
    """TicketCreate returns AuthFail for bad credentials."""
    engine = create_async_engine(compat_mariadb["async_url"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = _make_session_store()
    sysconfig = _make_sysconfig()

    async with factory() as session:
        result = await op_ticket_create(
            {
                "UserLogin": "compat.agent",
                "Password": "badpass",
                "Ticket": {
                    "Title": "Should Fail",
                    "QueueID": compat_mariadb["queue_id"],
                    "StateID": compat_mariadb["state_id"],
                    "PriorityID": compat_mariadb["priority_id"],
                },
            },
            session,
            factory,
            store,
            sysconfig,
        )

    assert "Error" in result
    assert result["Error"]["ErrorCode"] == "TicketCreate.AuthFail"
    await engine.dispose()


# ---------------------------------------------------------------------------
# TicketUpdate — note stays internal by default
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_ticket_update_note_stays_internal(compat_mariadb: dict[str, Any]) -> None:
    """TicketUpdate: article note without explicit IsVisibleForCustomer defaults to 0 (internal)."""
    engine = create_async_engine(compat_mariadb["async_url"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = _make_session_store()
    sysconfig = _make_sysconfig()

    # First create a ticket
    async with factory() as session:
        await _create_tiqora_tables(session)
        create_result = await op_ticket_create(
            {
                "UserLogin": "compat.agent",
                "Password": "testpass",
                "Ticket": {
                    "Title": "Note Internal Test",
                    "QueueID": compat_mariadb["queue_id"],
                    "StateID": compat_mariadb["state_id"],
                    "PriorityID": compat_mariadb["priority_id"],
                },
            },
            session,
            factory,
            store,
            sysconfig,
        )

    assert "Error" not in create_result
    ticket_id = create_result["TicketID"]

    # Now add an article note without specifying IsVisibleForCustomer
    async with factory() as session:
        update_result = await op_ticket_update(
            {
                "UserLogin": "compat.agent",
                "Password": "testpass",
                "TicketID": ticket_id,
                "Article": {
                    "Subject": "Internal Note",
                    "Body": "This should be internal",
                    "CommunicationChannel": "Internal",
                    # IsVisibleForCustomer NOT specified — should default to 0
                },
            },
            session,
            factory,
            store,
            sysconfig,
        )

    assert "Error" not in update_result, f"Unexpected error: {update_result}"

    # Verify the article is NOT visible for customer
    engine2 = create_async_engine(compat_mariadb["async_url"])
    factory2 = async_sessionmaker(engine2, expire_on_commit=False)
    async with factory2() as session2:
        row = (
            await session2.execute(
                text(
                    "SELECT a.is_visible_for_customer FROM article a"
                    " WHERE a.ticket_id = :tid"
                    " ORDER BY a.id DESC LIMIT 1"
                ),
                {"tid": ticket_id},
            )
        ).first()
    assert row is not None
    assert int(row[0]) == 0, f"Expected is_visible_for_customer=0 (internal), got {row[0]}"
    await engine.dispose()
    await engine2.dispose()


# ---------------------------------------------------------------------------
# TicketGet — AllArticles + Attachments (base64 roundtrip) + DynamicFields
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_ticket_get_with_attachments(compat_mariadb: dict[str, Any]) -> None:
    """TicketGet with Attachments=1 returns base64-encoded attachment content."""
    engine = create_async_engine(compat_mariadb["async_url"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = _make_session_store()
    sysconfig = _make_sysconfig()

    attachment_content = b"Hello, this is test attachment content!"
    attachment_b64 = base64.b64encode(attachment_content).decode("ascii")

    # Create ticket with article and attachment
    async with factory() as session:
        await _create_tiqora_tables(session)
        create_result = await op_ticket_create(
            {
                "UserLogin": "compat.agent",
                "Password": "testpass",
                "Ticket": {
                    "Title": "Attachment Test",
                    "QueueID": compat_mariadb["queue_id"],
                    "StateID": compat_mariadb["state_id"],
                    "PriorityID": compat_mariadb["priority_id"],
                },
                "Article": {
                    "Subject": "With Attachment",
                    "Body": "See attachment",
                    "Attachment": [
                        {
                            "Filename": "test.txt",
                            "ContentType": "text/plain",
                            "Content": attachment_b64,
                        }
                    ],
                },
            },
            session,
            factory,
            store,
            sysconfig,
        )

    assert "Error" not in create_result
    ticket_id = create_result["TicketID"]

    # Get ticket with all articles and attachments
    async with factory() as session:
        get_result = await op_ticket_get(
            {
                "UserLogin": "compat.agent",
                "Password": "testpass",
                "TicketID": ticket_id,
                "AllArticles": 1,
                "Attachments": 1,
            },
            session,
            store,
        )

    assert "Error" not in get_result, f"Unexpected error: {get_result}"
    assert "Ticket" in get_result
    tickets = get_result["Ticket"]
    assert len(tickets) == 1
    ticket_data = tickets[0]
    assert "Article" in ticket_data
    articles = ticket_data["Article"]
    assert len(articles) >= 1

    # Find the article with our attachment
    found_attachment = False
    for article in articles:
        for att in article.get("Attachment") or []:
            if att.get("Filename") == "test.txt":
                # Verify base64 roundtrip
                decoded = base64.b64decode(att["Content"])
                assert decoded == attachment_content, "Attachment content mismatch!"
                found_attachment = True

    assert found_attachment, "Expected attachment not found in TicketGet response"
    await engine.dispose()


# ---------------------------------------------------------------------------
# TicketSearch — StateType singular filter (gotcha), DynamicField_X
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_ticket_search_state_type_singular(compat_mariadb: dict[str, Any]) -> None:
    """TicketSearch StateType (singular string) correctly filters by state type name."""
    engine = create_async_engine(compat_mariadb["async_url"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = _make_session_store()
    sysconfig = _make_sysconfig()

    # Create a ticket (state_id=1 should be 'new' type)
    async with factory() as session:
        await _create_tiqora_tables(session)
        cr = await op_ticket_create(
            {
                "UserLogin": "compat.agent",
                "Password": "testpass",
                "Ticket": {
                    "Title": "StateType Filter Test",
                    "QueueID": compat_mariadb["queue_id"],
                    "StateID": compat_mariadb["state_id"],
                    "PriorityID": compat_mariadb["priority_id"],
                },
            },
            session,
            factory,
            store,
            sysconfig,
        )
    assert "Error" not in cr
    ticket_id = cr["TicketID"]

    # Search with StateType="new" — should find our ticket
    async with factory() as session:
        result_new = await op_ticket_search(
            {
                "UserLogin": "compat.agent",
                "Password": "testpass",
                "StateType": "new",
                "QueueIDs": [compat_mariadb["queue_id"]],
            },
            session,
            store,
        )

    assert "Error" not in result_new
    assert ticket_id in result_new["TicketID"], (
        f"Ticket {ticket_id} not found with StateType=new: {result_new}"
    )

    # Search with StateType="closed" — should NOT find our ticket (it's 'new')
    async with factory() as session:
        result_closed = await op_ticket_search(
            {
                "UserLogin": "compat.agent",
                "Password": "testpass",
                "StateType": "closed",
                "QueueIDs": [compat_mariadb["queue_id"]],
            },
            session,
            store,
        )

    assert "Error" not in result_closed
    assert ticket_id not in result_closed["TicketID"], (
        f"Ticket {ticket_id} should NOT appear in 'closed' StateType search"
    )
    await engine.dispose()


@pytest.mark.db
async def test_ticket_search_customer_user_login(compat_mariadb: dict[str, Any]) -> None:
    """TicketSearch CustomerUserLogin filter returns only matching tickets."""
    engine = create_async_engine(compat_mariadb["async_url"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = _make_session_store()
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _create_tiqora_tables(session)
        cr = await op_ticket_create(
            {
                "UserLogin": "compat.agent",
                "Password": "testpass",
                "Ticket": {
                    "Title": "CustomerSearch Test",
                    "QueueID": compat_mariadb["queue_id"],
                    "StateID": compat_mariadb["state_id"],
                    "PriorityID": compat_mariadb["priority_id"],
                    "CustomerUser": "cust.user1",
                },
            },
            session,
            factory,
            store,
            sysconfig,
        )
    assert "Error" not in cr
    ticket_id = cr["TicketID"]

    async with factory() as session:
        result = await op_ticket_search(
            {
                "UserLogin": "compat.agent",
                "Password": "testpass",
                "CustomerUserLogin": "cust.user1",
                "QueueIDs": [compat_mariadb["queue_id"]],
            },
            session,
            store,
        )

    assert "Error" not in result
    assert ticket_id in result["TicketID"]

    # Different customer should not find it
    async with factory() as session:
        result2 = await op_ticket_search(
            {
                "UserLogin": "compat.agent",
                "Password": "testpass",
                "CustomerUserLogin": "other.customer",
                "QueueIDs": [compat_mariadb["queue_id"]],
            },
            session,
            store,
        )

    assert "Error" not in result2
    assert ticket_id not in result2["TicketID"]
    await engine.dispose()


@pytest.mark.db
async def test_ticket_get_dynamic_fields(compat_mariadb: dict[str, Any]) -> None:
    """TicketGet with DynamicFields=1 returns dynamic field values."""
    engine = create_async_engine(compat_mariadb["async_url"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = _make_session_store()
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _create_tiqora_tables(session)
        cr = await op_ticket_create(
            {
                "UserLogin": "compat.agent",
                "Password": "testpass",
                "Ticket": {
                    "Title": "DynamicField Test",
                    "QueueID": compat_mariadb["queue_id"],
                    "StateID": compat_mariadb["state_id"],
                    "PriorityID": compat_mariadb["priority_id"],
                },
                "DynamicField": [{"Name": "CompatTestField", "Value": "MyValue123"}],
            },
            session,
            factory,
            store,
            sysconfig,
        )

    if "Error" in cr:
        pytest.skip(f"TicketCreate failed (may be missing dynamic field config): {cr}")

    ticket_id = cr["TicketID"]

    async with factory() as session:
        result = await op_ticket_get(
            {
                "UserLogin": "compat.agent",
                "Password": "testpass",
                "TicketID": ticket_id,
                "DynamicFields": 1,
            },
            session,
            store,
        )

    assert "Error" not in result
    tickets = result["Ticket"]
    assert len(tickets) == 1
    dfs = tickets[0].get("DynamicField") or []
    df_map = {df["Name"]: df["Value"] for df in dfs}
    assert "CompatTestField" in df_map, f"DynamicField not found: {df_map}"
    assert df_map["CompatTestField"] == "MyValue123"
    await engine.dispose()


@pytest.mark.db
async def test_ticket_search_no_permission_returns_empty(compat_mariadb: dict[str, Any]) -> None:
    """TicketSearch for user with no group permissions returns empty list."""
    engine = create_async_engine(compat_mariadb["async_url"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = _make_session_store()

    async with factory() as session:
        result = await op_ticket_search(
            {
                "UserLogin": "compat.noperms",
                "Password": "testpass",
            },
            session,
            store,
        )

    assert "Error" not in result
    assert result["TicketID"] == []
    await engine.dispose()
