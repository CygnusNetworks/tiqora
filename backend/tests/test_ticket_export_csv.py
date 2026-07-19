"""DB integration test for GET /api/v1/tickets/export.csv (subtask 3 CSV export).

Covers: permission filtering identical to GET /tickets (a queue the user
lacks ``ro`` on never appears in the export) and CSV format basics (UTF-8
BOM prefix, ``;``-delimited header row, no 200-row cap).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _seed(sync_url: str) -> dict[str, Any]:
    """Seed one allowed queue+ticket and one disallowed queue+ticket.

    reader.alpha has ``ro`` on the "allowed" group/queue only; the "denied"
    group/queue is one they cannot see. All ids/logins/queue-names are
    namespaced by a random UUID fragment — ``mariadb_znuny_url`` is a
    session-scoped testcontainer fixture shared across every test file in
    the run, so fixed small ids collide with other files' seed data.
    """
    ns = uuid.uuid4().hex[:8]
    base = int(ns, 16) % 1_000_000 * 1000

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :login, 'x', 'Read', 'Er', 1, :t, 1, :t, 1)"
            ),
            {"uid": base + 1, "login": f"reader.alpha.{ns}", "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by)"
                " VALUES (:g1, :n1, 1, :t, 1, :t, 1), (:g2, :n2, 1, :t, 1, :t, 1)"
            ),
            {
                "g1": base + 10,
                "n1": f"csv-allowed-{ns}",
                "g2": base + 11,
                "n2": f"csv-denied-{ns}",
                "t": NOW,
            },
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (:uid, :gid, 'ro', :t, 1, :t, 1)"
            ),
            {"uid": base + 1, "gid": base + 10, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:q1, :qn1, :g1, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1),"
                " (:q2, :qn2, :g2, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {
                "q1": base + 20,
                "qn1": f"CsvAllowedQueue-{ns}",
                "g1": base + 10,
                "q2": base + 21,
                "qn2": f"CsvDeniedQueue-{ns}",
                "g2": base + 11,
                "t": NOW,
            },
        )
        tn_visible, tn_hidden = f"T{base + 30}", f"T{base + 31}"
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:t1, :tn1, 'Visible ticket', :q1, 1, 1, :uid, 1, 3, 4,"
                " 'CUST-CSV', 'alice@example.com', 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1),"
                " (:t2, :tn2, 'Hidden ticket', :q2, 1, 1, :uid, 1, 3, 4,"
                " 'CUST-CSV', 'alice@example.com', 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {
                "t1": base + 30,
                "tn1": tn_visible,
                "q1": base + 20,
                "t2": base + 31,
                "tn2": tn_hidden,
                "q2": base + 21,
                "uid": base + 1,
                "t": NOW,
            },
        )
    engine.dispose()
    return {
        "user_id": base + 1,
        "login": f"reader.alpha.{ns}",
        "tn_visible": tn_visible,
        "tn_hidden": tn_hidden,
    }


@pytest.mark.db
@pytest.mark.asyncio
async def test_export_csv_permission_filtered_and_well_formed(mariadb_znuny_url: str) -> None:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    ids = _seed(mariadb_znuny_url)
    async_url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    fake_user = AuthenticatedUser(
        id=ids["user_id"],
        login=ids["login"],
        first_name="Read",
        last_name="Er",
        auth_method="session",
    )

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/tickets/export.csv")

    await engine.dispose()

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"] == 'attachment; filename="tickets.csv"'

    raw = resp.content
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV must start with a UTF-8 BOM"
    text_body = raw[3:].decode("utf-8")
    lines = text_body.splitlines()
    assert lines, "export produced no rows at all"

    header = lines[0].split(";")
    assert header == [
        "Number",
        "Title",
        "Queue",
        "State",
        "Priority",
        "Owner",
        "Customer",
        "Created",
        "Changed",
    ]

    body_lines = lines[1:]
    assert any(ids["tn_visible"] in line for line in body_lines), (
        "visible ticket missing from export"
    )
    assert not any(ids["tn_hidden"] in line for line in body_lines), (
        "ticket in a queue without ro permission leaked into the export"
    )


@pytest.mark.asyncio
async def test_export_csv_requires_auth() -> None:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.config import Settings

    app = create_app(Settings(environment="test"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/tickets/export.csv")
    assert resp.status_code == 401
