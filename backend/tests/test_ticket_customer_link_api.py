"""DB integration tests for ``GET /api/v1/tickets/{ticket_id}/customer-link``.

Uses a dedicated id block (87200+) so it coexists with the other
session-scoped MariaDB fixture consumers (see
``test_agent_ticket_zoom_apis.py`` for the id-block convention this
mirrors, and ``test_customer_link_resolver.py`` / ``test_customer_links_admin.py``
for the neighboring 87000-87199 blocks used by this feature's other tests).

Root (id=1) already has ``admin``-group ``rw`` from the Znuny seed (see
``test_auth_me_is_admin.py``), so it is reused here as the admin agent
rather than seeding a synthetic admin-group membership.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)

_UID_AGENT = 87201
_UID_ADMIN = 1  # root — already admin-group rw via the Znuny seed
_UID_OUTSIDER = 87203
_GROUP_QUEUE = 87230
_QUEUE_ID = 87200
_TICKET_ID = 87200
_LOGIN_AGENT = "custlink.api.agent"
_LOGIN_ADMIN = "root@localhost"
_LOGIN_OUTSIDER = "custlink.api.outsider"
_CUSTOMER_LOGIN = "custlink.api.customer"


def _mysql_async(sync_url: str) -> str:
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(
            text("DELETE FROM tiqora_queue_customer_link WHERE queue_id = :q"), {"q": _QUEUE_ID}
        )
        conn.execute(text("DELETE FROM ticket WHERE id = :t"), {"t": _TICKET_ID})
        conn.execute(text("DELETE FROM queue WHERE id = :q"), {"q": _QUEUE_ID})
        # Scope strictly to our synthetic queue-group + agent user — never
        # touch root's (user_id=1) grants in OTHER groups (its admin-group
        # rw must survive for other tests in this session-scoped DB).
        conn.execute(
            text("DELETE FROM group_user WHERE user_id = :u1 OR group_id = :gq"),
            {"u1": _UID_AGENT, "gq": _GROUP_QUEUE},
        )
        conn.execute(text("DELETE FROM permission_groups WHERE id = :g"), {"g": _GROUP_QUEUE})
        conn.execute(text("DELETE FROM customer_user WHERE login = :l"), {"l": _CUSTOMER_LOGIN})
        conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": _UID_AGENT})

        conn.execute(
            text(
                """
                INSERT INTO users (id, login, pw, first_name, last_name, valid_id,
                                  create_time, create_by, change_time, change_by)
                VALUES (:id, :login, 'x', 'CustLink', 'Agent', 1, :t, 1, :t, 1)
                """
            ),
            {"id": _UID_AGENT, "login": _LOGIN_AGENT, "t": NOW},
        )

        conn.execute(
            text(
                """
                INSERT INTO permission_groups (id, name, valid_id,
                    create_time, create_by, change_time, change_by)
                VALUES (:id, :name, 1, :t, 1, :t, 1)
                """
            ),
            {"id": _GROUP_QUEUE, "name": f"custlink-queue-{_GROUP_QUEUE}", "t": NOW},
        )

        # Both the plain agent and root (admin) need rw on the ticket's
        # queue so the ticket-read permission check (unrelated to
        # is_admin/visibility) passes for both.
        for uid in (_UID_AGENT, _UID_ADMIN):
            conn.execute(
                text(
                    """
                    INSERT INTO group_user (user_id, group_id, permission_key,
                        create_time, create_by, change_time, change_by)
                    VALUES (:uid, :gid, 'rw', :t, 1, :t, 1)
                    """
                ),
                {"uid": uid, "gid": _GROUP_QUEUE, "t": NOW},
            )

        conn.execute(
            text(
                """
                INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,
                    signature_id, follow_up_id, follow_up_lock, valid_id,
                    create_time, create_by, change_time, change_by)
                VALUES (:id, :name, :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)
                """
            ),
            {"id": _QUEUE_ID, "name": f"CustLinkApiQ-{_QUEUE_ID}", "gid": _GROUP_QUEUE, "t": NOW},
        )

        conn.execute(
            text(
                """
                INSERT INTO customer_user (login, email, customer_id, pw,
                    first_name, last_name, valid_id, create_time, create_by,
                    change_time, change_by)
                VALUES (:login, 'kunde@example.com', 'CUSTAPI', 'x', 'Kunde', 'Api', 1,
                    :t, 1, :t, 1)
                """
            ),
            {"login": _CUSTOMER_LOGIN, "t": NOW},
        )

        conn.execute(
            text(
                """
                INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,
                    user_id, responsible_user_id, ticket_priority_id, ticket_state_id,
                    customer_id, customer_user_id, timeout, until_time, escalation_time,
                    escalation_update_time, escalation_response_time,
                    escalation_solution_time, archive_flag,
                    create_time, create_by, change_time, change_by)
                VALUES (:id, :tn, 'CustLink API ticket', :qid, 1, 1,
                    :uid, 1, 3, 4, 'CUSTAPI', :cul,
                    0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)
                """
            ),
            {
                "id": _TICKET_ID,
                "tn": "20240601872001",
                "qid": _QUEUE_ID,
                "uid": _UID_AGENT,
                "cul": f"{_CUSTOMER_LOGIN}#9",
                "t": NOW,
            },
        )
    engine.dispose()


def _teardown(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM tiqora_queue_customer_link WHERE queue_id = :q"), {"q": _QUEUE_ID}
        )
        conn.execute(text("DELETE FROM ticket WHERE id = :t"), {"t": _TICKET_ID})
        conn.execute(text("DELETE FROM queue WHERE id = :q"), {"q": _QUEUE_ID})
        # Scope strictly to our synthetic queue-group + agent user — never
        # touch root's (user_id=1) grants in OTHER groups (its admin-group
        # rw must survive for other tests in this session-scoped DB).
        conn.execute(
            text("DELETE FROM group_user WHERE user_id = :u1 OR group_id = :gq"),
            {"u1": _UID_AGENT, "gq": _GROUP_QUEUE},
        )
        conn.execute(text("DELETE FROM permission_groups WHERE id = :g"), {"g": _GROUP_QUEUE})
        conn.execute(text("DELETE FROM customer_user WHERE login = :l"), {"l": _CUSTOMER_LOGIN})
        conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": _UID_AGENT})
    engine.dispose()


async def _client_for(mariadb_znuny_url: str, user_id: int, login: str) -> tuple[Any, Any]:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    fake_user = AuthenticatedUser(
        id=user_id, login=login, first_name="CustLink", last_name="Agent", auth_method="session"
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), engine


def _set_link(
    sync_url: str,
    *,
    url_template: str,
    admin_url_template: str | None,
    visibility: str,
    label: str | None = "Kundendaten",
) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM tiqora_queue_customer_link WHERE queue_id = :q"), {"q": _QUEUE_ID}
        )
        conn.execute(
            text(
                """
                INSERT INTO tiqora_queue_customer_link
                    (queue_id, url_template, admin_url_template, label, visibility,
                     create_by, create_time, change_by, change_time)
                VALUES (:qid, :url, :admin_url, :label, :vis, 1, :t, 1, :t)
                """
            ),
            {
                "qid": _QUEUE_ID,
                "url": url_template,
                "admin_url": admin_url_template,
                "label": label,
                "vis": visibility,
                "t": NOW,
            },
        )
    engine.dispose()


@pytest.mark.asyncio
async def test_customer_link_null_when_no_config(mariadb_znuny_url: str) -> None:
    _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, _UID_AGENT, _LOGIN_AGENT)
    async with client:
        resp = await client.get(f"/api/v1/tickets/{_TICKET_ID}/customer-link")
        assert resp.status_code == 200
        assert resp.json() == {"label": None, "url": None}
    await engine.dispose()
    _teardown(mariadb_znuny_url)


@pytest.mark.asyncio
async def test_customer_link_resolved_for_non_admin(mariadb_znuny_url: str) -> None:
    _seed(mariadb_znuny_url)
    _set_link(
        mariadb_znuny_url,
        url_template="https://netadmin.example/?u={customer_user}&tn={ticket_number}",
        admin_url_template="https://netadmin-krb.example/?u={customer_user}",
        visibility="all",
    )
    client, engine = await _client_for(mariadb_znuny_url, _UID_AGENT, _LOGIN_AGENT)
    async with client:
        resp = await client.get(f"/api/v1/tickets/{_TICKET_ID}/customer-link")
        assert resp.status_code == 200
        body = resp.json()
        assert body["label"] == "Kundendaten"
        assert body["url"] == (f"https://netadmin.example/?u={_CUSTOMER_LOGIN}&tn=20240601872001")
    await engine.dispose()
    _teardown(mariadb_znuny_url)


@pytest.mark.asyncio
async def test_customer_link_admin_sees_admin_template(mariadb_znuny_url: str) -> None:
    _seed(mariadb_znuny_url)
    _set_link(
        mariadb_znuny_url,
        url_template="https://netadmin.example/?u={customer_user}",
        admin_url_template="https://netadmin-krb.example/?u={customer_user}",
        visibility="all",
    )
    client, engine = await _client_for(mariadb_znuny_url, _UID_ADMIN, _LOGIN_ADMIN)
    async with client:
        resp = await client.get(f"/api/v1/tickets/{_TICKET_ID}/customer-link")
        assert resp.status_code == 200
        body = resp.json()
        assert body["url"] == f"https://netadmin-krb.example/?u={_CUSTOMER_LOGIN}"
    await engine.dispose()
    _teardown(mariadb_znuny_url)


@pytest.mark.asyncio
async def test_customer_link_admins_only_visibility_hides_from_agent(
    mariadb_znuny_url: str,
) -> None:
    _seed(mariadb_znuny_url)
    _set_link(
        mariadb_znuny_url,
        url_template="https://netadmin.example/?u={customer_user}",
        admin_url_template=None,
        visibility="admins",
    )
    agent_client, agent_engine = await _client_for(mariadb_znuny_url, _UID_AGENT, _LOGIN_AGENT)
    async with agent_client:
        resp = await agent_client.get(f"/api/v1/tickets/{_TICKET_ID}/customer-link")
        assert resp.status_code == 200
        assert resp.json() == {"label": None, "url": None}
    await agent_engine.dispose()

    admin_client, admin_engine = await _client_for(mariadb_znuny_url, _UID_ADMIN, _LOGIN_ADMIN)
    async with admin_client:
        resp = await admin_client.get(f"/api/v1/tickets/{_TICKET_ID}/customer-link")
        assert resp.status_code == 200
        assert resp.json()["url"] == f"https://netadmin.example/?u={_CUSTOMER_LOGIN}"
    await admin_engine.dispose()
    _teardown(mariadb_znuny_url)


@pytest.mark.asyncio
async def test_customer_link_permission_denied_for_unrelated_ticket(mariadb_znuny_url: str) -> None:
    """A user without access to the ticket's queue must not see the link
    (mirrors the ticket read-permission check every other ticket-scoped
    route already enforces via ``TicketService.get_ticket``)."""
    _seed(mariadb_znuny_url)
    engine = create_engine(mariadb_znuny_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": _UID_OUTSIDER})
        conn.execute(
            text(
                """
                INSERT INTO users (id, login, pw, first_name, last_name, valid_id,
                                  create_time, create_by, change_time, change_by)
                VALUES (:id, :login, 'x', 'CustLink', 'Outsider', 1, :t, 1, :t, 1)
                """
            ),
            {"id": _UID_OUTSIDER, "login": _LOGIN_OUTSIDER, "t": NOW},
        )
    engine.dispose()

    client, client_engine = await _client_for(mariadb_znuny_url, _UID_OUTSIDER, _LOGIN_OUTSIDER)
    async with client:
        resp = await client.get(f"/api/v1/tickets/{_TICKET_ID}/customer-link")
        assert resp.status_code in (403, 404)
    await client_engine.dispose()

    engine = create_engine(mariadb_znuny_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": _UID_OUTSIDER})
    engine.dispose()
    _teardown(mariadb_znuny_url)
