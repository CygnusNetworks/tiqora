"""``/api/v1/itsm`` CI routes and ``/api/v1/tickets/{id}/cis`` (Task 14).

Both dialects. The fake provider covers the route logic (mapping, permissions, paging); one
Mode A test per dialect x schema profile runs the real ``ZnunyCiProvider`` end to end.

Everything a test seeds is flushed in a session that is rolled back at the end (the routes'
``commit`` is turned into ``flush``), so the strict leak check has nothing to find; the mode row
and the profile packages are committed before the session starts and removed by ``prepare``.
Seed band 940900-940999: users 940920-940922, groups 940930/940933, queues 940931/940932,
tickets 940940/940941, dynamic fields/types/states 94095x.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests._itsm_adapter_helpers import itsm_session, seed_user
from tests._itsm_db import ITSM_PARAMS
from tests._itsm_fake_provider import FakeCiProvider
from tests._itsm_mode_helpers import itsm_url, profile_packages, seed_packages, store
from tests.test_itsm_znuny_provider import FULL as Z_FULL
from tests.test_itsm_znuny_provider import TICKET as Z_TICKET
from tests.test_itsm_znuny_provider import Env, seed_world
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.api_key_scopes import path_to_area
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions import Registry, set_registry_for_tests
from tiqora.itsm.mode import SETTING_MODE
from tiqora.itsm.providers import CI_PROVIDERS, CiProviderFactory
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache
from tiqora.itsm.znuny_adapter.provider import ZnunyCiProvider
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 1, 8, 0, 0)  # noqa: DTZ001 - Znuny stores naive UTC
FULL, RO, NOBODY = 940920, 940921, 940922  # rw on queue A / ro on queue A / nothing
GROUP_A, GROUP_B = 940930, 940933
QUEUE_A, QUEUE_B = 940931, 940932
T_A, T_B = 940940, 940941  # ticket in queue A / in queue B (nobody here reads B)
DIALECTS = ["mysql", "postgresql"]


def _user(uid: int) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=uid, login=f"ci94.{uid}", first_name="Ci", last_name="Agent", auth_method="session"
    )


@dataclass
class World:
    client: AsyncClient
    session: AsyncSession
    fake: FakeCiProvider
    as_user: dict[str, AuthenticatedUser]

    def login(self, uid: int) -> None:
        self.as_user["u"] = _user(uid)


async def _grant(session: AsyncSession, user_id: int, group_id: int, *keys: str) -> None:
    for key in keys:
        await session.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by) VALUES (:u, :g, :k, :t, 1, :t, 1)"
            ),
            {"u": user_id, "g": group_id, "k": key, "t": NOW},
        )


async def _seed_access(session: AsyncSession) -> None:
    """FULL: ro+rw on queue A; RO: ro on queue A; NOBODY: nothing. Queue B: a group nobody holds."""
    for uid, login in ((FULL, "ci94-full"), (RO, "ci94-ro"), (NOBODY, "ci94-nobody")):
        await seed_user(session, uid, login, "Ci", login)
    for gid, name in ((GROUP_A, "ci94-a"), (GROUP_B, "ci94-b")):
        await session.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)"
            ),
            {"id": gid, "n": name, "t": NOW},
        )
    await _grant(session, FULL, GROUP_A, "ro", "rw")
    await _grant(session, RO, GROUP_A, "ro")
    for qid, gid in ((QUEUE_A, GROUP_A), (QUEUE_B, GROUP_B)):
        await session.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": qid, "n": f"ci94-q{qid}", "g": gid, "t": NOW},
        )
    for tid, qid in ((T_A, QUEUE_A), (T_B, QUEUE_B)):
        await session.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
                " responsible_user_id, ticket_priority_id, ticket_state_id, timeout, until_time,"
                " escalation_time, escalation_update_time, escalation_response_time,"
                " escalation_solution_time, archive_flag, create_time, create_by, change_time,"
                " change_by) VALUES (:id, :tn, 'ci94 ticket', :q, 1, 1, 1, 1, 3, 1, 0, 0, 0, 0,"
                " 0, 0, 0, :t, 1, :t, 1)"
            ),
            {"id": tid, "tn": f"20261001{tid}", "q": qid, "t": NOW},
        )


@asynccontextmanager
async def _client(
    session: AsyncSession, user: dict[str, AuthenticatedUser]
) -> AsyncIterator[AsyncClient]:
    async def _db() -> Any:
        yield session

    # The routes commit; a commit would persist the seed rows. The test owns the transaction.
    session.commit = session.flush  # type: ignore[method-assign]
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user["u"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@asynccontextmanager
async def fake_world(
    request: pytest.FixtureRequest, dialect: str, *, mode: str = "znuny_readthrough"
) -> AsyncIterator[World]:
    """The fake provider behind ``znuny_readthrough``: CIs 11 (4 versions), 12 (incident) and
    13 (hidden from ``RO``)."""
    url = itsm_url(request, dialect, "itsm-6")
    seed_packages(url, profile_packages("itsm-6"))
    store(url, SETTING_MODE, mode)
    fake = FakeCiProvider()
    fake.add_ci(11, "fake-web-01", _state("1"), versions=4)
    fake.add_ci(12, "fake-web-02", _state("3"))
    fake.add_ci(13, "fake-web-hidden", _state("1"), hidden_for=[RO])
    registry = Registry()
    registry.add(CI_PROVIDERS, CiProviderFactory(mode="znuny_readthrough", create=lambda _s: fake))
    registry.freeze()
    set_registry_for_tests(registry)
    request.addfinalizer(lambda: set_registry_for_tests(None))
    try:
        async with itsm_session(request, dialect, "itsm-6") as (session, _reader, _engine):
            await _seed_access(session)
            who = {"u": _user(FULL)}
            async with _client(session, who) as client:
                yield World(client, session, fake, who)
    finally:
        clear_detect_cache()


def _state(key: str) -> Any:
    from tests._itsm_fake_provider import INCIDENT, OPERATIONAL

    return INCIDENT if key == "3" else OPERATIONAL


@pytest.fixture(params=DIALECTS)
def dialect(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
async def world(request: pytest.FixtureRequest, dialect: str) -> AsyncIterator[World]:
    async with fake_world(request, dialect) as w:
        yield w


# --- status --------------------------------------------------------------------------------------


async def test_status_without_gate(request: pytest.FixtureRequest, dialect: str) -> None:
    async with fake_world(request, dialect, mode="off") as w:
        got = await w.client.get("/api/v1/itsm/status")
    assert got.status_code == 200
    body = got.json()
    assert body["mode"] == "off" and body["source"] == "auto"  # no meta row: written by the test
    assert body["ci_available"] is False and body["ci_source"] is None
    assert body["unavailable_code"] is None
    assert body["znuny"]["core_installed"] is True  # the database has the packages; the mode is off
    assert body["znuny"]["profile"] == "itsm-6"
    assert body["znuny"]["packages"]["ITSMCore"]


async def test_status_cmdb_not_installed(request: pytest.FixtureRequest, dialect: str) -> None:
    """Mode B before the CMDB plan registers a provider: unavailable, not an error."""
    async with fake_world(request, dialect, mode="tiqora") as w:
        got = await w.client.get("/api/v1/itsm/status")
        assert got.status_code == 200
        body = got.json()
        assert body["mode"] == "tiqora" and body["ci_available"] is False
        assert body["unavailable_code"] == "cmdb_not_installed" and body["ci_source"] is None
        refused = await w.client.get("/api/v1/itsm/cis")
    assert refused.status_code == 409 and refused.json()["detail"] == {"code": "cmdb_not_installed"}


async def test_status_mode_a_fake_provider(world: World) -> None:
    body = (await world.client.get("/api/v1/itsm/status")).json()
    assert body["mode"] == "znuny_readthrough" and body["ci_available"] is True
    assert body["ci_source"] == "znuny" and body["ci_read_only"] is False
    assert body["unavailable_code"] is None


async def test_status_setup_complete_follows_the_seeded_rows(world: World) -> None:
    """The ITSM database ships types, state and fields: complete until one is invalid."""
    c, s = world.client, world.session
    assert (await c.get("/api/v1/itsm/status")).json()["setup_complete"] is True
    for table, name in (
        ("dynamic_field", "ITSMImpact"),
        ("ticket_state", "closed with workaround"),
        ("ticket_type", "Problem"),
    ):
        await s.execute(text(f"UPDATE {table} SET valid_id = 2 WHERE name = :n"), {"n": name})  # noqa: S608
        assert (await c.get("/api/v1/itsm/status")).json()["setup_complete"] is False, table
        await s.execute(text(f"UPDATE {table} SET valid_id = 1 WHERE name = :n"), {"n": name})  # noqa: S608
        assert (await c.get("/api/v1/itsm/status")).json()["setup_complete"] is True, table


async def test_status_names_the_matrix_fields(world: World) -> None:
    """Forms suggest the matrix priority from these fields, so a rename must reach them."""
    body = (await world.client.get("/api/v1/itsm/status")).json()
    assert (body["criticality_field"], body["impact_field"]) == ("ITSMCriticality", "ITSMImpact")
    world.session.add_all(
        [
            TiqoraSettings(key="itsm.criticality_field", value="SiteCriticality"),
            TiqoraSettings(key="itsm.impact_field", value="SiteImpact"),
        ]
    )
    await world.session.flush()
    body = (await world.client.get("/api/v1/itsm/status")).json()
    assert (body["criticality_field"], body["impact_field"]) == ("SiteCriticality", "SiteImpact")


# --- catalog, search, detail -------------------------------------------------------------------


async def test_catalog(world: World) -> None:
    body = (await world.client.get("/api/v1/itsm/catalog")).json()
    assert [c["name"] for c in body["classes"]] == ["Computer"]
    assert [v["functionality"] for v in body["incident_states"]] == [
        "operational",
        "warning",
        "incident",
    ]
    assert [v["functionality"] for v in body["deployment_states"]] == ["productive"]


async def test_search_and_detail(world: World) -> None:
    c = world.client
    page = (await c.get("/api/v1/itsm/cis", params={"q": "fake-web", "limit": 2})).json()
    assert page["total"] == 3 and page["truncated"] is False
    assert [i["ref"] for i in page["items"]] == ["znuny:11", "znuny:12"]
    first = page["items"][0]
    assert first["source"] == "znuny" and first["id"] == 11 and first["name"] == "fake-web-01"
    assert first["incident_state"]["functionality"] == "operational"
    assert first["changed_at"].endswith("+00:00")
    rest = (
        await c.get("/api/v1/itsm/cis", params={"q": "fake-web", "limit": 2, "offset": 2})
    ).json()
    assert [i["ref"] for i in rest["items"]] == ["znuny:13"]
    assert (await c.get("/api/v1/itsm/cis", params={"q": "nothing-like-it"})).json()["total"] == 0
    assert (await c.get("/api/v1/itsm/cis", params={"limit": 101})).status_code == 422
    detail = await c.get("/api/v1/itsm/cis/znuny/12")
    assert detail.status_code == 200
    body = detail.json()
    assert body["summary"]["ref"] == "znuny:12" and body["read_only"] is False
    assert body["summary"]["incident_state"]["name"] == "Incident"
    assert body["attributes"] == [] and body["version"] is None


async def test_detail_unreadable_is_404(world: World) -> None:
    c = world.client
    world.login(RO)
    hidden = await c.get("/api/v1/itsm/cis/znuny/13")
    missing = await c.get("/api/v1/itsm/cis/znuny/99999")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json() == {"detail": {"code": "ci_not_found"}}  # no leak
    assert (await c.get("/api/v1/itsm/cis/znuny/13/versions")).status_code == 404
    assert (await c.get("/api/v1/itsm/cis/znuny/13/tickets")).status_code == 404
    assert (await c.get("/api/v1/itsm/cis/tiqora/11")).status_code == 404  # the other source
    assert (await c.get("/api/v1/itsm/cis/bogus/11")).status_code == 422
    hits = (await c.get("/api/v1/itsm/cis", params={"q": "fake-web"})).json()
    assert [i["ref"] for i in hits["items"]] == ["znuny:11", "znuny:12"]


async def test_versions_paging(world: World) -> None:
    c = world.client
    first = (await c.get("/api/v1/itsm/cis/znuny/11/versions", params={"limit": 3})).json()
    assert first["total"] == 4 and [v["seq"] for v in first["items"]] == [4, 3, 2]
    assert first["items"][0]["created_at"].endswith("+00:00")
    last = (
        await c.get("/api/v1/itsm/cis/znuny/11/versions", params={"limit": 3, "offset": 3})
    ).json()
    assert [v["seq"] for v in last["items"]] == [1] and last["total"] == 4


async def test_ci_tickets_filtered_by_ro(world: World) -> None:
    world.fake._links.update({(T_A, 11, "RelevantTo"), (T_B, 11, "RelevantTo"), (T_A, 12, "x")})
    got = await world.client.get("/api/v1/itsm/cis/znuny/11/tickets")
    assert got.status_code == 200
    rows = got.json()
    assert [r["other_ticket_id"] for r in rows] == [T_A]  # the ticket in queue B is not readable
    row = rows[0]
    assert row["link_type"] == "RelevantTo" and row["target_key"] == "znuny:11"
    assert row["other_title"] == "ci94 ticket" and row["other_tn"] == f"20261001{T_A}"
    assert row["other_state"] and row["other_state_type"]
    world.login(NOBODY)
    assert (await world.client.get("/api/v1/itsm/cis/znuny/11/tickets")).json() == []


# --- ticket <-> CI --------------------------------------------------------------------------------


async def test_ticket_cis_list_link_unlink(world: World) -> None:
    c = world.client
    base = f"/api/v1/tickets/{T_A}/cis"
    assert (await c.get(base)).json() == []
    made = await c.post(base, json={"ci": "znuny:11"})
    assert made.status_code == 201
    link = made.json()
    assert link["link_type"] == "RelevantTo"  # ItsmSettings.ci_ticket_link_type
    assert link["ci"]["ref"] == "znuny:11" and link["ticket_id"] == T_A
    again = await c.post(base, json={"ci": "znuny:11"})  # idempotent
    assert again.status_code == 201
    listed = (await c.get(base)).json()
    assert [(x["ci"]["ref"], x["link_type"], x["direction"]) for x in listed] == [
        ("znuny:11", "RelevantTo", "both")
    ]
    assert world.fake._links == {(T_A, 11, "RelevantTo")}
    assert (await c.post(base, json={"ci": "bogus"})).json()["detail"] == {"code": "invalid_ci_ref"}
    world.login(RO)
    assert (await c.post(base, json={"ci": "znuny:13"})).status_code == 403  # rw first
    world.login(FULL)
    gone = await c.delete(f"{base}/znuny/11")
    assert gone.status_code == 204 and gone.content == b""
    assert (await c.get(base)).json() == [] and world.fake._links == set()
    assert (await c.delete(f"{base}/znuny/11")).status_code == 204  # idempotent
    assert (await c.post(base, json={"ci": "znuny:99999"})).status_code == 404


async def test_unlink_one_link_type_only(world: World) -> None:
    world.fake._links.update({(T_A, 11, "RelevantTo"), (T_A, 11, "DependsOn")})
    resp = await world.client.delete(
        f"/api/v1/tickets/{T_A}/cis/znuny/11", params={"link_type": "DependsOn"}
    )
    assert resp.status_code == 204 and world.fake._links == {(T_A, 11, "RelevantTo")}


async def test_link_requires_rw_on_ticket(world: World) -> None:
    c = world.client
    base = f"/api/v1/tickets/{T_A}/cis"
    world.login(RO)  # ro on the ticket, ro on the CI
    refused = await c.post(base, json={"ci": "znuny:11"})
    assert refused.status_code == 403 and refused.json()["detail"] == {"code": "forbidden"}
    assert (await c.delete(f"{base}/znuny/11")).status_code == 403
    assert world.fake._links == set()
    assert (await c.get(base)).status_code == 200  # reading needs ro only


@pytest.mark.parametrize("verb", ["get", "post", "delete"])
async def test_ticket_cis_404_without_ro(world: World, verb: str) -> None:
    """No leak through the root: a ticket the user cannot read answers like a missing one."""
    base = "/api/v1/tickets/{}/cis"
    suffix = "/znuny/11" if verb == "delete" else ""
    kwargs: dict[str, Any] = {"json": {"ci": "znuny:11"}} if verb == "post" else {}
    world.login(NOBODY)
    denied = await world.client.request(verb, base.format(T_A) + suffix, **kwargs)
    missing = await world.client.request(verb, base.format(940999) + suffix, **kwargs)
    queue_b = await world.client.request(verb, base.format(T_B) + suffix, **kwargs)
    assert denied.status_code == missing.status_code == queue_b.status_code == 404
    assert denied.json() == missing.json() == {"detail": {"code": "ticket_not_found"}}
    assert world.fake._links == set()


async def test_link_type_must_be_offered(world: World) -> None:
    c = world.client
    base = f"/api/v1/tickets/{T_A}/cis"
    refused = await c.post(base, json={"ci": "znuny:11", "link_type": "Normal"})
    assert refused.status_code == 422
    assert refused.json()["detail"] == {"code": "link_type_not_offered", "link_type": "Normal"}
    assert world.fake._links == set()
    ok = await c.post(base, json={"ci": "znuny:11", "link_type": "RelevantTo"})
    assert ok.status_code == 201


async def test_off_returns_409(request: pytest.FixtureRequest, dialect: str) -> None:
    async with fake_world(request, dialect, mode="off") as w:
        c = w.client
        for method, path in (
            ("get", "/api/v1/itsm/catalog"),
            ("get", "/api/v1/itsm/cis"),
            ("get", "/api/v1/itsm/cis/znuny/11"),
            ("get", "/api/v1/itsm/cis/znuny/11/versions"),
            ("get", "/api/v1/itsm/cis/znuny/11/tickets"),
            ("get", f"/api/v1/tickets/{T_A}/cis"),
            ("delete", f"/api/v1/tickets/{T_A}/cis/znuny/11"),
        ):
            got = await c.request(method, path)
            assert got.status_code == 409, path
            assert got.json()["detail"] == {"code": "itsm_off"}, path
        posted = await c.post(f"/api/v1/tickets/{T_A}/cis", json={"ci": "znuny:11"})
        assert posted.status_code == 409 and posted.json()["detail"] == {"code": "itsm_off"}
        assert w.fake._links == set()


async def test_unavailable_provider_maps_to_409_code(
    request: pytest.FixtureRequest, dialect: str
) -> None:
    """``CiUnavailable`` becomes 409 with its code, and the status route reports it."""
    from tiqora.itsm.ci_provider import CiUnavailable

    async with fake_world(request, dialect) as w:

        async def _boom(*_a: Any, **_k: Any) -> Any:
            raise CiUnavailable("unsupported_znuny_itsm_version", "7.4.0 not surveyed")

        w.fake.search = _boom  # type: ignore[method-assign]
        w.fake.is_empty = _boom  # type: ignore[method-assign]
        refused = await w.client.get("/api/v1/itsm/cis")
        assert refused.status_code == 409
        assert refused.json()["detail"] == {"code": "unsupported_znuny_itsm_version"}
        status = (await w.client.get("/api/v1/itsm/status")).json()
    assert status["ci_available"] is False
    assert status["unavailable_code"] == "unsupported_znuny_itsm_version"


# --- API-key area (R11) ---------------------------------------------------------------------------


def test_routes_map_to_the_cmdb_and_tickets_areas() -> None:
    app = create_app(Settings(environment="test"))
    paths = set(app.openapi()["paths"])  # FastAPI wraps included routers
    itsm = {p for p in paths if p.startswith("/api/v1/itsm/")}
    assert {"/api/v1/itsm/status", "/api/v1/itsm/cis", "/api/v1/itsm/cis/{source}/{ci_id}"} <= itsm
    assert {path_to_area(p) for p in itsm} == {"cmdb"}
    assert "/api/v1/tickets/{ticket_id}/cis" in paths
    assert path_to_area("/api/v1/tickets/{ticket_id}/cis") == "tickets"
    assert path_to_area("/api/v1/tickets/12/cis/znuny/3") == "tickets"


# --- Mode A end to end ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dialect", "profile"), ITSM_PARAMS, ids=[f"{d}-{p}" for d, p in ITSM_PARAMS]
)
async def test_mode_a_end_to_end(
    request: pytest.FixtureRequest, dialect: str, profile: str
) -> None:
    """search -> detail -> link -> the ticket's list shows it -> the CI's tickets -> unlink."""
    url = itsm_url(request, dialect, profile)
    seed_packages(url, profile_packages(profile))
    store(url, SETTING_MODE, "znuny_readthrough")
    set_registry_for_tests(None)  # the real registry: register_core -> ZnunyCiProvider
    try:
        async with itsm_session(request, dialect, profile) as (session, _reader, engine):
            env = Env(session, ZnunyCiProvider(SysConfig(session)), profile, dialect, engine)
            seeded = await seed_world(env)
            who = {"u": _user(Z_FULL)}
            async with _client(session, who) as c:
                status = (await c.get("/api/v1/itsm/status")).json()
                assert status["mode"] == "znuny_readthrough" and status["ci_available"] is True
                assert status["ci_read_only"] is True and status["ci_source"] == "znuny"
                assert status["znuny"]["profile"] == profile and status["znuny"]["cmdb_installed"]

                page = (await c.get("/api/v1/itsm/cis", params={"q": "itsm94-kit"})).json()
                refs = {i["name"]: i["ref"] for i in page["items"]}
                assert refs["itsm94-kit-b"] == str(seeded.b) and page["total"] == 3
                detail = (await c.get(f"/api/v1/itsm/cis/znuny/{seeded.a.id}")).json()
                assert detail["read_only"] is True and detail["summary"]["name"] == "itsm94-kit-a"
                assert detail["version"]["name"] == "itsm94-kit-a"  # the current version
                versions = (await c.get(f"/api/v1/itsm/cis/znuny/{seeded.a.id}/versions")).json()
                assert versions["total"] == 3

                base = f"/api/v1/tickets/{Z_TICKET}/cis"
                made = await c.post(base, json={"ci": str(seeded.a), "link_type": "DependsOn"})
                assert made.status_code == 201, made.text
                assert made.json()["direction"] == "source"  # the ticket depends on the CI
                listed = (await c.get(base)).json()
                assert [(x["ci"]["ref"], x["link_type"]) for x in listed] == [
                    (str(seeded.a), "DependsOn")
                ]
                from_ci = (await c.get(f"/api/v1/itsm/cis/znuny/{seeded.a.id}/tickets")).json()
                assert [(t["other_ticket_id"], t["link_type"]) for t in from_ci] == [
                    (Z_TICKET, "DependsOn")
                ]
                refused = await c.post(base, json={"ci": str(seeded.a), "link_type": "Normal"})
                assert refused.status_code == 422
                assert (await c.delete(f"{base}/znuny/{seeded.a.id}")).status_code == 204
                assert (await c.get(base)).json() == []
                hidden = await c.get(f"/api/v1/itsm/cis/znuny/{seeded.hidden.id}")
                assert hidden.status_code == 200  # FULL reads the secret class too
                who["u"] = _user(Z_FULL + 1)  # RO: not the secret class, no rw on the ticket
                assert (
                    await c.get(f"/api/v1/itsm/cis/znuny/{seeded.hidden.id}")
                ).status_code == 404
                forbidden = await c.post(base, json={"ci": str(seeded.a)})
                assert forbidden.status_code == 404  # no ro on the ticket's queue at all
    finally:
        set_registry_for_tests(None)
        clear_detect_cache()
