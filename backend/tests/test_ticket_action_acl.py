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
AGENT_RO, AGENT_ROLE, AGENT_SECOND, AGENT_RW2 = 886503, 886504, 886505, 886506
ROLE = 886550
GROUP, GROUP_HIDDEN, GROUP_SECOND = 886510, 886511, 886512
Q_BILLING, Q_SUPPORT, Q_HIDDEN, Q_SECOND = 886520, 886521, 886522, 886523
T_BILLING, T_SUPPORT, T_HIDDEN, T_SECOND = 886530, 886531, 886532, 886533
SYSCONFIG_BASE = 886560  # sysconfig_default/_modified ids 886560-886569
ACL_CLOSE, ACL_BULK, ACL_ZOOM_OWNER = 886540, 886541, 886542
ACL_NO_ZOOM, ACL_WHITELIST = 886543, 886544
BILLING = "Billing-886"

_SEEDED: list[str] = []


def _to_async_url(sync_url: str) -> str:
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _seed_statements() -> tuple[tuple[str, dict[str, Any]], ...]:
    return (
        ("DELETE FROM acl WHERE id BETWEEN 886540 AND 886549", {}),
        ("DELETE FROM ticket WHERE id BETWEEN 886530 AND 886539", {}),
        ("DELETE FROM queue WHERE id BETWEEN 886520 AND 886529", {}),
        ("DELETE FROM group_role WHERE role_id BETWEEN 886550 AND 886559", {}),
        ("DELETE FROM role_user WHERE role_id BETWEEN 886550 AND 886559", {}),
        ("DELETE FROM roles WHERE id BETWEEN 886550 AND 886559", {}),
        ("DELETE FROM sysconfig_modified WHERE id BETWEEN 886560 AND 886569", {}),
        ("DELETE FROM sysconfig_default WHERE id BETWEEN 886560 AND 886569", {}),
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


def _set_sysconfig(conn: Any, settings: dict[str, Any]) -> None:
    """Effective SysConfig values (Znuny's sysconfig_default + sysconfig_modified rows)."""
    from tiqora.znuny.sysconfig import yaml_encode_effective

    for i, (name, value) in enumerate(settings.items()):
        enc = yaml_encode_effective(value)
        conn.execute(
            text(
                "INSERT INTO sysconfig_default (id, name, description, navigation,"
                " is_invisible, is_readonly, is_required, is_valid, has_configlevel,"
                " user_modification_possible, user_modification_active, xml_content_raw,"
                " xml_content_parsed, xml_filename, effective_value, is_dirty,"
                " exclusive_lock_guid, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :n, 'x', 'x', 0, 0, 1, 1, 0, 0, 0, 'x', 'x', 'Ticket.xml',"
                " :e, 0, '0', current_timestamp, 1, current_timestamp, 1)"
            ),
            {"id": SYSCONFIG_BASE + i, "n": name, "e": enc},
        )
        conn.execute(
            text(
                "INSERT INTO sysconfig_modified (id, sysconfig_default_id, name, user_id,"
                " is_valid, user_modification_active, effective_value, is_dirty,"
                " reset_to_default, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :did, :n, NULL, 1, 0, :e, 0, 0, current_timestamp, 1,"
                " current_timestamp, 1)"
            ),
            {"id": SYSCONFIG_BASE + i, "did": SYSCONFIG_BASE + i, "n": name, "e": enc},
        )


def _seed(
    sync_url: str,
    acls: tuple[tuple[int, str, str, str], ...] = (),
    sysconfig: dict[str, Any] | None = None,
) -> None:
    _SEEDED.append(sync_url)
    clear_revision_cache()
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        _wipe(conn)
        for uid, login, first, last in (
            (AGENT, "agent-886001", "Alma", "Billing"),
            (OTHER_AGENT, "agent-886002", "Bruno", "Support"),
            (AGENT_RO, "agent-886003", "Cora", "Readonly"),
            (AGENT_ROLE, "agent-886004", "Dora", "Role"),
            (AGENT_SECOND, "agent-886005", "Emil", "Second"),
            (AGENT_RW2, "agent-886006", "Aaron", "Rw"),  # login sorts last, label first
        ):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :l, 'x', :f, :s, 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "l": login, "f": first, "s": last, "t": NOW},
            )
        for gid, name in (
            (GROUP, "acl886-grp"),
            (GROUP_HIDDEN, "acl886-hidden"),
            (GROUP_SECOND, "acl886-second"),
        ):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": name, "t": NOW},
            )
        # Alma: ro+owner; Bruno and Aaron: rw; Cora: ro only; Dora: owner via a role;
        # Emil: owner on the second group; Alma has ro on the second group, too.
        for uid, gid, key in (
            (AGENT, GROUP, "ro"),
            (AGENT, GROUP, "owner"),
            (AGENT, GROUP_SECOND, "ro"),
            (AGENT, GROUP_SECOND, "owner"),
            (OTHER_AGENT, GROUP, "rw"),
            (OTHER_AGENT, GROUP_SECOND, "rw"),
            (AGENT_RW2, GROUP, "rw"),
            (AGENT_RO, GROUP, "ro"),
            (AGENT_SECOND, GROUP_SECOND, "owner"),
        ):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:u, :g, :k, :t, 1, :t, 1)"
                ),
                {"u": uid, "g": gid, "k": key, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO roles (id, name, valid_id, create_time, create_by, change_time,"
                " change_by) VALUES (:r, 'acl886-role', 1, :t, 1, :t, 1)"
            ),
            {"r": ROLE, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO role_user (user_id, role_id, create_time, create_by, change_time,"
                " change_by) VALUES (:u, :r, :t, 1, :t, 1)"
            ),
            {"u": AGENT_ROLE, "r": ROLE, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO group_role (role_id, group_id, permission_key, permission_value,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:r, :g, 'owner', 1, :t, 1, :t, 1)"
            ),
            {"r": ROLE, "g": GROUP, "t": NOW},
        )
        for qid, name, gid in (
            (Q_BILLING, BILLING, GROUP),
            (Q_SUPPORT, "Support-886", GROUP),
            (Q_HIDDEN, "Hidden-886", GROUP_HIDDEN),
            (Q_SECOND, "Second-886", GROUP_SECOND),
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
        for tid, qid in (
            (T_BILLING, Q_BILLING),
            (T_SUPPORT, Q_SUPPORT),
            (T_HIDDEN, Q_HIDDEN),
            (T_SECOND, Q_SECOND),
        ):
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
        if sysconfig:
            _set_sysconfig(conn, sysconfig)
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


NO_ZOOM_IN_BILLING = (
    ACL_NO_ZOOM,
    "acl886-no-zoom-in-billing",
    f"---\nProperties:\n  Ticket:\n    Queue:\n    - {BILLING}\n",
    "---\nPossibleNot:\n  Action:\n  - AgentTicketZoom\n",
)
WHITELIST_NOTE_ZOOM = (
    ACL_WHITELIST,
    "acl886-whitelist",
    f"---\nProperties:\n  Ticket:\n    Queue:\n    - {BILLING}\n",
    "---\nPossible:\n  Action:\n  - AgentTicketZoom\n  - AgentTicketNote\n",
)
WHITELIST_WITHOUT_ZOOM = (
    ACL_WHITELIST,
    "acl886-whitelist-no-zoom",
    f"---\nProperties:\n  Ticket:\n    Queue:\n    - {BILLING}\n",
    "---\nPossible:\n  Action:\n  - AgentTicketNote\n",
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
    # responsible = rw on the group (Bruno, Aaron); the ACL then removes Bruno.
    assert body["responsible"] == {str(AGENT_RW2): "Aaron Rw"}
    assert str(OTHER_AGENT) in body["owner"]  # PossibleNot names Responsible only
    assert posted.status_code == 200, posted.text
    options = posted.json()["options"]
    assert options["responsible"] == [{"value": str(AGENT_RW2), "label": "Aaron Rw"}]
    assert {"value": str(OTHER_AGENT), "label": "Bruno Support"} in options["owner"]


@pytest.mark.asyncio
async def test_owner_responsible_follow_queue_group_permissions(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url)
    async with _client(factory) as client:
        resp = await client.get(
            f"/api/v1/tickets/{T_BILLING}/field-options",
            params={"fields": "owner,responsible", "action": "AgentTicketOwner"},
        )
        posted = await client.post(
            "/api/v1/reference/field-options",
            json={
                "fields": ["owner", "responsible"],
                "context": {"ticket_id": T_BILLING, "action": "AgentTicketOwner"},
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # owner = `owner` or `rw` on the queue's group, roles included; Cora (ro only) and
    # Emil (owner on another group) are not offered.
    assert body["owner"] == {
        str(AGENT_RW2): "Aaron Rw",
        str(AGENT): "Alma Billing",
        str(OTHER_AGENT): "Bruno Support",
        str(AGENT_ROLE): "Dora Role",
    }
    # `responsible` is not in System::Permission by default -> `rw` is required.
    assert body["responsible"] == {str(AGENT_RW2): "Aaron Rw", str(OTHER_AGENT): "Bruno Support"}
    # Option lists are sorted by full name (Aaron's login sorts last), not by login/id.
    labels = [o["label"] for o in posted.json()["options"]["owner"]]
    assert labels == ["Aaron Rw", "Alma Billing", "Bruno Support", "Dora Role"]


@pytest.mark.asyncio
async def test_responsible_uses_responsible_key_when_configured(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    permissions = ["ro", "move_into", "create", "note", "owner", "priority", "responsible", "rw"]
    _seed(url, sysconfig={"System::Permission": permissions})
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (:u, :g, 'responsible', :t, 1, :t, 1)"
            ),
            {"u": AGENT_RO, "g": GROUP, "t": NOW},
        )
    engine.dispose()
    async with _client(factory) as client:
        resp = await client.get(
            f"/api/v1/tickets/{T_BILLING}/field-options",
            params={"fields": "responsible", "action": "AgentTicketResponsible"},
        )
    assert resp.status_code == 200, resp.text
    # the `responsible` key now counts (Cora), and rw still implies it
    assert resp.json()["responsible"] == {
        str(AGENT_RW2): "Aaron Rw",
        str(OTHER_AGENT): "Bruno Support",
        str(AGENT_RO): "Cora Readonly",
    }


@pytest.mark.asyncio
async def test_change_owner_to_everyone_offers_all_valid_agents(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url, sysconfig={"Ticket::ChangeOwnerToEveryone": 1})
    async with _client(factory) as client:
        resp = await client.get(
            f"/api/v1/tickets/{T_BILLING}/field-options",
            params={"fields": "owner,responsible", "action": "AgentTicketOwner"},
        )
    assert resp.status_code == 200, resp.text
    everyone = {AGENT, OTHER_AGENT, AGENT_RO, AGENT_ROLE, AGENT_SECOND, AGENT_RW2}
    for field in ("owner", "responsible"):
        assert {int(i) for i in resp.json()[field]} >= everyone


@pytest.mark.asyncio
async def test_new_ticket_form_scopes_pickers_by_selected_queue(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url)
    async with _client(factory) as client:
        scoped = await client.get(
            "/api/v1/reference/ticket-field-options",
            params={"fields": "owner", "action": "AgentTicketPhone", "queue_id": Q_SECOND},
        )
        unscoped = await client.get(
            "/api/v1/reference/ticket-field-options",
            params={"fields": "owner", "action": "AgentTicketPhone"},
        )
    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["owner"] == {
        str(AGENT): "Alma Billing",
        str(OTHER_AGENT): "Bruno Support",
        str(AGENT_SECOND): "Emil Second",
    }
    # no queue chosen yet: nothing to scope by
    assert {AGENT_RO, AGENT_SECOND} <= {int(i) for i in unscoped.json()["owner"]}


@pytest.mark.asyncio
async def test_bulk_owner_choices_intersect_selected_ticket_queues(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url)

    async def bulk(client: Any, tickets: list[int]) -> Any:
        return await client.post(
            "/api/v1/reference/field-options",
            json={
                "fields": ["owner", "responsible"],
                "context": {"action": "AgentTicketBulk", "ticket_ids": tickets},
            },
        )

    async with _client(factory) as client:
        both = await bulk(client, [T_BILLING, T_SECOND])
        single = await bulk(client, [T_SECOND])
        foreign = await bulk(client, [T_BILLING, T_HIDDEN])
        missing = await bulk(client, [T_BILLING, 886539])
    assert both.status_code == 200, both.text
    options = both.json()["options"]
    # Billing group owners {Aaron, Alma, Bruno, Dora} / second group {Alma, Bruno, Emil}
    assert [o["label"] for o in options["owner"]] == ["Alma Billing", "Bruno Support"]
    assert [o["label"] for o in options["responsible"]] == ["Bruno Support"]
    assert [o["label"] for o in single.json()["options"]["owner"]] == [
        "Alma Billing",
        "Bruno Support",
        "Emil Second",
    ]
    assert foreign.status_code == 403  # no ro on the hidden ticket's queue
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_zoom_refused_when_acl_removes_agent_ticket_zoom(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url, (NO_ZOOM_IN_BILLING,))
    async with _client(factory) as client:
        refused = await client.get(f"/api/v1/tickets/{T_BILLING}")
        other = await client.get(f"/api/v1/tickets/{T_SUPPORT}")
        menu = await client.get(f"/api/v1/tickets/{T_BILLING}/acl-actions")
    assert refused.status_code == 403
    assert "AgentTicketZoom" in refused.json()["detail"]
    assert other.status_code == 200, other.text  # the ACL only matches Billing
    assert other.json()["id"] == T_SUPPORT
    assert menu.json()["allowed"] == list(ZOOM_ACTIONS)  # Zoom is not a menu entry


@pytest.mark.asyncio
async def test_possible_action_whitelist_limits_menu_and_zoom(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url, (WHITELIST_NOTE_ZOOM,))
    async with _client(factory) as client:
        menu = await client.get(f"/api/v1/tickets/{T_BILLING}/acl-actions")
        zoom = await client.get(f"/api/v1/tickets/{T_BILLING}")
        untouched = await client.get(f"/api/v1/tickets/{T_SUPPORT}/acl-actions")
    assert menu.json()["allowed"] == ["AgentTicketNote"]
    assert zoom.status_code == 200, zoom.text  # AgentTicketZoom is whitelisted
    assert untouched.json()["allowed"] == list(ZOOM_ACTIONS)
    _seed(url, (WHITELIST_WITHOUT_ZOOM,))
    async with _client(factory) as client:
        locked = await client.get(f"/api/v1/tickets/{T_BILLING}")
    assert locked.status_code == 403  # a whitelist without AgentTicketZoom refuses the zoom
