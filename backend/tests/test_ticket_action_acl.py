"""DB tests: zoom menu ``Action`` ACLs (GET /tickets/{id}/acl-actions) and the owner/responsible
agent pickers (``ReturnSubType`` Owner/Responsible) incl. the bulk form (no ticket context).

Seed ids live in the 886500-886999 band; the module deletes every row it creates.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.deployed.store import clear_revision_cache
from tiqora.domain.ticket_action_acl import ZOOM_ACTIONS

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)

AGENT, OTHER_AGENT = 886501, 886502
GROUP, GROUP_HIDDEN = 886510, 886511
Q_BILLING, Q_SUPPORT, Q_HIDDEN = 886520, 886521, 886522
T_BILLING, T_SUPPORT, T_HIDDEN = 886530, 886531, 886532
ACL_CLOSE, ACL_BULK, ACL_ZOOM_OWNER = 886540, 886541, 886542
BILLING = "Billing-886"

_SEEDED: list[str] = []


def _to_async_url(sync_url: str) -> str:
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _seed_statements() -> tuple[tuple[str, dict[str, Any]], ...]:
    return (
        ("DELETE FROM acl WHERE id BETWEEN 886540 AND 886549", {}),
        ("DELETE FROM ticket WHERE id BETWEEN 886530 AND 886539", {}),
        ("DELETE FROM queue WHERE id BETWEEN 886520 AND 886529", {}),
        ("DELETE FROM group_user WHERE group_id BETWEEN 886510 AND 886519", {}),
        ("DELETE FROM permission_groups WHERE id BETWEEN 886510 AND 886519", {}),
        ("DELETE FROM users WHERE id BETWEEN 886501 AND 886509", {}),
    )


def _wipe(conn: Any) -> None:
    for stmt, params in _seed_statements():
        conn.execute(text(stmt), params)
    conn.execute(text("DELETE FROM tiqora_acl_revision"))


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in set(_SEEDED):
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            _wipe(conn)
        engine.dispose()
    _SEEDED.clear()
    clear_revision_cache()


def _insert_acl(conn: Any, acl_id: int, name: str, match: str, change: str) -> None:
    conn.execute(
        text(
            "INSERT INTO acl (id, name, comments, description, valid_id, stop_after_match,"
            " config_match, config_change, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :n, '', '', 1, 0, :m, :c, :t, 1, :t, 1)"
        ),
        {"id": acl_id, "n": name, "m": match, "c": change, "t": NOW},
    )


def _seed(sync_url: str, acls: tuple[tuple[int, str, str, str], ...] = ()) -> None:
    _SEEDED.append(sync_url)
    clear_revision_cache()
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        _wipe(conn)
        for uid, login, first, last in (
            (AGENT, "agent-886001", "Alma", "Billing"),
            (OTHER_AGENT, "agent-886002", "Bruno", "Support"),
        ):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :l, 'x', :f, :s, 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "l": login, "f": first, "s": last, "t": NOW},
            )
        for gid, name in ((GROUP, "acl886-grp"), (GROUP_HIDDEN, "acl886-hidden")):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": name, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:u, :g, 'ro', :t, 1, :t, 1)"
            ),
            {"u": AGENT, "g": GROUP, "t": NOW},
        )
        for qid, name, gid in (
            (Q_BILLING, BILLING, GROUP),
            (Q_SUPPORT, "Support-886", GROUP),
            (Q_HIDDEN, "Hidden-886", GROUP_HIDDEN),
        ):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": qid, "n": name, "g": gid, "t": NOW},
            )
        for tid, qid in ((T_BILLING, Q_BILLING), (T_SUPPORT, Q_SUPPORT), (T_HIDDEN, Q_HIDDEN)):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'Action ACL ticket', :q, 1, 1, 1, 1, 3, 4,"
                    " NULL, NULL, 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"2024060188{tid}", "q": qid, "t": NOW},
            )
        for acl in acls:
            _insert_acl(conn, *acl)
    engine.dispose()


CLOSE_NOT_BILLING = (
    ACL_CLOSE,
    "acl886-no-close-in-billing",
    f"---\nProperties:\n  Ticket:\n    Queue:\n    - {BILLING}\n",
    "---\nPossibleNot:\n  Action:\n  - AgentTicketClose\n",
)
BULK_ONLY_ALMA = (
    ACL_BULK,
    "acl886-bulk-owner",
    "---\nProperties:\n  Frontend:\n    Action:\n    - AgentTicketBulk\n",
    "---\nPossible:\n  Ticket:\n    Owner:\n    - agent-886001\n",
)
ZOOM_OWNER_NOT_BRUNO = (
    ACL_ZOOM_OWNER,
    "acl886-zoom-owner",
    "---\nProperties:\n  Frontend:\n    Action:\n    - AgentTicketZoom\n",
    "---\nPossibleNot:\n  Ticket:\n    Responsible:\n    - agent-886002\n",
)


@pytest.fixture
async def db(mariadb_znuny_url: str) -> AsyncIterator[tuple[str, async_sessionmaker[AsyncSession]]]:
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield mariadb_znuny_url, factory
    finally:
        await engine.dispose()


def _client(factory: async_sessionmaker[AsyncSession], user_id: int = AGENT) -> Any:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    fake_user = AuthenticatedUser(
        id=user_id,
        login="agent-886001",
        first_name="Alma",
        last_name="Billing",
        auth_method="session",
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = _override_get_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_acl_actions_hide_close_for_queue(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url, (CLOSE_NOT_BILLING,))
    async with _client(factory) as client:
        billing = await client.get(f"/api/v1/tickets/{T_BILLING}/acl-actions")
        support = await client.get(f"/api/v1/tickets/{T_SUPPORT}/acl-actions")
    assert billing.status_code == 200, billing.text
    allowed = billing.json()["allowed"]
    assert "AgentTicketClose" not in allowed
    assert "AgentTicketNote" in allowed
    assert allowed == [a for a in ZOOM_ACTIONS if a != "AgentTicketClose"]
    # The ACL only matches Billing: the other queue keeps the full menu.
    assert support.json()["allowed"] == list(ZOOM_ACTIONS)


@pytest.mark.asyncio
async def test_acl_actions_without_acls_lists_all(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url)
    async with _client(factory) as client:
        resp = await client.get(f"/api/v1/tickets/{T_BILLING}/acl-actions")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"allowed": list(ZOOM_ACTIONS)}


@pytest.mark.asyncio
async def test_acl_actions_needs_ro(db: tuple[str, async_sessionmaker[AsyncSession]]) -> None:
    url, factory = db
    _seed(url, (CLOSE_NOT_BILLING,))
    async with _client(factory) as client:
        foreign = await client.get(f"/api/v1/tickets/{T_HIDDEN}/acl-actions")
        missing = await client.get("/api/v1/tickets/886539/acl-actions")
        plain = await client.get(f"/api/v1/tickets/{T_HIDDEN}")
    assert plain.status_code == 403
    assert foreign.status_code == plain.status_code  # same rule as GET /tickets/{id}
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_bulk_owner_options_filtered(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url, (BULK_ONLY_ALMA,))
    async with _client(factory) as client:
        bulk = await client.get(
            "/api/v1/reference/ticket-field-options",
            params={"fields": "owner", "action": "AgentTicketBulk"},
        )
        zoom = await client.get(
            "/api/v1/reference/ticket-field-options",
            params={"fields": "owner", "action": "AgentTicketZoom"},
        )
    assert bulk.status_code == 200, bulk.text
    # ACL data is {id: login}, the labels are the full names.
    assert bulk.json()["owner"] == {str(AGENT): "Alma Billing"}
    assert bulk.json()["responsible"] == {}
    zoom_owner = zoom.json()["owner"]
    assert zoom_owner[str(AGENT)] == "Alma Billing"
    assert zoom_owner[str(OTHER_AGENT)] == "Bruno Support"  # the ACL needs AgentTicketBulk


@pytest.mark.asyncio
async def test_responsible_options_filtered_for_ticket(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url, (ZOOM_OWNER_NOT_BRUNO,))
    async with _client(factory) as client:
        resp = await client.get(
            f"/api/v1/tickets/{T_SUPPORT}/field-options",
            params={"fields": "owner,responsible", "action": "AgentTicketZoom"},
        )
        posted = await client.post(
            "/api/v1/reference/field-options",
            json={
                "fields": ["responsible", "owner"],
                "context": {"ticket_id": T_SUPPORT, "action": "AgentTicketZoom"},
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["responsible"][str(AGENT)] == "Alma Billing"
    assert str(OTHER_AGENT) not in body["responsible"]
    assert str(OTHER_AGENT) in body["owner"]  # PossibleNot names Responsible only
    assert posted.status_code == 200, posted.text
    options = posted.json()["options"]
    assert str(OTHER_AGENT) not in {o["value"] for o in options["responsible"]}
    assert {"value": str(OTHER_AGENT), "label": "Bruno Support"} in options["owner"]
