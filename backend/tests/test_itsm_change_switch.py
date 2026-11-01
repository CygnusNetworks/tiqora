"""The module switch ``itsm_change.enabled`` (and the dependency on ``approvals.enabled``) on every
surface: off hides and refuses, deletes nothing (both dialects).

The seed band of ``tests._change_fixtures`` (946300-946399); here 946380-946389. The routes commit;
the test owns the transaction (``commit`` is ``flush``), so nothing leaks.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from tests._change_fixtures import (
    Q_A,
    U_FULL,
    URL_FIXTURES,
    ChangeWorld,
    change_world,
)
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db, get_session_factory_dep
from tiqora.api.portal.deps import get_current_customer, require_portal_enabled
from tiqora.api.v1 import itsm_change as change_routes
from tiqora.api.v1.admin import daemons as admin_daemons
from tiqora.approvals.settings import APPROVALS_SECTION
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.customer_auth import AuthenticatedCustomer
from tiqora.extensions.core import get_registry
from tiqora.itsm.change.impact import ChangeImpactPayload, enqueue_impact, run_change_impact_job
from tiqora.itsm.change.legacy.archive import archive_blocker
from tiqora.itsm.change.models import TiqoraItsmChange
from tiqora.itsm.change.settings import ITSM_CHANGE_SECTION, ItsmChangeSettings
from tiqora.itsm.settings import ItsmSettings
from tiqora.itsm.setup import apply_setup
from tiqora.itsm.summary import ticket_itsm_summary
from tiqora.jobs.context import JobContext
from tiqora.reporting.catalogue import active_builtin
from tiqora.reporting.catalogue_itsm import CHANGE_ONLY_BUILTINS
from tiqora.reporting.run_service import RequestError, RunOverrides, prepare_ref
from tiqora.reporting.scope import available_sources, switched_off_message
from tiqora.reporting.semantic import active_source
from tiqora.settings_sections.store import save_section_values
from tiqora.worker.itsm_change import run_itsm_change_tick_in

pytestmark = pytest.mark.db

SETTINGS = Settings(environment="test", portal_enabled=True)
ADMIN_ID = 1
UNAVAILABLE = {"detail": {"code": "change_unavailable"}}
CHANGE_BUILTINS = {
    "builtin.itsm_changes_by_status",
    "builtin.itsm_change_outcomes",
    "builtin.itsm_change_transitions",
    "builtin.itsm_change_requesters",
    "builtin.itsm_changes_per_ci_class",
}


def _user(uid: int) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=uid,
        login=f"chg94.{uid}",
        first_name="Erika",
        last_name="Beispiel",
        auth_method="session",
    )


@dataclass
class Api:
    client: AsyncClient
    world: ChangeWorld
    as_user: dict[str, AuthenticatedUser]

    async def create(self, **fields: Any) -> int:
        body = {"title": "Switch-Tausch", "change_type": "normal", "queue_id": Q_A, **fields}
        got = await self.client.post("/api/v1/itsm/changes", json=body)
        assert got.status_code == 201, got.text
        return int(got.json()["change"]["ticket_id"])

    async def switch(self, *, change: bool | None = None, approvals: bool | None = None) -> None:
        s = self.world.session
        key = SETTINGS.secret_key
        if change is not None:
            await save_section_values(s, ITSM_CHANGE_SECTION, {"enabled": change}, secret_key=key)
        if approvals is not None:
            await save_section_values(s, APPROVALS_SECTION, {"enabled": approvals}, secret_key=key)
        await s.commit()


@pytest.fixture(params=URL_FIXTURES)
async def api(request: pytest.FixtureRequest) -> AsyncIterator[Api]:
    async with change_world(request, str(request.param)) as world:
        who = {"u": _user(U_FULL)}

        async def _db() -> Any:
            yield world.session

        customer = AuthenticatedCustomer(
            id=1,
            login="kunde@example.org",
            email="kunde@example.org",
            customer_id="chg94-co",
            first_name="Cora",
            last_name="Customer",
        )
        app = create_app(SETTINGS)
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_session_factory_dep] = lambda: world.factory
        app.dependency_overrides[get_current_user] = lambda: who["u"]
        app.dependency_overrides[require_portal_enabled] = lambda: None
        app.dependency_overrides[get_current_customer] = lambda: customer
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield Api(client, world, who)


SWITCHES = [
    pytest.param({"change": False}, id="change_off"),
    pytest.param({"approvals": False}, id="approvals_off"),
]


def _concrete(route: APIRoute, ticket_id: int) -> str:
    return re.sub(r"\{[^}]+\}", str(ticket_id), f"/api/v1{route.path}")


def test_default_is_on_and_the_section_stores_it() -> None:
    assert ItsmChangeSettings().enabled is True
    assert ITSM_CHANGE_SECTION.storage_key("enabled") == "itsm_change.enabled"


@pytest.mark.parametrize("off", SWITCHES)
async def test_every_route_answers_404_but_the_status(api: Api, off: dict[str, bool]) -> None:
    """Walks the routers (legacy views included), so a route added later is covered as well, and a
    request that would not validate gets the same answer."""
    tid = await api.create()
    await api.switch(**off)
    routes = [
        r
        for r in (*change_routes.router.routes, *change_routes.ticket_router.routes)
        if isinstance(r, APIRoute)
    ]
    assert len(routes) >= 30
    assert any("legacy-changes" in r.path for r in routes)
    for route in routes:
        path = _concrete(route, tid)
        for method in route.methods:
            got = await api.client.request(method, path, json={})
            assert (got.status_code, got.json()) == (404, UNAVAILABLE), (method, path)
    status = await api.client.get("/api/v1/itsm/changes/status")
    assert status.status_code == 200
    body = status.json()
    assert (body["available"], body["legacy"], body["can_see_legacy"]) == (False, "none", False)
    assert body["reason"] == ("change_off" if "change" in off else "approvals_off")


async def test_on_again_brings_everything_back(api: Api) -> None:
    tid = await api.create(title="Switch-Tausch")
    await api.switch(change=False)
    assert (await api.client.get(f"/api/v1/itsm/changes/{tid}")).status_code == 404
    await api.switch(change=True)
    got = await api.client.get(f"/api/v1/itsm/changes/{tid}")
    assert got.status_code == 200, got.text
    assert got.json()["ticket_id"] == tid
    assert (await api.client.get("/api/v1/itsm/changes/status")).json()["reason"] == "ok"


async def test_admin_pages_and_setup_keep_working(api: Api) -> None:
    await api.switch(change=False)
    api.as_user["u"] = _user(ADMIN_ID)
    for path in (
        "/api/v1/admin/itsm/change/templates",
        "/api/v1/admin/itsm/change/cabs",
        "/api/v1/admin/itsm/change/freeze-windows",
        "/api/v1/admin/itsm/change/risk-questions",
        "/api/v1/admin/itsm/change/legacy-archive",
        "/api/v1/admin/itsm/setup",
        "/api/v1/admin/settings-sections/itsm_change",
    ):
        got = await api.client.get(path)
        assert got.status_code == 200, (path, got.text)
    applied = await api.client.post("/api/v1/admin/itsm/setup", json={"groups": ["change"]})
    assert applied.status_code == 200, applied.text
    # the archive job is judged by mode and tables only, not by the switch
    blocker = await archive_blocker(api.world.session)
    assert blocker is None or blocker.code == "legacy_archive_no_tables"


@pytest.mark.parametrize("off", SWITCHES)
async def test_portal_schedule_is_gone(api: Api, off: dict[str, bool]) -> None:
    await save_section_values(
        api.world.session,
        ITSM_CHANGE_SECTION,
        {"portal_schedule_enabled": True},
        secret_key=SETTINGS.secret_key,
    )
    await api.world.session.commit()
    params = {"start": "2026-11-01T00:00:00Z", "end": "2026-11-08T00:00:00Z"}
    on = await api.client.get("/api/portal/change-schedule", params=params)
    assert on.status_code == 200, on.text
    await api.switch(**off)
    got = await api.client.get("/api/portal/change-schedule", params=params)
    assert (got.status_code, got.json()) == (404, {"detail": "Not Found"})


@pytest.mark.parametrize("off", SWITCHES)
async def test_ticket_summary_has_no_change_refs(api: Api, off: dict[str, bool]) -> None:
    tid = await api.create()
    cfg = ItsmSettings()
    session = api.world.session
    await apply_setup(session, cfg, groups={"incident_problem"}, user_id=ADMIN_ID)
    before = await ticket_itsm_summary(session, tid, user_id=U_FULL, cfg=cfg)
    assert before.change is not None
    await api.switch(**off)
    after = await ticket_itsm_summary(session, tid, user_id=U_FULL, cfg=cfg)
    assert after.change is None and after.change_task is None


@pytest.mark.parametrize("off", SWITCHES)
async def test_worker_tick_pauses_without_error_and_dienste_says_why(
    api: Api, off: dict[str, bool]
) -> None:
    session = api.world.session
    await api.create()
    await session.execute(text("DELETE FROM tiqora_job WHERE kind = 'core.itsm_change_impact'"))
    await api.switch(**off)
    assert await run_itsm_change_tick_in(session, SETTINGS) == {"skipped": 1}
    jobs = await session.execute(
        select(TiqoraJob.id).where(TiqoraJob.kind == "core.itsm_change_impact")
    )
    assert jobs.first() is None

    listed = await admin_daemons.list_daemons(_user(ADMIN_ID), session)  # type: ignore[arg-type]
    rows = {s.slug: s for s in listed.services}
    reason = "change_off" if "change" in off else "approvals_off"
    assert (rows["itsm_change"].enabled, rows["itsm_change"].paused_reason) == (True, reason)
    assert rows["itsm_change"].last_error is None
    assert rows["approvals"].paused_reason == ("approvals_off" if "approvals" in off else None)
    assert rows["poller"].paused_reason is None


async def test_impact_request_waits_for_the_switch(api: Api) -> None:
    """A CI link or service edit while off queues no job; the stamp makes the first tick after it
    pick the change up. A job queued before the switch ends without a failure."""
    session = api.world.session
    tid = await api.create()
    await session.execute(text("DELETE FROM tiqora_job WHERE kind = 'core.itsm_change_impact'"))
    await api.switch(change=False)
    assert await enqueue_impact(session, ticket_id=tid, user_id=None) is None
    row = await session.get(TiqoraItsmChange, tid)
    assert row is not None and row.impact_requested_at is not None
    jobs = await session.execute(
        select(TiqoraJob.id).where(TiqoraJob.kind == "core.itsm_change_impact")
    )
    assert jobs.first() is None

    class _Factory:
        def __call__(self) -> Any:
            return self

        async def __aenter__(self) -> Any:
            return session

        async def __aexit__(self, *exc: object) -> None:
            return None

    ctx = JobContext(
        job_id=0,
        kind="core.itsm_change_impact",
        created_by=None,
        settings=SETTINGS,
        session_factory=_Factory(),  # type: ignore[arg-type]
        attempt=1,
        max_attempts=1,
    )
    out = await run_change_impact_job(ctx, ChangeImpactPayload(ticket_id=tid, user_id=None))
    assert out == {"passes": 0, "skipped": True, "reason": "change_off"}

    await api.switch(change=True)
    assert (await run_itsm_change_tick_in(session, SETTINGS))["queued"] >= 1


@pytest.mark.parametrize("off", SWITCHES)
async def test_reports_hide_sources_and_builtins(api: Api, off: dict[str, bool]) -> None:
    session = api.world.session
    registry = get_registry()
    assert {"changes", "change_events"} <= {
        s.key for s in await available_sources(session, registry)
    }
    listed = await api.client.get("/api/v1/reports")
    assert listed.status_code == 200, listed.text
    assert {r["key"] for r in listed.json()["reports"]} >= CHANGE_BUILTINS

    await api.switch(**off)
    offered = {s.key for s in await available_sources(session, registry)}
    assert not {"changes", "change_events"} & offered
    assert {"config_items", "ci_tickets"} <= offered  # the CMDB stays
    listed = await api.client.get("/api/v1/reports")
    keys = {r["key"] for r in listed.json()["reports"]}
    assert not CHANGE_BUILTINS & keys
    assert "builtin.cmdb_cis_by_class" in keys
    sources = await api.client.get("/api/v1/reporting/sources")
    available = {s["key"]: s["available"] for s in sources.json()}
    assert (available["changes"], available["change_events"]) == (False, False)
    assert available["ci_tickets"] is True


@pytest.mark.parametrize("off", SWITCHES)
async def test_tiles_and_schedules_are_refused_only_while_off(
    api: Api, off: dict[str, bool]
) -> None:
    session = api.world.session
    registry = get_registry()

    async def refused(key: str) -> str | None:
        report = active_builtin(registry, key)
        assert report is not None
        source = active_source(registry, report.definition.source)
        assert source is not None
        return await switched_off_message(session, source, report)

    assert {"builtin.itsm_changes_per_ci_class"} == CHANGE_ONLY_BUILTINS
    for key in CHANGE_BUILTINS:
        assert await refused(key) is None
    await api.switch(**off)
    for key in CHANGE_BUILTINS:
        assert await refused(key) == "Change-Management ist ausgeschaltet."
    assert await refused("builtin.cmdb_cis_by_class") is None
    assert await refused("builtin.itsm_incident_prone_cis") is None


@pytest.mark.parametrize("off", SWITCHES)
async def test_schedule_path_refuses_and_the_api_run_path_stays(
    api: Api, off: dict[str, bool]
) -> None:
    """``refuse_switched_off`` is what schedules and tiles pass; ``POST /reports/run`` does not."""
    session = api.world.session
    await api.switch(**off)
    with pytest.raises(RequestError) as refused:
        await prepare_ref(
            session,
            U_FULL,
            report_key="builtin.itsm_changes_by_status",
            definition=None,
            overrides=RunOverrides(),
            refuse_switched_off=True,
        )
    assert refused.value.status_code == 409
    assert refused.value.message == "Change-Management ist ausgeschaltet."
    prepared = await prepare_ref(
        session,
        U_FULL,
        report_key="builtin.itsm_changes_by_status",
        definition=None,
        overrides=RunOverrides(),
    )
    assert prepared.definition.source == "changes"
