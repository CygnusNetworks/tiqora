"""Portal change schedule (``/api/portal/change-schedule``, both dialects).

Seed band 947700-947799: services 947730-947733. Customers ``chg94-cust@example.org`` (assigned
SVC_MINE and SVC_SHARED), ``chg94-cust2@example.org`` (no assignment: the ``<DEFAULT>`` services,
SVC_DEFAULT). Tickets are created by the change service and rolled back with the world.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text, update

from tests._change_fixtures import (
    APP_SETTINGS,
    Q_A,
    U_FULL,
    URL_FIXTURES,
    ChangeWorld,
    change_world,
)
from tiqora.api.app import create_app
from tiqora.api.deps import get_db, get_session_factory_dep
from tiqora.api.portal.deps import get_current_customer, require_portal_enabled
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.customer_auth import AuthenticatedCustomer
from tiqora.itsm.change.models import (
    TiqoraItsmChange,
    TiqoraItsmChangeImpact,
    TiqoraItsmChangeService,
)
from tiqora.itsm.change.service import ChangeCreateIn, create_change
from tiqora.itsm.change.settings import ITSM_CHANGE_SECTION
from tiqora.settings_sections.store import save_section_values
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

CUSTOMER = "chg94-cust@example.org"
CUSTOMER_DEFAULT = "chg94-cust2@example.org"
SVC_MINE, SVC_SHARED, SVC_OTHER, SVC_DEFAULT = 947730, 947731, 947732, 947733
D = datetime(2026, 11, 10)
WIN = (D.replace(hour=8), D.replace(hour=12))
RANGE = {"start": "2026-11-09T00:00:00", "end": "2026-11-12T00:00:00"}
_T = datetime(2026, 10, 1)


@pytest.fixture(params=URL_FIXTURES)
async def w(request: pytest.FixtureRequest) -> AsyncIterator[ChangeWorld]:
    async with change_world(request, str(request.param)) as world:
        for sid, name in (
            (SVC_MINE, "chg94-mine"),
            (SVC_SHARED, "chg94-shared"),
            (SVC_OTHER, "chg94-other-customer"),
            (SVC_DEFAULT, "chg94-default"),
        ):
            await world.session.execute(
                text(
                    "INSERT INTO service (id, name, valid_id, create_time, create_by, change_time,"
                    " change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": sid, "n": name, "t": _T},
            )
        for login, sid in (
            (CUSTOMER, SVC_MINE),
            (CUSTOMER, SVC_SHARED),
            ("chg94-cust3@example.org", SVC_OTHER),
            ("<DEFAULT>", SVC_DEFAULT),
        ):
            await world.session.execute(
                text(
                    "INSERT INTO service_customer_user (customer_user_login, service_id,"
                    " create_time, create_by) VALUES (:l, :s, :t, 1)"
                ),
                {"l": login, "s": sid, "t": _T},
            )
        await _enable(world, True)
        yield world


async def _enable(w: ChangeWorld, on: bool) -> None:
    await save_section_values(
        w.session,
        ITSM_CHANGE_SECTION,
        {"portal_schedule_enabled": on},
        secret_key=APP_SETTINGS.secret_key,
    )


async def _change(
    w: ChangeWorld,
    *,
    title: str = "Switch-Tausch",
    phase: str = "scheduled",
    window: tuple[datetime, datetime] | None = WIN,
    visible: bool = True,
    ticket_service: int | None = None,
    extra: tuple[int, ...] = (),
    impact: tuple[tuple[int, bool], ...] = (),
) -> int:
    view, _ = await create_change(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        data=ChangeCreateIn.model_validate(
            {"title": title, "change_type": "normal", "queue_id": Q_A}
        ),
        user_id=U_FULL,
        session_factory=w.factory,
    )
    tid = view.record.ticket_id
    await w.session.execute(
        text("UPDATE ticket SET service_id = :s WHERE id = :t"), {"s": ticket_service, "t": tid}
    )
    row = await w.session.get(TiqoraItsmChange, tid)
    assert row is not None
    row.phase = phase
    row.portal_visible = visible
    row.implementation_plan = "geheimer Umsetzungsplan"
    row.backout_plan = "geheimer Rueckfallplan"
    row.risk_level = "high"
    if window is not None:
        row.planned_start, row.planned_end = window
    await w.session.execute(
        text("DELETE FROM tiqora_itsm_change_impact WHERE ticket_id = :t"), {"t": tid}
    )
    w.session.add_all(TiqoraItsmChangeService(ticket_id=tid, service_id=s) for s in extra)
    w.session.add_all(
        TiqoraItsmChangeImpact(ticket_id=tid, kind="service", ref_key=str(s), direct=d)
        for s, d in impact
    )
    await w.session.flush()
    return tid


def _wired_client(w: ChangeWorld, *, portal_enabled: bool = True) -> AsyncClient:
    """Production wiring: only the database is replaced; portal gate and customer auth are real."""

    async def _db() -> Any:
        yield w.session

    app = create_app(Settings(environment="test", portal_enabled=portal_enabled))
    app.dependency_overrides[get_db] = _db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _client(w: ChangeWorld, login: str = CUSTOMER) -> AsyncClient:
    async def _db() -> Any:
        yield w.session

    customer = AuthenticatedCustomer(
        id=1,
        login=login,
        email=login,
        customer_id="chg94-co",
        first_name="Cora",
        last_name="Customer",
    )
    app = create_app(APP_SETTINGS)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_session_factory_dep] = lambda: w.factory
    app.dependency_overrides[require_portal_enabled] = lambda: None
    app.dependency_overrides[get_current_customer] = lambda: customer
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _get(w: ChangeWorld, login: str = CUSTOMER, **params: str) -> Any:
    async with _client(w, login) as c:
        return await c.get("/api/portal/change-schedule", params={**RANGE, **params})


async def test_schedule_404_when_disabled_or_not_mode_b(w: ChangeWorld) -> None:
    await _change(w, ticket_service=SVC_MINE)
    assert (await _get(w)).status_code == 200

    await _enable(w, False)
    off = await _get(w)
    assert (off.status_code, off.json()) == (404, {"detail": "Not Found"})

    await _enable(w, True)
    await w.session.execute(
        update(TiqoraSettings).where(TiqoraSettings.key == "itsm.mode").values(value="off")
    )
    mode_a = await _get(w)
    assert (mode_a.status_code, mode_a.json()) == (404, {"detail": "Not Found"})
    await w.session.execute(
        update(TiqoraSettings)
        .where(TiqoraSettings.key == "itsm.mode")
        .values(value="znuny_readthrough")
    )
    assert (await _get(w)).status_code == 404


async def test_portal_schedule_only_customer_services(w: ChangeWorld) -> None:
    direct = await _change(w, title="chg94 direct", ticket_service=SVC_MINE)
    reached = await _change(
        w, title="chg94 impacted", impact=((SVC_OTHER, True), (SVC_SHARED, False))
    )
    extra = await _change(w, title="chg94 extra", ticket_service=SVC_OTHER, extra=(SVC_MINE,))
    await _change(w, title="chg94 foreign", ticket_service=SVC_OTHER, impact=((SVC_OTHER, True),))
    await _change(w, title="chg94 none")
    del direct, reached, extra

    r = await _get(w)
    assert r.status_code == 200
    got = {i["title"]: i["services"] for i in r.json()}
    # the other customer's service is never named, also not next to the customer's own
    assert got == {
        "chg94 direct": ["chg94-mine"],
        "chg94 impacted": ["chg94-shared"],
        "chg94 extra": ["chg94-mine"],
    }
    assert "chg94-other-customer" not in r.text

    # no own assignment: the <DEFAULT> services apply (and only those)
    await _change(w, title="chg94 default", impact=((SVC_DEFAULT, False), (SVC_MINE, True)))
    r2 = await _get(w, CUSTOMER_DEFAULT)
    assert [(i["title"], i["services"]) for i in r2.json()] == [
        ("chg94 default", ["chg94-default"])
    ]
    # a customer with own assignments does not inherit the defaults: only SVC_MINE shows
    own = {i["title"]: i["services"] for i in (await _get(w)).json()}
    assert own["chg94 default"] == ["chg94-mine"]


async def test_only_flagged_changes_in_listed_phases(w: ChangeWorld) -> None:
    for phase in (
        "draft",
        "submitted",
        "assessment",
        "approval",
        "review",
        "rejected",
        "cancelled",
    ):
        await _change(w, title=f"chg94 {phase}", phase=phase, ticket_service=SVC_MINE)
    await _change(w, title="chg94 unflagged", visible=False, ticket_service=SVC_MINE)
    await _change(w, title="chg94 nowindow", window=None, ticket_service=SVC_MINE)
    later = (WIN[0] + timedelta(days=30), WIN[1] + timedelta(days=30))
    await _change(w, title="chg94 later", window=later, ticket_service=SVC_MINE)
    for phase in ("scheduled", "implementing", "closed"):
        await _change(w, title=f"chg94 ok {phase}", phase=phase, ticket_service=SVC_MINE)

    r = await _get(w)
    assert sorted((i["title"], i["phase"]) for i in r.json()) == [
        ("chg94 ok closed", "closed"),
        ("chg94 ok implementing", "implementing"),
        ("chg94 ok scheduled", "scheduled"),
    ]
    # half-open range: a change that ends where the range starts is not in it
    edge = await _get(w, start=WIN[1].isoformat(), end=(WIN[1] + timedelta(days=1)).isoformat())
    assert edge.json() == []
    wide = await _get(w, start="2026-08-01T00:00:00", end="2026-12-31T00:00:00")
    assert wide.status_code == 422
    assert wide.json()["detail"]["code"] == "range_too_large"
    back = await _get(w, start=RANGE["end"], end=RANGE["start"])
    assert back.status_code == 422, back.text


async def test_output_has_no_internal_fields(w: ChangeWorld) -> None:
    tid = await _change(w, title="chg94 fields", ticket_service=SVC_MINE)
    r = await _get(w)
    [item] = r.json()
    assert set(item) == {
        "tn",
        "title",
        "change_type",
        "phase",
        "planned_start",
        "planned_end",
        "services",
    }
    assert item["change_type"] == "normal"
    assert item["planned_start"].startswith("2026-11-10T08:00:00")
    # the plain-text plans and the ids are not in the body, only the seven keys above
    assert "geheimer" not in r.text
    assert tid not in {v for v in item.values() if isinstance(v, int)}


async def test_production_wiring_gate_and_auth(w: ChangeWorld) -> None:
    await _change(w, ticket_service=SVC_MINE)
    async with _wired_client(w, portal_enabled=False) as c:
        off = await c.get("/api/portal/change-schedule", params=RANGE)
    assert (off.status_code, off.json()) == (404, {"detail": "Not Found"})
    async with _wired_client(w) as c:
        anon = await c.get("/api/portal/change-schedule", params=RANGE)
    assert (anon.status_code, anon.json()) == (401, {"detail": "Not authenticated"})
    # the schedule's own switch is checked before authentication: nothing to learn when off
    await _enable(w, False)
    async with _wired_client(w) as c:
        feature_off = await c.get("/api/portal/change-schedule", params=RANGE)
    assert (feature_off.status_code, feature_off.json()) == (404, {"detail": "Not Found"})


async def test_invalid_service_assignment_is_not_shown(w: ChangeWorld) -> None:
    await w.session.execute(
        text("UPDATE service SET valid_id = 2 WHERE id = :s"), {"s": SVC_SHARED}
    )
    await _change(w, title="chg94 invalid", ticket_service=SVC_SHARED)
    await _change(w, title="chg94 both", ticket_service=SVC_SHARED, extra=(SVC_MINE,))
    got = {i["title"]: i["services"] for i in (await _get(w)).json()}
    assert got == {"chg94 both": ["chg94-mine"]}


async def test_scan_cap_sets_truncated_header(
    w: ChangeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.itsm.change import portal as portal_module

    monkeypatch.setattr(portal_module, "LIST_SCAN_CAP", 2)
    for n in range(3):
        await _change(w, title=f"chg94 cap {n}", extra=(SVC_MINE,))
    r = await _get(w)
    assert r.status_code == 200
    assert r.headers["X-Schedule-Truncated"] == "true"
    assert len(r.json()) == 2
    monkeypatch.setattr(portal_module, "LIST_SCAN_CAP", 5000)
    ok = await _get(w)
    assert "X-Schedule-Truncated" not in ok.headers and len(ok.json()) == 3


async def test_scan_cap_keeps_the_earliest_and_chunks_services(
    w: ChangeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-M6: the capped scans are ordered by planned start (the cut keeps the earliest changes
    on both dialects) and the customer's services go in chunks (here: one per query)."""
    from tiqora.itsm.change import portal as portal_module
    from tiqora.itsm.change import read as read_module

    late = await _change(
        w,
        title="chg94 spaet",
        extra=(SVC_MINE,),
        window=(WIN[0] + timedelta(hours=3), WIN[1] + timedelta(hours=3)),
    )
    early = await _change(w, title="chg94 frueh", ticket_service=SVC_SHARED)
    middle = await _change(
        w,
        title="chg94 mitte",
        impact=((SVC_MINE, False),),
        window=(WIN[0] + timedelta(hours=1), WIN[1] + timedelta(hours=1)),
    )
    assert late < early < middle  # ids and starts disagree on purpose
    monkeypatch.setattr(read_module, "CHUNK", 1)
    monkeypatch.setattr(portal_module, "LIST_SCAN_CAP", 2)
    r = await _get(w)
    assert r.headers["X-Schedule-Truncated"] == "true"
    assert [i["title"] for i in r.json()] == ["chg94 frueh", "chg94 mitte"]
    monkeypatch.setattr(portal_module, "LIST_SCAN_CAP", 5000)
    whole = await _get(w)
    assert [i["title"] for i in whole.json()] == ["chg94 frueh", "chg94 mitte", "chg94 spaet"]
