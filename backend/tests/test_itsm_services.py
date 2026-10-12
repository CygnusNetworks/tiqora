"""Service ITSM data, live incident state and the incident flood warning (Task 17), both
dialects. Mode A tests run on both schema profiles (``ITSM_PARAMS``).

Everything is flushed in one rolled-back session (the routes' ``commit`` becomes ``flush``) so the
strict leak check finds nothing. Seed band 941200-941299: services 941201-941204, tickets
941220-941229; the access world (users, queues) is the one of ``test_itsm_ci_api`` (``FULL`` has
ro+rw on queue A; nobody reads queue B).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests._itsm_adapter_helpers import itsm_session, seed_service
from tests._itsm_db import ITSM_PARAMS
from tests._itsm_fake_provider import INCIDENT, OPERATIONAL, FakeCiProvider
from tests._itsm_fixtures import catalog_id
from tests._itsm_mode_helpers import itsm_url, profile_packages, seed_packages, store
from tests.test_itsm_ci_api import FULL, QUEUE_A, QUEUE_B, _seed_access, _user
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions import Registry, set_registry_for_tests
from tiqora.itsm.ci_provider import CiReadOnly
from tiqora.itsm.mode import SETTING_MODE
from tiqora.itsm.models import TiqoraItsmServiceMeta
from tiqora.itsm.providers import CI_PROVIDERS, CiProviderFactory
from tiqora.itsm.services import ServiceMetaIn, save_service_meta, service_info
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache

pytestmark = pytest.mark.db

ADMIN = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)
S_PLAIN, S_TYPED, S_FLOOD, S_OTHER = 941201, 941202, 941203, 941204
T_NEW, T_OLD, T_HIDDEN, T_TASK, T_MAJOR = 941220, 941221, 941222, 941223, 941224
NOW = datetime(2026, 1, 1, 8, 0, 0)  # noqa: DTZ001 - Znuny stores naive UTC
DIALECTS = ["mysql", "postgresql"]
API = "/api/v1/itsm"
ADMIN_API = "/api/v1/admin/itsm"


@dataclass
class World:
    client: AsyncClient
    session: AsyncSession
    url: str
    fake: FakeCiProvider | None


@asynccontextmanager
async def world_for(
    request: pytest.FixtureRequest,
    dialect: str,
    profile: str,
    *,
    mode: str,
    default_minutes: int | None = None,
    fake: FakeCiProvider | None = None,
) -> AsyncIterator[World]:
    url = itsm_url(request, dialect, profile)
    seed_packages(url, profile_packages(profile))
    store(url, SETTING_MODE, mode)
    if default_minutes is not None:
        store(url, "itsm.min_minutes_between_incidents", str(default_minutes))
    if fake is not None:
        registry = Registry()
        registry.add(
            CI_PROVIDERS, CiProviderFactory(mode="znuny_readthrough", create=lambda _s: fake)
        )
        registry.freeze()
        set_registry_for_tests(registry)
        request.addfinalizer(lambda: set_registry_for_tests(None))
    try:
        async with itsm_session(request, dialect, profile) as (session, _reader, _engine):
            await _seed_access(session)

            async def _db() -> Any:
                yield session

            session.commit = session.flush  # type: ignore[method-assign]
            app = create_app(Settings(environment="test"))
            app.dependency_overrides[get_db] = _db
            app.dependency_overrides[get_current_user] = lambda: _user(FULL)
            app.dependency_overrides[get_admin_user] = lambda: ADMIN
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                yield World(c, session, url, fake)
    finally:
        clear_detect_cache()


@pytest.fixture(params=ITSM_PARAMS, ids=lambda p: f"{p[0]}-{p[1]}")
async def mode_a(request: pytest.FixtureRequest) -> AsyncIterator[World]:
    dialect, profile = request.param
    async with world_for(request, dialect, profile, mode="znuny_readthrough") as w:
        yield w


@pytest.fixture(params=DIALECTS)
async def mode_b(request: pytest.FixtureRequest) -> AsyncIterator[World]:
    async with world_for(request, str(request.param), "itsm-6", mode="tiqora") as w:
        yield w


async def _znuny_service(session: AsyncSession) -> tuple[str, str]:
    """A service with a Znuny type and criticality plus an SLA with a minimum time."""
    type_id = await catalog_id(session, "ITSM::Service::Type", "End User Service")
    await seed_service(session, S_TYPED, "svc941 typed")
    await session.execute(
        text("UPDATE service SET type_id = :t, criticality = '3 normal' WHERE id = :i"),
        {"t": type_id, "i": S_TYPED},
    )
    await session.execute(
        text(
            "INSERT INTO sla (id, name, first_response_time, update_time, solution_time, valid_id,"
            " create_time, create_by, change_time, change_by, min_time_bet_incidents)"
            " VALUES (941201, 'svc941 sla', 10, 20, 30, 1, :t, 1, :t, 1, 120)"
        ),
        {"t": NOW},
    )
    return "End User Service", "3 normal"


async def _type_id(session: AsyncSession, name: str) -> int:
    found = (
        await session.execute(text("SELECT id FROM ticket_type WHERE name = :n"), {"n": name})
    ).scalar_one_or_none()
    if found is None:
        await session.execute(
            text(
                "INSERT INTO ticket_type (name, valid_id, create_time, create_by, change_time,"
                " change_by) VALUES (:n, 1, :t, 1, :t, 1)"
            ),
            {"n": name, "t": NOW},
        )
        found = (
            await session.execute(text("SELECT id FROM ticket_type WHERE name = :n"), {"n": name})
        ).scalar_one()
    return int(found)


async def _ticket(
    session: AsyncSession,
    ticket_id: int,
    *,
    service_id: int,
    type_name: str,
    minutes_ago: int,
    queue_id: int = QUEUE_A,
) -> None:
    created = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=minutes_ago)
    await session.execute(
        text(
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, service_id,"
            " user_id, responsible_user_id, ticket_priority_id, ticket_state_id, timeout,"
            " until_time, escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, create_time, create_by, change_time,"
            " change_by) VALUES (:id, :tn, :ti, :q, 1, :ty, :s, 1, 1, 3, 1, 0, 0, 0, 0, 0, 0, 0,"
            " :c, 1, :c, 1)"
        ),
        {
            "id": ticket_id,
            "tn": f"20261001{ticket_id}",
            "ti": f"flood {ticket_id}",
            "q": queue_id,
            "ty": await _type_id(session, type_name),
            "s": service_id,
            "c": created,
        },
    )


async def _meta(w: World, service_id: int, **fields: Any) -> None:
    body = {"portal_visible": True, **fields}
    got = await w.client.put(f"{ADMIN_API}/services/{service_id}/meta", json=body)
    assert got.status_code == 200, got.text


async def _flood(w: World, service_id: int, **params: Any) -> Any:
    got = await w.client.get(f"{API}/services/{service_id}/recent-incident", params=params)
    assert got.status_code == 200, got.text
    return got.json()


# --- meta ----------------------------------------------------------------------------------------


async def test_mode_a_type_and_criticality_from_service_columns(mode_a: World) -> None:
    await _znuny_service(mode_a.session)
    got = await mode_a.client.get(f"{API}/services/{S_TYPED}")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["znuny_owned"] is True
    assert (body["service_type"], body["criticality"]) == ("End User Service", "3 normal")
    assert body["znuny_sla_min_times"] == {"941201": 120}
    assert body["incident_state"]["state"] is None  # no CI linked to the service
    await seed_service(mode_a.session, S_PLAIN, "svc941 plain")
    plain = (await mode_a.client.get(f"{API}/services/{S_PLAIN}")).json()
    assert (plain["service_type"], plain["criticality"]) == (None, None)
    missing = await mode_a.client.get(f"{API}/services/941299")
    assert missing.status_code == 404 and missing.json()["detail"] == {"code": "service_not_found"}


async def test_mode_a_meta_type_change_rejected(mode_a: World) -> None:
    z_type, z_crit = await _znuny_service(mode_a.session)
    url = f"{ADMIN_API}/services/{S_TYPED}/meta"
    refused = await mode_a.client.put(url, json={"service_type": "Other"})
    assert refused.status_code == 409 and refused.json()["detail"] == {"code": "read_only"}
    refused = await mode_a.client.put(url, json={"criticality": "1 very low"})
    assert refused.status_code == 409
    # echoing Znuny's own values is harmless and the Tiqora-side fields still save
    await _meta(mode_a, S_TYPED, service_type=z_type, criticality=z_crit, description="web shop")
    row = await mode_a.session.get(TiqoraItsmServiceMeta, S_TYPED)
    assert row is not None and row.description == "web shop"
    assert (row.service_type, row.criticality) == (None, None)  # Znuny stays the owner
    shown = (await mode_a.client.get(url)).json()
    assert (shown["service_type"], shown["description"]) == (z_type, "web shop")


async def test_tiqora_meta_round_trip(mode_b: World) -> None:
    await seed_service(mode_b.session, S_PLAIN, "svc941 plain")
    await _meta(
        mode_b,
        S_PLAIN,
        service_type="Customer-facing",
        criticality="4 high",
        owner_user_id=FULL,
        description="the shop",
        portal_visible=False,
        min_minutes_between_incidents=30,
    )
    for path in (f"{ADMIN_API}/services/{S_PLAIN}/meta", f"{API}/services/{S_PLAIN}"):
        body = (await mode_b.client.get(path)).json()
        assert body["znuny_owned"] is False
        assert (body["service_type"], body["criticality"]) == ("Customer-facing", "4 high")
        assert (body["owner_user_id"], body["owner_name"]) == (FULL, "Ci ci94-full")
        assert (body["description"], body["portal_visible"]) == ("the shop", False)
        assert body["min_minutes_between_incidents"] == 30
        assert body["effective_min_minutes_between_incidents"] == 30
        assert body["znuny_sla_min_times"] == {}
    # PUT replaces: omitted fields are cleared
    await _meta(mode_b, S_PLAIN)
    cleared = (await mode_b.client.get(f"{API}/services/{S_PLAIN}")).json()
    assert cleared["service_type"] is None and cleared["owner_user_id"] is None
    assert cleared["portal_visible"] is True
    unknown = await mode_b.client.put(f"{ADMIN_API}/services/941299/meta", json={})
    assert unknown.status_code == 404


async def test_import_mode_bypass_for_itsm_import(mode_a: World) -> None:
    await _znuny_service(mode_a.session)
    data = ServiceMetaIn(service_type="Imported Type", criticality="5 very high")
    with pytest.raises(CiReadOnly):
        await save_service_meta(mode_a.session, S_TYPED, data, user_id=1)
    await save_service_meta(mode_a.session, S_TYPED, data, user_id=1, import_mode=True)
    row = await mode_a.session.get(TiqoraItsmServiceMeta, S_TYPED)
    assert row is not None
    assert (row.service_type, row.criticality) == ("Imported Type", "5 very high")
    # still Mode A: the live read keeps showing Znuny's columns
    info = await service_info(mode_a.session, S_TYPED, user_id=1)
    assert (info.service_type, info.criticality) == ("End User Service", "3 normal")


# --- incident state ------------------------------------------------------------------------------


@pytest.fixture(params=DIALECTS)
async def with_fake(request: pytest.FixtureRequest) -> AsyncIterator[World]:
    fake = FakeCiProvider()
    fake.add_ci(11, "fake-web-01", OPERATIONAL)
    fake.add_ci(12, "fake-web-02", INCIDENT)
    fake.link_service(S_PLAIN, 11)
    fake.link_service(S_PLAIN, 12)
    fake.link_service(S_OTHER, 11)
    async with world_for(
        request, str(request.param), "itsm-6", mode="znuny_readthrough", fake=fake
    ) as w:
        await seed_service(w.session, S_PLAIN, "svc941 plain")
        await seed_service(w.session, S_OTHER, "svc941 other")
        yield w


async def test_incident_state_via_provider(with_fake: World) -> None:
    body = (await with_fake.client.get(f"{API}/services/{S_PLAIN}")).json()
    state = body["incident_state"]
    assert state["state"]["functionality"] == "incident"
    assert (state["cis_incident"], state["cis_warning"]) == (1, 0)
    batch = await with_fake.client.get(
        f"{API}/services/incident-states", params={"ids": [S_PLAIN, S_OTHER]}
    )
    assert batch.status_code == 200, batch.text
    got = batch.json()
    assert got[str(S_PLAIN)]["state"]["functionality"] == "incident"
    assert got[str(S_OTHER)]["state"]["functionality"] == "operational"


async def test_incident_states_batch_limit(with_fake: World) -> None:
    ok = await with_fake.client.get(
        f"{API}/services/incident-states", params={"ids": list(range(1, 201))}
    )
    assert ok.status_code == 200
    too_many = await with_fake.client.get(
        f"{API}/services/incident-states", params={"ids": list(range(1, 202))}
    )
    assert too_many.status_code == 422


# --- recent incident -----------------------------------------------------------------------------


async def test_recent_incident_window_and_type(mode_b: World) -> None:
    s = mode_b.session
    await seed_service(s, S_FLOOD, "svc941 flood")
    await _ticket(s, T_OLD, service_id=S_FLOOD, type_name="Incident", minutes_ago=300)
    await _ticket(s, T_TASK, service_id=S_FLOOD, type_name="Change::Task", minutes_ago=2)
    await _ticket(s, T_NEW, service_id=S_FLOOD, type_name="Incident", minutes_ago=20)
    await _ticket(s, T_MAJOR, service_id=S_FLOOD, type_name="Incident::Major", minutes_ago=5)
    assert await _flood(mode_b, S_FLOOD) is None  # default 0 = off
    await _meta(mode_b, S_FLOOD, min_minutes_between_incidents=60)
    hit = await _flood(mode_b, S_FLOOD)
    assert hit["ticket_id"] == T_MAJOR and hit["type_name"] == "Incident::Major"
    assert hit["window_minutes"] == 60 and 4 <= hit["minutes_ago"] <= 6
    hit = await _flood(mode_b, S_FLOOD, exclude_ticket_id=T_MAJOR)
    assert hit["ticket_id"] == T_NEW  # the change task and the 300 minute old ticket never match
    await _meta(mode_b, S_FLOOD, min_minutes_between_incidents=10)
    assert (await _flood(mode_b, S_FLOOD, exclude_ticket_id=T_MAJOR)) is None
    other = await _flood(mode_b, S_OTHER)
    assert other is None


async def test_recent_incident_ro_filtered(mode_b: World) -> None:
    s = mode_b.session
    await seed_service(s, S_FLOOD, "svc941 flood")
    await _ticket(s, T_OLD, service_id=S_FLOOD, type_name="Incident", minutes_ago=30)
    await _ticket(
        s, T_HIDDEN, service_id=S_FLOOD, type_name="Incident", minutes_ago=3, queue_id=QUEUE_B
    )
    await _meta(mode_b, S_FLOOD, min_minutes_between_incidents=60)
    hit = await _flood(mode_b, S_FLOOD)
    assert hit["ticket_id"] == T_OLD  # the newer ticket sits in a queue FULL cannot read
    only_hidden = await _flood(mode_b, S_FLOOD, exclude_ticket_id=T_OLD)
    assert only_hidden is None


@pytest.fixture(params=DIALECTS)
async def default_45(request: pytest.FixtureRequest) -> AsyncIterator[World]:
    async with world_for(
        request, str(request.param), "itsm-6", mode="tiqora", default_minutes=45
    ) as w:
        yield w


async def test_service_default_from_settings(default_45: World) -> None:
    s = default_45.session
    await seed_service(s, S_FLOOD, "svc941 flood")
    await seed_service(s, S_OTHER, "svc941 other")
    await _ticket(s, T_NEW, service_id=S_FLOOD, type_name="Incident", minutes_ago=30)
    await _ticket(s, T_OLD, service_id=S_OTHER, type_name="Incident", minutes_ago=60)
    body = (await default_45.client.get(f"{API}/services/{S_FLOOD}")).json()
    assert body["min_minutes_between_incidents"] is None
    assert body["effective_min_minutes_between_incidents"] == 45
    assert (await _flood(default_45, S_FLOOD))["ticket_id"] == T_NEW
    assert await _flood(default_45, S_OTHER) is None  # 60 minutes is outside the default 45
    await _meta(default_45, S_FLOOD, min_minutes_between_incidents=0)  # own 0 beats the default
    assert await _flood(default_45, S_FLOOD) is None
    await _meta(default_45, S_OTHER, min_minutes_between_incidents=90)
    assert (await _flood(default_45, S_OTHER))["ticket_id"] == T_OLD
