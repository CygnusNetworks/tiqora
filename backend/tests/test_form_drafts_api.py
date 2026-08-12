"""DB integration tests for ``/api/v1/tickets/{id}/drafts*``.

Covers the per-article keying added in migration ``20260807_0030``: an agent
can hold one autosaved reply draft per article of a ticket, repeated saves
update in place rather than piling up rows, and discarding one draft leaves
the others alone.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.test_ticket_write_service import _mysql_async, _seed_tiqora_tables

TICKET_ID = 998_100
USER_ID = 1
ACTION = "AgentTicketCompose"
QUEUE_ID = 998_101
GROUP_ID = 998_102
NOW = datetime(2024, 6, 1, 12, 0, 0)


async def _seed_readable_ticket(session: AsyncSession) -> None:
    """A real ticket in a queue USER_ID can read.

    The draft endpoints require `ro` on the ticket (security review L-1), so a
    bare ticket id is no longer enough to exercise the per-article keying.
    """
    from sqlalchemy import text

    await session.execute(
        text(
            "INSERT INTO permission_groups (id, name, valid_id, create_time,"
            " create_by, change_time, change_by)"
            " VALUES (:gid, 'draft-api-group', 1, :t, 1, :t, 1)"
        ),
        {"gid": GROUP_ID, "t": NOW},
    )
    await session.execute(
        text(
            "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
            " create_by, change_time, change_by)"
            " VALUES (:uid, :gid, 'ro', :t, 1, :t, 1), (:uid, :gid, 'rw', :t, 1, :t, 1)"
        ),
        {"uid": USER_ID, "gid": GROUP_ID, "t": NOW},
    )
    await session.execute(
        text(
            "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
            " signature_id, follow_up_id, follow_up_lock, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:qid, 'DraftApiQueue', :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
        ),
        {"qid": QUEUE_ID, "gid": GROUP_ID, "t": NOW},
    )
    await session.execute(
        text(
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
            " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
            " timeout, until_time, escalation_time, escalation_update_time,"
            " escalation_response_time, escalation_solution_time, archive_flag,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:tid, :tn, 'Draft API ticket', :qid, 1, 1, :uid, 1, 3, 4,"
            " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
        ),
        {"tid": TICKET_ID, "tn": str(TICKET_ID), "qid": QUEUE_ID, "uid": USER_ID, "t": NOW},
    )


async def _unseed_readable_ticket(session: AsyncSession) -> None:
    from sqlalchemy import text

    for sql, params in (
        ("DELETE FROM ticket WHERE id = :tid", {"tid": TICKET_ID}),
        ("DELETE FROM queue WHERE id = :qid", {"qid": QUEUE_ID}),
        ("DELETE FROM group_user WHERE group_id = :gid", {"gid": GROUP_ID}),
        ("DELETE FROM permission_groups WHERE id = :gid", {"gid": GROUP_ID}),
    ):
        await session.execute(text(sql), params)


async def _client_for(mariadb_znuny_url: str) -> Any:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        await _seed_tiqora_tables(session)
    async with factory() as session, session.begin():
        from sqlalchemy import text

        await session.execute(
            text("DELETE FROM tiqora_form_draft WHERE ticket_id = :tid"),
            {"tid": TICKET_ID},
        )
        await _unseed_readable_ticket(session)
        await _seed_readable_ticket(session)

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    fake_user = AuthenticatedUser(
        id=USER_ID,
        login="root@localhost",
        first_name="Draft",
        last_name="Er",
        auth_method="session",
    )

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = _override_get_db

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), engine


async def _cleanup(client: Any, base: str) -> None:
    """Leave no rows behind, so each test also passes run on its own."""
    for aid in (11, 22):
        await client.delete(f"{base}/{ACTION}", params={"article_id": aid})
    await client.delete(f"{base}/{ACTION}")


def _put(article_id: int | None, body: str) -> dict[str, Any]:
    return {
        "action": ACTION,
        "article_id": article_id,
        "content": json.dumps({"body": body}),
    }


@pytest.mark.db
@pytest.mark.asyncio
async def test_draft_upsert_is_per_article(mariadb_znuny_url: str) -> None:
    """Two articles keep separate drafts; re-saving one updates it in place."""
    client, engine = await _client_for(mariadb_znuny_url)
    base = f"/api/v1/tickets/{TICKET_ID}/drafts"

    async with client:
        first = await client.put(f"{base}/{ACTION}", json=_put(11, "reply to 11"))
        second = await client.put(f"{base}/{ACTION}", json=_put(22, "reply to 22"))
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json()["article_id"] == 11
        assert first.json()["id"] != second.json()["id"]

        # Same article again: update, not a second row.
        again = await client.put(f"{base}/{ACTION}", json=_put(11, "edited"))
        assert again.json()["id"] == first.json()["id"]
        assert json.loads(again.json()["content"])["body"] == "edited"

        listed = await client.get(base)
        drafts = listed.json()
        assert len(drafts) == 2
        assert sorted(d["article_id"] for d in drafts) == [11, 22]

        await _cleanup(client, base)

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_draft_delete_is_scoped_to_one_article(mariadb_znuny_url: str) -> None:
    """Discarding one reply draft must not take the ticket's others with it."""
    client, engine = await _client_for(mariadb_znuny_url)
    base = f"/api/v1/tickets/{TICKET_ID}/drafts"

    async with client:
        await client.put(f"{base}/{ACTION}", json=_put(11, "keep me"))
        await client.put(f"{base}/{ACTION}", json=_put(22, "delete me"))

        gone = await client.delete(f"{base}/{ACTION}", params={"article_id": 22})
        assert gone.status_code == 204

        remaining = (await client.get(base)).json()
        assert [d["article_id"] for d in remaining] == [11]
        assert json.loads(remaining[0]["content"])["body"] == "keep me"

        await _cleanup(client, base)

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_ticket_wide_draft_coexists_with_article_drafts(
    mariadb_znuny_url: str,
) -> None:
    """``article_id`` omitted addresses the ticket-wide draft, separately."""
    client, engine = await _client_for(mariadb_znuny_url)
    base = f"/api/v1/tickets/{TICKET_ID}/drafts"

    async with client:
        await client.put(f"{base}/{ACTION}", json=_put(11, "on article"))
        wide = await client.put(f"{base}/{ACTION}", json=_put(None, "ticket wide"))
        assert wide.json()["article_id"] is None

        # Re-saving the ticket-wide draft must find it despite the NULL key.
        again = await client.put(f"{base}/{ACTION}", json=_put(None, "still wide"))
        assert again.json()["id"] == wide.json()["id"]
        assert len((await client.get(base)).json()) == 2

        # Deleting without article_id only removes the ticket-wide one.
        await client.delete(f"{base}/{ACTION}")
        remaining = (await client.get(base)).json()
        assert [d["article_id"] for d in remaining] == [11]

        await _cleanup(client, base)

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_drafts_require_ticket_read_access(mariadb_znuny_url: str) -> None:
    """A draft cannot be parked against a ticket the agent cannot read (L-1).

    The endpoints are per-agent, so nothing leaks *out* — but writing to an
    arbitrary ticket id is both unbounded storage and an existence oracle.
    """
    client, engine = await _client_for(mariadb_znuny_url)
    unreadable = TICKET_ID + 500  # never seeded → not readable by anyone
    base = f"/api/v1/tickets/{unreadable}/drafts"

    async with client:
        put = await client.put(f"{base}/{ACTION}", json=_put(11, "should not land"))
        assert put.status_code == 404, put.text

        listed = await client.get(base)
        assert listed.status_code == 404

        deleted = await client.delete(f"{base}/{ACTION}", params={"article_id": 11})
        assert deleted.status_code == 404

    # Nothing was written despite three attempts.
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        from sqlalchemy import text

        count = (
            await session.execute(
                text("SELECT COUNT(*) FROM tiqora_form_draft WHERE ticket_id = :tid"),
                {"tid": unreadable},
            )
        ).scalar_one()
    assert count == 0

    async with factory() as session, session.begin():
        await _unseed_readable_ticket(session)

    await engine.dispose()
