"""DB integration tests for ``/api/v1/calendar/*`` REST endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from tests.test_calendar_service import _mysql_async, _seed

NOW = datetime(2026, 1, 1, 12, 0, 0)


async def _client_for(mariadb_znuny_url: str, ids: dict[str, Any], *, agent_id: int) -> Any:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    async_url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    fake_user = AuthenticatedUser(
        id=agent_id,
        login=ids["agent_login"],
        first_name="Cal",
        last_name="Agent",
        auth_method="session",
    )

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), engine


@pytest.mark.db
@pytest.mark.asyncio
async def test_calendar_appointment_crud_via_rest(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids, agent_id=ids["agent_id"])

    async with client:
        resp = await client.get("/api/v1/calendar/calendars")
        assert resp.status_code == 200
        cal_ids = {c["id"] for c in resp.json()}
        assert ids["allowed_calendar_id"] in cal_ids
        assert ids["denied_calendar_id"] not in cal_ids

        resp = await client.post(
            "/api/v1/calendar/appointments",
            json={
                "calendar_id": ids["allowed_calendar_id"],
                "title": "Quarterly review",
                "start_time": NOW.isoformat(),
                "end_time": (NOW + timedelta(hours=1)).isoformat(),
            },
        )
        assert resp.status_code == 201, resp.text
        appt = resp.json()
        appt_id = appt["id"]
        assert appt["title"] == "Quarterly review"

        resp = await client.get(f"/api/v1/calendar/appointments/{appt_id}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Quarterly review"

        resp = await client.patch(
            f"/api/v1/calendar/appointments/{appt_id}", json={"title": "Quarterly review (v2)"}
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Quarterly review (v2)"

        resp = await client.get(
            "/api/v1/calendar/appointments",
            params={
                "start": NOW.isoformat(),
                "end": (NOW + timedelta(days=1)).isoformat(),
            },
        )
        assert resp.status_code == 200
        occs = resp.json()
        assert any(o["appointment_id"] == appt_id for o in occs)

        resp = await client.post(
            f"/api/v1/calendar/appointments/{appt_id}/tickets/{ids['ticket_id']}"
        )
        assert resp.status_code == 201

        resp = await client.get(f"/api/v1/calendar/appointments/{appt_id}/tickets")
        assert resp.status_code == 200
        assert [t["ticket_id"] for t in resp.json()] == [ids["ticket_id"]]

        resp = await client.get(
            f"/api/v1/calendar/calendars/{ids['allowed_calendar_id']}/export.ics"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/calendar")
        assert "SUMMARY:Quarterly review" in resp.text

        resp = await client.delete(f"/api/v1/calendar/appointments/{appt_id}")
        assert resp.status_code == 204

        resp = await client.get(f"/api/v1/calendar/appointments/{appt_id}")
        assert resp.status_code == 404

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_calendar_permission_denied_via_rest(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids, agent_id=ids["denied_agent_id"])

    async with client:
        resp = await client.post(
            "/api/v1/calendar/appointments",
            json={
                "calendar_id": ids["allowed_calendar_id"],
                "title": "Should be forbidden",
                "start_time": NOW.isoformat(),
                "end_time": (NOW + timedelta(hours=1)).isoformat(),
            },
        )
        assert resp.status_code == 403

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_calendar_feed_ics_token(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids, agent_id=ids["agent_id"])

    async with client:
        resp = await client.get(
            f"/api/v1/calendar/calendars/{ids['allowed_calendar_id']}/feed-token"
        )
        assert resp.status_code == 200
        token = resp.json()["token"]

        resp = await client.get(
            f"/api/v1/calendar/calendars/{ids['allowed_calendar_id']}/feed.ics",
            params={"login": ids["agent_login"], "token": token},
        )
        assert resp.status_code == 200
        assert "BEGIN:VCALENDAR" in resp.text

        resp = await client.get(
            f"/api/v1/calendar/calendars/{ids['allowed_calendar_id']}/feed.ics",
            params={"login": ids["agent_login"], "token": "wrong"},
        )
        assert resp.status_code == 403

    await engine.dispose()
