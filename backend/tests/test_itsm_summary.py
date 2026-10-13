"""Ticket ITSM summary (Task 21): ``ticket_itsm_summary`` and ``GET /tickets/{id}/itsm``.

Both dialects. One rolled-back session carries the world (the routes' ``commit`` becomes
``flush``). Seed band 941600-941699. ``FULL`` holds rw on queue A, ``RO`` only ro, ``NOBODY``
nothing; queue B is out of reach for everybody. Mode A with the fake provider behind it (real
Znuny ITSM tables for the matrix and the service columns); one test per dialect x schema profile
runs the real ``ZnunyCiProvider``.

Tickets: ``T`` incident on service ``SVC`` (A), ``PR`` problem linked to ``T`` (A), ``MAJ`` active
major incident linked to ``T`` (A), ``HIDDEN_MAJ`` active major incident linked to ``T`` in
queue B, ``OTHER`` second incident on ``SVC`` (A).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests._itsm_adapter_helpers import count_statements, itsm_session, seed_service
from tests._itsm_db import ITSM_PARAMS
from tests._itsm_fake_provider import INCIDENT, OPERATIONAL, FakeCiProvider
from tests._itsm_mode_helpers import itsm_url, profile_packages, seed_packages, store
from tests.test_itsm_ci_api import FULL, NOBODY, NOW, QUEUE_A, QUEUE_B, RO, _seed_access, _user
from tests.test_itsm_problem import _state_id, _type_id
from tests.test_itsm_znuny_provider import FULL as Z_FULL
from tests.test_itsm_znuny_provider import TICKET as Z_TICKET
from tests.test_itsm_znuny_provider import Env, seed_world
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.domain.api_key_scopes import path_to_area
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.ticket_write_service import link_objects
from tiqora.extensions import Registry, set_registry_for_tests
from tiqora.itsm.mode import SETTING_MODE
from tiqora.itsm.models import TiqoraItsmMajorIncident, TiqoraItsmProblem
from tiqora.itsm.providers import CI_PROVIDERS, CiProviderFactory
from tiqora.itsm.settings import ITSM_SECTION, ItsmSettings
from tiqora.itsm.summary import TicketNotReadable, ticket_itsm_summary
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache
from tiqora.itsm.znuny_adapter.provider import ZnunyCiProvider
from tiqora.jobs.service import utc_now
from tiqora.settings_sections.store import save_section_values
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

T, PR, MAJ, HIDDEN_MAJ, OTHER = 941601, 941602, 941603, 941604, 941605
SVC = 941650
DIALECTS = ["mysql", "postgresql"]
SECRET = Settings(environment="test").secret_key
#: statements ``ticket_itsm_summary`` may run for a fully populated ticket (measured: 33, constant
#: in the number of links and CIs; the pre-flight target of 8 is out of reach because every
#: PermissionEngine, the mode and the settings load their own rows -- the pin is "no N+1")
STATEMENT_BOUND = 36


@dataclass
class World:
    client: AsyncClient
    session: AsyncSession
    engine: AsyncEngine
    fake: FakeCiProvider
    as_user: dict[str, AuthenticatedUser]

    def login(self, uid: int) -> None:
        self.as_user["u"] = _user(uid)

    async def configure(self, **values: Any) -> None:
        await save_section_values(self.session, ITSM_SECTION, values, secret_key=SECRET)

    async def summary(self, ticket_id: int = T, user_id: int = FULL) -> Any:
        return await ticket_itsm_summary(
            self.session, ticket_id, user_id=user_id, cfg=ItsmSettings()
        )

    async def get(self, ticket_id: int = T) -> dict[str, Any]:
        got = await self.client.get(f"/api/v1/tickets/{ticket_id}/itsm")
        assert got.status_code == 200, got.text
        body: dict[str, Any] = got.json()
        return body

    async def link(self, source: int, *others: int, link_type: str = "Normal") -> None:
        for other in others:
            await link_objects(
                self.session,
                source_object="Ticket",
                source_key=str(source),
                target_object="Ticket",
                target_key=str(other),
                link_type=link_type,
                user_id=1,
            )

    async def new_ticket(
        self,
        ticket_id: int,
        title: str,
        *,
        type_name: str = "Incident",
        queue_id: int = QUEUE_A,
        service_id: int | None = None,
        created: Any = None,
    ) -> None:
        await insert_ticket(
            self.session,
            ticket_id,
            title,
            type_name=type_name,
            queue_id=queue_id,
            service_id=service_id,
            created=created,
        )

    async def set_field(self, ticket_id: int, name: str, value: str) -> None:
        await self.session.execute(
            text(
                "INSERT INTO dynamic_field_value (field_id, object_id, value_text)"
                " SELECT id, :t, :v FROM dynamic_field WHERE name = :n AND object_type = 'Ticket'"
            ),
            {"t": ticket_id, "v": value, "n": name},
        )


async def insert_ticket(
    session: AsyncSession,
    ticket_id: int,
    title: str,
    *,
    type_name: str = "Incident",
    queue_id: int = QUEUE_A,
    service_id: int | None = None,
    created: Any = None,
) -> None:
    type_id = await _type_id(session, type_name)
    state_id = await _state_id(session, "open", "open")
    stamp = created or NOW
    await session.execute(
        text(
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, service_id,"
            " user_id, responsible_user_id, ticket_priority_id, ticket_state_id, timeout,"
            " until_time, escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, create_time, create_by, change_time,"
            " change_by) VALUES (:id, :tn, :title, :q, 1, :ty, :svc, 1, 1, 3, :st, 0, 0, 0, 0, 0,"
            " 0, 0, :t, 1, :t, 1)"
        ),
        {
            "id": ticket_id,
            "tn": f"20261013{ticket_id}",
            "title": title,
            "q": queue_id,
            "ty": type_id,
            "svc": service_id,
            "st": state_id,
            "t": stamp,
        },
    )


async def _seed(session: AsyncSession) -> None:
    await _seed_access(session)
    await seed_service(session, SVC, "itsm94 mail service")
    await insert_ticket(session, T, "Mailserver antwortet nicht", service_id=SVC)
    await insert_ticket(session, PR, "Ursache Mailserver", type_name="Problem")
    await insert_ticket(session, MAJ, "Totalausfall Rechenzentrum", type_name="Incident::Major")
    await insert_ticket(
        session,
        HIDDEN_MAJ,
        "Fremder Totalausfall",
        type_name="Incident::Major",
        queue_id=QUEUE_B,
    )
    await insert_ticket(session, OTHER, "Mail langsam", service_id=SVC)


def _registry(fake: FakeCiProvider, mode: str) -> Registry:
    registry = Registry()
    registry.add(CI_PROVIDERS, CiProviderFactory(mode=mode, create=lambda _s: fake))  # type: ignore[arg-type]
    registry.freeze()
    return registry


@asynccontextmanager
async def world_for(
    request: pytest.FixtureRequest, dialect: str, *, mode: str = "znuny_readthrough"
) -> AsyncIterator[World]:
    url = itsm_url(request, dialect, "itsm-6")
    seed_packages(url, profile_packages("itsm-6"))
    store(url, SETTING_MODE, mode)
    fake = FakeCiProvider()
    fake.add_ci(11, "srv-mail-01.example.org", OPERATIONAL)
    fake.add_ci(12, "srv-mail-02.example.org", INCIDENT)
    fake.add_ci(13, "srv-secret.example.org", OPERATIONAL, hidden_for=[RO])
    fake.link_service(SVC, 12)
    set_registry_for_tests(_registry(fake, mode))
    request.addfinalizer(lambda: set_registry_for_tests(None))
    try:
        async with itsm_session(request, dialect, "itsm-6") as (session, _reader, engine):
            await _seed(session)
            session.commit = session.flush  # type: ignore[method-assign]

            async def _db() -> Any:
                yield session

            who = {"u": _user(FULL)}
            app = create_app(Settings(environment="test"))
            app.dependency_overrides[get_db] = _db
            app.dependency_overrides[get_current_user] = lambda: who["u"]
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                yield World(c, session, engine, fake, who)
    finally:
        clear_detect_cache()


@pytest.fixture(params=DIALECTS)
def dialect(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
async def w(request: pytest.FixtureRequest, dialect: str) -> AsyncIterator[World]:
    async with world_for(request, dialect) as world:
        yield world


async def test_summary_disabled_when_off_or_setup_missing(
    request: pytest.FixtureRequest, dialect: str
) -> None:
    async with world_for(request, dialect, mode="off") as off:
        with count_statements(off.engine) as seen:
            body = await off.get()
        assert body["enabled"] is False and body["mode"] == "off"
        assert body["role"] is None and body["service"] is None and body["ci_count"] is None
        assert body["dialogs"] == {"itsm_fields": False, "decision": False}
        # settings, ticket row, permission check, mode: nothing else runs
        assert len(seen) <= 12, len(seen)
        assert not any("dynamic_field" in q or "link_relation" in q for q in seen)
    async with world_for(request, dialect) as w:
        assert (await w.get())["enabled"] is True
        # a missing matrix field (the setup is incomplete) disables the whole summary
        await w.session.execute(
            text("UPDATE dynamic_field SET valid_id = 2 WHERE name = 'ITSMImpact'")
        )
        body = await w.get()
        assert body["enabled"] is False and body["mode"] == "znuny_readthrough"
        assert body["criticality"] is None and body["major_banner"] is None


async def test_summary_incident_with_service_state_and_matrix(w: World) -> None:
    await w.set_field(T, "ITSMCriticality", "5 very high")
    await w.set_field(T, "ITSMImpact", "4 high")
    ref = w.fake.add_ci(14, "srv-mail-03.example.org", OPERATIONAL)
    await w.fake.link_ticket(w.session, ticket_id=T, ref=ref, link_type="RelevantTo", user_id=FULL)
    suggestion = (
        await w.client.get(
            "/api/v1/itsm/priority-suggestion",
            params={"criticality": "5 very high", "impact": "4 high"},
        )
    ).json()["priority_id"]
    assert isinstance(suggestion, int)

    body = await w.get()
    assert body["enabled"] is True and body["mode"] == "znuny_readthrough"
    assert body["role"] == "incident"
    assert (body["criticality"], body["impact"]) == ("5 very high", "4 high")
    assert body["matrix_priority_id"] == suggestion
    assert body["service"]["service_id"] == SVC
    assert body["service"]["incident_state"]["state"]["functionality"] == "incident"
    assert body["service"]["incident_state"]["cis_incident"] == 1
    assert body["ci_count"] == 1
    assert body["major_incident"] is None
    assert body["dialogs"] == {"itsm_fields": True, "decision": True}  # ITSM DB: Znuny config
    # an incident without values has no matrix suggestion; the other ticket role differs
    other = await w.get(OTHER)
    assert other["matrix_priority_id"] is None and other["criticality"] is None
    assert (await w.get(PR))["role"] == "problem"


async def test_summary_recent_incident_flood_warning(w: World) -> None:
    await w.configure(min_minutes_between_incidents=60)
    fresh = utc_now().replace(microsecond=0) - timedelta(minutes=5)
    await w.session.execute(
        text("UPDATE ticket SET create_time = :t WHERE id = :i"), {"t": fresh, "i": OTHER}
    )
    body = await w.get()
    assert body["recent_incident"]["ticket_id"] == OTHER
    assert body["recent_incident"]["window_minutes"] == 60
    assert (await w.get(PR))["recent_incident"] is None  # problems get no flood warning
    w.login(NOBODY)
    assert (await w.client.get(f"/api/v1/tickets/{T}/itsm")).status_code == 404


async def _populate(w: World, extra: int, start: int = 0) -> None:
    """``extra`` more linked incidents on the service and ``extra`` more CIs on the ticket."""
    for i in range(start, start + extra):
        tid = 941620 + i
        await w.new_ticket(tid, f"Folgestörung {i}", service_id=SVC)
        await w.link(T, tid)
        ref = w.fake.add_ci(30 + i, f"srv-extra-{i}.example.org", OPERATIONAL)
        await w.fake.link_ticket(
            w.session, ticket_id=T, ref=ref, link_type="RelevantTo", user_id=FULL
        )


async def test_summary_query_count_bounded(w: World) -> None:
    await w.link(T, PR, MAJ, HIDDEN_MAJ)
    await _populate(w, 1)
    await w.summary()  # warm the process-wide caches (package detection), then count
    with count_statements(w.engine) as small:
        first = await w.summary()
    await _populate(w, 8, start=1)  # nine linked incidents and CIs more: still the same statements
    with count_statements(w.engine) as big:
        second = await w.summary()
    assert first.ci_count == 1 and second.ci_count == 9
    assert first.problem is not None and second.problem is not None
    assert len(big) == len(small), "the statement count must not grow with links or CIs"
    assert len(big) <= STATEMENT_BOUND, f"{len(big)} statements"


async def test_summary_problem_and_banner(w: World) -> None:
    await w.link(T, PR, MAJ, HIDDEN_MAJ)
    w.session.add_all(
        [
            TiqoraItsmProblem(
                ticket_id=PR,
                root_cause="Defekter Speicher",
                workaround=None,
                known_error=True,
                change_time=NOW,
                change_by=1,
            ),
            TiqoraItsmMajorIncident(
                ticket_id=MAJ, summary="Strom im Rechenzentrum", declared_at=NOW, declared_by=1
            ),
            TiqoraItsmMajorIncident(
                ticket_id=HIDDEN_MAJ,
                summary="geheim",
                declared_at=NOW + timedelta(hours=1),
                declared_by=1,
            ),
        ]
    )
    await w.session.flush()
    body = await w.get()
    assert body["problem"]["ticket_id"] == PR and body["problem"]["known_error"] is True
    # the newer declaration sits in queue B: the caller cannot read it, so the readable one shows
    assert body["major_banner"]["ticket_id"] == MAJ
    assert body["major_banner"]["summary"] == "Strom im Rechenzentrum"
    assert "geheim" not in str(body) and "Fremder" not in str(body)
    # the major ticket itself: its declaration, no banner for itself
    major = await w.get(MAJ)
    assert major["role"] == "major_incident" and major["major_banner"] is None
    assert major["major_incident"]["active"] is True
    assert major["major_incident"]["summary"] == "Strom im Rechenzentrum"
    # the problem side sees no problem reference of its own
    assert (await w.get(PR))["problem"] is None


async def test_summary_reports_the_two_zoom_switches(w: World) -> None:
    body = await w.get()
    assert body["known_error_category_configured"] is False
    assert body["major_incident_customer_reply"] is False
    await w.configure(known_error_kb_category_id=7, major_incident_customer_reply=True)
    body = await w.get()
    assert body["known_error_category_configured"] is True
    assert body["major_incident_customer_reply"] is True


async def test_ticket_itsm_404_without_ro(w: World) -> None:
    for user in (NOBODY, RO):
        w.login(user)
        got = await w.client.get(f"/api/v1/tickets/{HIDDEN_MAJ}/itsm")
        assert got.status_code == 404 and got.json()["detail"] == {"code": "ticket_not_found"}
    w.login(FULL)
    assert (await w.client.get("/api/v1/tickets/941699/itsm")).status_code == 404
    with pytest.raises(TicketNotReadable):
        await w.summary(HIDDEN_MAJ)


async def test_itsm_routes_map_to_their_api_key_areas() -> None:
    assert path_to_area("/api/v1/itsm/status") == "cmdb"  # cross R11
    assert path_to_area(f"/api/v1/tickets/{T}/itsm") == "tickets"
    assert path_to_area(f"/api/v1/tickets/{T}/link-graph") == "tickets"


@pytest.mark.parametrize(
    ("dialect", "profile"), ITSM_PARAMS, ids=[f"{d}-{p}" for d, p in ITSM_PARAMS]
)
async def test_mode_a_summary_and_graph_end_to_end(
    request: pytest.FixtureRequest, dialect: str, profile: str
) -> None:
    """The real ``ZnunyCiProvider``: link a CI, then summary and graph both show it."""
    url = itsm_url(request, dialect, profile)
    seed_packages(url, profile_packages(profile))
    store(url, SETTING_MODE, "znuny_readthrough")
    set_registry_for_tests(None)
    try:
        async with itsm_session(request, dialect, profile) as (session, _reader, engine):
            env = Env(session, ZnunyCiProvider(SysConfig(session)), profile, dialect, engine)
            seeded = await seed_world(env)
            session.commit = session.flush  # type: ignore[method-assign]

            async def _db() -> Any:
                yield session

            app = create_app(Settings(environment="test"))
            app.dependency_overrides[get_db] = _db
            app.dependency_overrides[get_current_user] = lambda: _user(Z_FULL)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                made = await c.post(
                    f"/api/v1/tickets/{Z_TICKET}/cis",
                    json={"ci": str(seeded.a), "link_type": "RelevantTo"},
                )
                assert made.status_code == 201, made.text
                summary = (await c.get(f"/api/v1/tickets/{Z_TICKET}/itsm")).json()
                assert summary["enabled"] is True and summary["ci_count"] == 1
                assert summary["dialogs"] == {"itsm_fields": True, "decision": True}
                graph = (await c.get(f"/api/v1/tickets/{Z_TICKET}/link-graph")).json()
                assert [n["key"] for n in graph["nodes"]] == [f"ticket:{Z_TICKET}", str(seeded.a)]
                assert graph["edges"][0]["kind"] == "ticket_ci"
    finally:
        clear_detect_cache()
        set_registry_for_tests(None)
