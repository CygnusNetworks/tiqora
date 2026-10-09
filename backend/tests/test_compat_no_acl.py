"""Compat API pin: Znuny's GenericInterface applies no TicketAcl (plan D1/D11, ruling F33).

Znuny's ``TicketUpdate`` (``Kernel/GenericInterface/Operation/Ticket/TicketUpdate.pm``) calls
``TicketStateSet``/``TicketQueueSet``/... directly and never ``TicketAcl``, so an ACL that forbids a
state for everyone must not block the compat operation. Seed ids live in 884500-884999.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.api.compat.operations import op_ticket_update
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.deployed.store import clear_revision_cache
from tiqora.domain.auth import SessionStore
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

T0 = datetime(2024, 6, 1, 12, 0, 0)
AGENT, GROUP, QUEUE, TICKET, ACL = 884501, 884510, 884520, 884530, 884540
SESSION = "GI884500AGENTSESSION"
_SEEDED: list[str] = []

_CLEANUP = (
    f"DELETE FROM ticket_history WHERE ticket_id = {TICKET}",
    f"DELETE FROM tiqora_event_outbox WHERE ticket_id = {TICKET}",
    f"DELETE FROM tiqora_cache_invalidation WHERE ticket_id = {TICKET}",
    f"DELETE FROM ticket WHERE id = {TICKET}",
    f"DELETE FROM acl WHERE id = {ACL}",
    "DELETE FROM sessions WHERE session_id LIKE 'GI884500%'",
    f"DELETE FROM queue WHERE id = {QUEUE}",
    f"DELETE FROM group_user WHERE group_id = {GROUP}",
    f"DELETE FROM permission_groups WHERE id = {GROUP}",
    f"DELETE FROM users WHERE id = {AGENT}",
)


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in set(_SEEDED):
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            for stmt in _CLEANUP:
                conn.execute(text(stmt))
        engine.dispose()
    _SEEDED.clear()
    clear_revision_cache()


class _FakeRedis:
    async def get(self, key: str) -> str | None:
        return None


class _FakeSettings:
    session_ttl_seconds = 86400
    session_cookie_name = "tiqora_session"


def _seed(sync_url: str) -> None:
    _SEEDED.append(sync_url)
    clear_revision_cache()
    now = int(time.time())
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in _CLEANUP:
            conn.execute(text(stmt))
        conn.execute(text("DELETE FROM tiqora_acl_revision"))
        for stmt in (
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
            " create_by, change_time, change_by)"
            f" VALUES ({AGENT}, 'gi884500.agent', 'x', 'Gi', 'Agent', 1, :t, 1, :t, 1)",
            "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
            f" change_time, change_by) VALUES ({GROUP}, 'gi884500-group', 1, :t, 1, :t, 1)",
            "INSERT INTO group_user (user_id, group_id, permission_key, create_time, create_by,"
            f" change_time, change_by) VALUES ({AGENT}, {GROUP}, 'rw', :t, 1, :t, 1)",
            "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
            " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
            f" change_time, change_by) VALUES ({QUEUE}, 'GI884500Queue', {GROUP}, 1, 1, 1, 1, 0,"
            " 1, :t, 1, :t, 1)",
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
            " responsible_user_id, ticket_priority_id, ticket_state_id, timeout, until_time,"
            " escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, create_time, create_by, change_time,"
            f" change_by) VALUES ({TICKET}, '20240601884530', 'GI884500 ticket', {QUEUE}, 1, 1,"
            " 1, 1, 3, 1, 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)",
            "INSERT INTO acl (id, name, comments, description, valid_id, stop_after_match,"
            " config_match, config_change, create_time, create_by, change_time, change_by)"
            f" VALUES ({ACL}, 'gi884500-no-closed', '', '', 1, 0, '---\n', "
            "'---\nPossibleNot:\n  Ticket:\n    State:\n    - closed successful\n', :t, 1, :t, 1)",
        ):
            conn.execute(text(stmt), {"t": T0})
        for key, val in {
            "UserID": str(AGENT),
            "UserLogin": "gi884500.agent",
            "UserType": "User",
            "UserLastRequest": str(now),
            "UserSessionStart": str(now),
        }.items():
            conn.execute(
                text(
                    "INSERT INTO sessions (session_id, data_key, data_value, serialized)"
                    " VALUES (:sid, :k, :v, 0)"
                ),
                {"sid": SESSION, "k": key, "v": val},
            )
    engine.dispose()


def _query(sync_url: str, sql: str) -> list[Any]:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            return list(conn.execute(text(sql)).fetchall())
    finally:
        engine.dispose()


async def test_compat_ticket_update_ignores_acls(mariadb_znuny_url: str) -> None:
    _seed(mariadb_znuny_url)
    from tiqora.domain.ticket_acl import apply_ticket_acl, load_valid_acls

    engine = create_async_engine(mariadb_znuny_url.replace("mysql+pymysql://", "mysql+aiomysql://"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]

    async def _no_row(name: str) -> None:
        return None

    try:
        async with factory() as session:
            # The ACL is live: the same rule set filters the agent UI's state list.
            assert "gi884500-no-closed" in [r.name for r in await load_valid_acls(session)]
            states = {1: "new", 2: "closed successful"}
            assert await apply_ticket_acl(
                session,
                user_id=AGENT,
                data=states,
                return_type="Ticket",
                return_sub_type="State",
                ticket_id=TICKET,
            ) == {1: "new"}
            result = await op_ticket_update(
                {
                    "SessionID": SESSION,
                    "TicketID": TICKET,
                    "Ticket": {"State": "closed successful"},
                },
                session,
                factory,
                store,
                SysConfig(fetch=_no_row),  # type: ignore[arg-type]
            )
    finally:
        await engine.dispose()
    assert "Error" not in result, result
    assert result["TicketID"] == TICKET
    state = _query(
        mariadb_znuny_url,
        "SELECT s.name FROM ticket t JOIN ticket_state s ON s.id = t.ticket_state_id"
        f" WHERE t.id = {TICKET}",
    )
    assert state == [("closed successful",)]
