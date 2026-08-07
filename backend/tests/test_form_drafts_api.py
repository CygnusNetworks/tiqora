"""DB integration tests for ``/api/v1/tickets/{id}/drafts*``.

Covers the per-article keying added in migration ``20260807_0030``: an agent
can hold one autosaved reply draft per article of a ticket, repeated saves
update in place rather than piling up rows, and discarding one draft leaves
the others alone.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.test_ticket_write_service import _mysql_async, _seed_tiqora_tables

TICKET_ID = 998_100
USER_ID = 1
ACTION = "AgentTicketCompose"


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
