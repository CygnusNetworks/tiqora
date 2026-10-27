"""Freeze windows: admin CRUD, validation, occurrence expansion, the agent read API.

Seed band 946700-946799 (windows are named ``chg94-...``); users, services and the world come from
``tests._change_fixtures``. Both dialects. Rows live in the rolled-back world session.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from tests._change_fixtures import (
    SERVICE,
    SERVICE_B,
    U_FULL,
    URL_FIXTURES,
    ChangeWorld,
    change_world,
)
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db, get_session_factory_dep
from tiqora.calendar.recurrence import MAX_OCCURRENCES
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.itsm.change.freeze import FreezeIn, create_freeze, occurrences

pytestmark = pytest.mark.db

ADMIN_ID = 1
ADMIN = "/api/v1/admin/itsm/change/freeze-windows"
READ = "/api/v1/itsm/freeze-windows"
#: Friday 2026-11-06
FRI_18 = "2026-11-06T18:00:00Z"
MON_06 = "2026-11-09T06:00:00Z"


@pytest.fixture(params=URL_FIXTURES)
async def w(request: pytest.FixtureRequest) -> AsyncIterator[ChangeWorld]:
    async with change_world(request, str(request.param)) as world:
        yield world


@pytest.fixture
def make_client(w: ChangeWorld) -> Callable[[int, str], AsyncClient]:
    async def _db() -> Any:
        yield w.session

    def build(user_id: int, login: str) -> AsyncClient:
        user = AuthenticatedUser(
            id=user_id, login=login, first_name="Erika", last_name="Beispiel", auth_method="session"
        )
        app = create_app(Settings(environment="test"))
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_session_factory_dep] = lambda: w.factory
        app.dependency_overrides[get_current_user] = lambda: user
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    return build


def _body(**fields: Any) -> dict[str, Any]:
    return {"name": "chg94-freeze", "starts_at": FRI_18, "ends_at": MON_06, **fields}


def _data(**fields: Any) -> FreezeIn:
    return FreezeIn.model_validate(_body(**fields))


async def test_freeze_crud_admin_only(
    w: ChangeWorld, make_client: Callable[[int, str], AsyncClient]
) -> None:
    body = _body(
        note="Jahresabschluss",
        enforcement="warn",
        allow_emergency=False,
        scope="services",
        service_ids=[SERVICE_B, SERVICE],
        recurrence={"type": "Weekly", "interval": 1, "count": 4},
    )
    async with make_client(U_FULL, "chg94-full") as agent:
        for call in (
            agent.get(ADMIN),
            agent.post(ADMIN, json=body),
            agent.get(f"{ADMIN}/1"),
            agent.put(f"{ADMIN}/1", json=body),
            agent.delete(f"{ADMIN}/1"),
        ):
            assert (await call).status_code == 403

    async with make_client(ADMIN_ID, "root@localhost") as admin:
        created = await admin.post(ADMIN, json=body)
        assert created.status_code == 201, created.text
        win = created.json()
        assert (win["name"], win["enforcement"], win["allow_emergency"], win["valid"]) == (
            "chg94-freeze",
            "warn",
            False,
            True,
        )
        assert win["service_ids"] == sorted([SERVICE, SERVICE_B])
        assert win["starts_at"].startswith("2026-11-06T18:00:00")
        assert win["recurrence"]["type"] == "Weekly" and win["recurrence"]["count"] == 4
        assert win["created_by"] == ADMIN_ID

        got = (await admin.get(f"{ADMIN}/{win['id']}")).json()
        stamps = ("created_at", "updated_at")  # MariaDB drops the microseconds
        assert {k: v for k, v in got.items() if k not in stamps} == {
            k: v for k, v in win.items() if k not in stamps
        }
        assert [x["id"] for x in (await admin.get(ADMIN)).json()] == [win["id"]]

        put = await admin.put(
            f"{ADMIN}/{win['id']}",
            json=_body(name="chg94-freeze-2", scope="all", service_ids=[SERVICE], valid=False),
        )
        assert put.status_code == 200, put.text
        assert (put.json()["name"], put.json()["scope"], put.json()["valid"]) == (
            "chg94-freeze-2",
            "all",
            False,
        )
        # scope "all" keeps no service rows; the recurrence was replaced as well
        assert put.json()["service_ids"] == [] and put.json()["recurrence"] is None

        assert (await admin.get(f"{ADMIN}/946799")).status_code == 404
        assert (await admin.put(f"{ADMIN}/946799", json=_body())).status_code == 404
        assert (await admin.delete(f"{ADMIN}/{win['id']}")).status_code == 204
        assert (await admin.delete(f"{ADMIN}/{win['id']}")).status_code == 404
        assert (await admin.get(ADMIN)).json() == []


async def test_freeze_validation(
    w: ChangeWorld, make_client: Callable[[int, str], AsyncClient]
) -> None:
    async with make_client(ADMIN_ID, "root@localhost") as admin:

        async def code(**fields: Any) -> tuple[int, Any]:
            res = await admin.post(ADMIN, json=_body(**fields))
            return res.status_code, res.json()["detail"]

        status, detail = await code(ends_at=FRI_18)  # end == start
        assert status == 422 and isinstance(detail, list)
        status, detail = await code(ends_at="2026-11-06T17:00:00Z")
        assert status == 422 and isinstance(detail, list)
        status, detail = await code(recurrence={"type": "Hourly"})
        assert status == 422 and isinstance(detail, list)
        status, detail = await code(enforcement="maybe")
        assert status == 422
        status, detail = await code(scope="services", service_ids=[])
        assert (status, detail["code"]) == (422, "freeze_services_required")
        status, detail = await code(scope="services", service_ids=[946799])
        assert (status, detail["code"]) == (422, "service_not_found")
        status, detail = await code(recurrence={"type": "Daily", "until": "2026-11-01T00:00:00Z"})
        assert (status, detail["code"]) == (422, "freeze_recurrence_invalid")
        assert (await admin.get(ADMIN)).json() == []  # nothing was stored

        # times with an offset are stored as UTC (ruling F35)
        ok = await admin.post(
            ADMIN, json=_body(starts_at="2026-11-06T19:00:00+01:00", ends_at=MON_06)
        )
        assert ok.status_code == 201, ok.text
        assert ok.json()["starts_at"].startswith("2026-11-06T18:00:00")


async def test_occurrences_one_off_and_weekly(w: ChangeWorld) -> None:
    await create_freeze(w.session, data=_data(name="chg94-oneoff"), user_id=ADMIN_ID)
    weekly = await create_freeze(
        w.session,
        data=_data(
            name="chg94-weekly",
            scope="services",
            service_ids=[SERVICE],
            recurrence={"type": "Weekly", "interval": 1, "count": 4},
        ),
        user_id=ADMIN_ID,
    )
    occ = await occurrences(w.session, start=datetime(2026, 11, 1), end=datetime(2026, 12, 31))
    mine = [o for o in occ if o.freeze_id == weekly.id]
    assert [(o.start, o.end) for o in mine] == [
        (datetime(2026, 11, 6, 18), datetime(2026, 11, 9, 6)),
        (datetime(2026, 11, 13, 18), datetime(2026, 11, 16, 6)),
        (datetime(2026, 11, 20, 18), datetime(2026, 11, 23, 6)),
        (datetime(2026, 11, 27, 18), datetime(2026, 11, 30, 6)),
    ]
    assert mine[0].service_ids == frozenset({SERVICE}) and mine[0].scope == "services"
    assert (mine[0].enforcement, mine[0].allow_emergency) == ("block", True)
    one = [o for o in occ if o.name == "chg94-oneoff"]
    assert [(o.start, o.end, o.service_ids) for o in one] == [
        (datetime(2026, 11, 6, 18), datetime(2026, 11, 9, 6), frozenset())
    ]
    # sorted by start; a range inside the third weekend finds that one only
    assert [o.start for o in occ] == sorted(o.start for o in occ)
    third = await occurrences(w.session, start=datetime(2026, 11, 21), end=datetime(2026, 11, 22))
    assert [(o.freeze_id, o.start) for o in third] == [(weekly.id, datetime(2026, 11, 20, 18))]
    # a window that ended before the range, or starts after it, is not returned
    assert (
        await occurrences(w.session, start=datetime(2026, 12, 2), end=datetime(2026, 12, 9)) == []
    )
    assert (
        await occurrences(w.session, start=datetime(2026, 11, 1), end=datetime(2026, 11, 6)) == []
    )


async def test_occurrences_skip_invalid(
    w: ChangeWorld, make_client: Callable[[int, str], AsyncClient]
) -> None:
    await create_freeze(w.session, data=_data(name="chg94-off", valid=False), user_id=ADMIN_ID)
    live = await create_freeze(w.session, data=_data(name="chg94-on"), user_id=ADMIN_ID)
    rng = {"start": "2026-11-01T00:00:00Z", "end": "2026-11-30T00:00:00Z"}
    occ = await occurrences(w.session, start=datetime(2026, 11, 1), end=datetime(2026, 11, 30))
    assert [o.freeze_id for o in occ] == [live.id]

    async with make_client(U_FULL, "chg94-full") as agent:
        res = await agent.get(READ, params=rng)
        assert res.status_code == 200, res.text
        assert [(o["freeze_id"], o["name"]) for o in res.json()] == [(live.id, "chg94-on")]
        assert res.json()[0]["start"].startswith("2026-11-06T18:00:00")
        assert res.json()[0]["service_ids"] == []
    async with make_client(ADMIN_ID, "root@localhost") as admin:
        assert len((await admin.get(ADMIN)).json()) == 2  # the admin list keeps invalid ones


async def test_occurrences_bounded(
    w: ChangeWorld, make_client: Callable[[int, str], AsyncClient]
) -> None:
    daily: dict[str, Any] = {
        "starts_at": "2026-11-06T18:00:00Z",
        "ends_at": "2026-11-06T19:00:00Z",
        "recurrence": {"type": "Daily", "interval": 1},
    }
    await create_freeze(w.session, data=_data(name="chg94-d1", **daily), user_id=ADMIN_ID)
    one = await occurrences(w.session, start=datetime(2026, 11, 1), end=datetime(2036, 11, 1))
    assert 0 < len(one) <= MAX_OCCURRENCES
    # many series together stay under the same bound
    await create_freeze(w.session, data=_data(name="chg94-d2", **daily), user_id=ADMIN_ID)
    await create_freeze(w.session, data=_data(name="chg94-d3", **daily), user_id=ADMIN_ID)
    many = await occurrences(w.session, start=datetime(2026, 11, 1), end=datetime(2036, 11, 1))
    assert len(many) == MAX_OCCURRENCES
    assert [o.start for o in many] == sorted(o.start for o in many)

    async with make_client(U_FULL, "chg94-full") as agent:
        # the read API bounds the range it expands
        wide = await agent.get(
            READ, params={"start": "2026-11-01T00:00:00Z", "end": "2036-11-01T00:00:00Z"}
        )
        assert (wide.status_code, wide.json()["detail"]["code"]) == (422, "range_too_large")
        flipped = await agent.get(
            READ, params={"start": "2026-11-02T00:00:00Z", "end": "2026-11-01T00:00:00Z"}
        )
        assert (flipped.status_code, flipped.json()["detail"]["code"]) == (422, "range_invalid")
        ok = await agent.get(
            READ, params={"start": "2026-11-01T00:00:00Z", "end": "2027-01-31T00:00:00Z"}
        )
        assert ok.status_code == 200 and len(ok.json()) == 3 * 86  # three series, 86 days
