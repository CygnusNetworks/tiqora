"""HTTP tests for ``/api/v1/admin/itsm`` and the ``itsm`` settings section (both dialects)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from tests._itsm_mode_helpers import (
    PLAIN_URL_FIXTURES,
    itsm_url,
    make_factory,
    plain_url,
    profile_packages,
    register_fake_mode_b,
    seed_packages,
    store,
    stored,
)
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.itsm.mode import (
    SETTING_MODE,
    SETTING_SWITCH,
    ensure_itsm_mode_initialised,
    switch_to_tiqora,
)
from tiqora.itsm.settings import ITSM_SECTION, ItsmSettings, load_itsm_settings
from tiqora.settings_sections.section import validate_section_schema

pytestmark = pytest.mark.db

BASE = "/api/v1/admin/itsm"
SECTIONS = "/api/v1/admin/settings-sections/itsm"
ADMIN = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)
AGENT = AuthenticatedUser(
    id=94012, login="itsm94.agent", first_name="Ina", last_name="Agent", auth_method="session"
)
DIALECTS = ["mysql", "postgresql"]


async def _client(url: str, *, admin: bool) -> AsyncIterator[tuple[AsyncClient, Any, Any]]:
    engine, factory = make_factory(url)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    if admin:
        app.dependency_overrides[get_admin_user] = lambda: ADMIN
    else:
        app.dependency_overrides[get_current_user] = lambda: AGENT
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, factory, engine
    await engine.dispose()


async def _itsm_admin(
    request: pytest.FixtureRequest, dialect: str, *, initialise: bool = True
) -> AsyncIterator[tuple[AsyncClient, Any, str]]:
    url = itsm_url(request, dialect, "itsm-6")
    seed_packages(url, profile_packages("itsm-6"))
    async for client, factory, _engine in _client(url, admin=True):
        if initialise:
            await ensure_itsm_mode_initialised(factory)
        yield client, factory, url


@pytest.fixture
async def mode_a(request, dialect: str) -> AsyncIterator[tuple[AsyncClient, Any, str]]:
    async for item in _itsm_admin(request, dialect):
        yield item


@pytest.fixture(params=DIALECTS)
def dialect(request) -> str:
    return request.param


async def test_get_admin_itsm_reports_mode_detection_and_choices(mode_a) -> None:
    client, _factory, _url = mode_a
    body = (await client.get(BASE)).json()
    assert body["mode"] == "znuny_readthrough" and body["source"] == "auto"
    assert body["stored"] is True and body["switched"] is False and body["switch"] is None
    assert body["previous_mode"] is None
    assert body["detected"]["core_installed"] is True
    assert body["detected"]["profile"] == "itsm-6"
    assert body["detected"]["unsupported_reason"] is None
    assert body["detected"]["packages"]["ITSMCore"] == profile_packages("itsm-6")["ITSMCore"]
    assert body["cmdb_empty"] is True
    assert body["allowed_transitions"] == ["off"]  # A -> B is the importer's job


async def test_admin_cannot_switch_a_to_b(mode_a) -> None:
    client, _factory, url = mode_a
    resp = await client.put(f"{BASE}/mode", json={"mode": "tiqora"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == {
        "code": "mode_transition_not_allowed",
        "from": "znuny_readthrough",
        "to": "tiqora",
        "reason": "znuny_to_tiqora_importer_only",
    }
    assert stored(url, SETTING_MODE) == "znuny_readthrough"


async def test_unknown_mode_is_a_validation_error(mode_a) -> None:
    client, _factory, _url = mode_a
    assert (await client.put(f"{BASE}/mode", json={"mode": "banana"})).status_code == 422


async def test_admin_switch_to_off_and_back(mode_a) -> None:
    client, _factory, url = mode_a
    off = await client.put(f"{BASE}/mode", json={"mode": "off"})
    assert off.status_code == 200
    body = off.json()
    assert body["mode"] == "off" and body["source"] == "admin" and body["changed_by"] == 1
    assert body["previous_mode"] == "znuny_readthrough"
    assert body["allowed_transitions"] == ["znuny_readthrough"]  # the mode before, nothing else
    assert stored(url, SETTING_MODE) == "off"

    # off with the stored ITSM mode: B is not reachable through the off loophole
    refused = await client.put(f"{BASE}/mode", json={"mode": "tiqora"})
    assert refused.status_code == 409
    assert refused.json()["detail"]["reason"] == "off_returns_to_previous_mode"

    back = await client.put(f"{BASE}/mode", json={"mode": "znuny_readthrough"})
    assert back.status_code == 200
    assert back.json()["mode"] == "znuny_readthrough" and back.json()["previous_mode"] is None
    assert (await client.get(BASE)).json()["mode"] == "znuny_readthrough"


async def test_same_mode_is_refused(mode_a) -> None:
    client, _factory, _url = mode_a
    resp = await client.put(f"{BASE}/mode", json={"mode": "znuny_readthrough"})
    assert resp.status_code == 409 and resp.json()["detail"]["reason"] == "already_in_mode"


async def test_b_to_a_needs_empty_cmdb(request, dialect) -> None:
    url = itsm_url(request, dialect, "itsm-6")
    seed_packages(url, profile_packages("itsm-6"))
    store(url, SETTING_MODE, "tiqora")  # a site that runs Mode B and still has Znuny ITSM tables
    fake = register_fake_mode_b(request, empty=False)
    async for client, _factory, _engine in _client(url, admin=True):
        body = (await client.get(BASE)).json()
        assert body["mode"] == "tiqora" and body["cmdb_empty"] is False
        assert body["allowed_transitions"] == ["off"]
        refused = await client.put(f"{BASE}/mode", json={"mode": "znuny_readthrough"})
        assert refused.status_code == 409
        assert refused.json()["detail"] == {
            "code": "mode_transition_not_allowed",
            "from": "tiqora",
            "to": "znuny_readthrough",
            "reason": "tiqora_cmdb_not_empty",
        }
        assert stored(url, SETTING_MODE) == "tiqora"

        fake.empty = True  # the CMDB was emptied
        body = (await client.get(BASE)).json()
        assert body["allowed_transitions"] == ["off", "znuny_readthrough"]
        ok = await client.put(f"{BASE}/mode", json={"mode": "znuny_readthrough"})
        assert ok.status_code == 200 and ok.json()["mode"] == "znuny_readthrough"


async def test_b_to_a_needs_detected_znuny_itsm(request, dialect) -> None:
    """A site without Znuny ITSM tables cannot go to Mode A, whatever the CMDB holds."""
    url = plain_url(request, "mariadb_znuny_url" if dialect == "mysql" else "postgres_znuny_url")
    register_fake_mode_b(request, empty=True)
    async for client, factory, _engine in _client(url, admin=True):
        await ensure_itsm_mode_initialised(factory)
        body = (await client.get(BASE)).json()
        assert body["mode"] == "tiqora" and body["detected"]["core_installed"] is False
        assert body["allowed_transitions"] == ["off"]
        resp = await client.put(f"{BASE}/mode", json={"mode": "znuny_readthrough"})
        assert resp.status_code == 409
        assert resp.json()["detail"]["reason"] == "znuny_itsm_not_detected"


async def test_switched_site_reports_the_switch_and_blocks_b_to_a(request, dialect) -> None:
    url = itsm_url(request, dialect, "itsm-6")
    seed_packages(url, profile_packages("itsm-6"))
    register_fake_mode_b(request, empty=True)  # even an empty CMDB: only the importer rolls back
    async for client, factory, _engine in _client(url, admin=True):
        await ensure_itsm_mode_initialised(factory)
        async with factory() as session:
            await switch_to_tiqora(session, user_id=1, import_run_id=7)
            await session.commit()
        body = (await client.get(BASE)).json()
        assert body["mode"] == "tiqora" and body["source"] == "importer"
        assert body["import_run_id"] == 7 and body["switched"] is True
        assert body["switch"]["run_id"] == 7 and body["switch"]["by"] == 1
        assert body["allowed_transitions"] == ["off"]
        resp = await client.put(f"{BASE}/mode", json={"mode": "znuny_readthrough"})
        assert resp.status_code == 409
        assert resp.json()["detail"]["reason"] == "tiqora_to_znuny_importer_only"
        assert stored(url, SETTING_SWITCH) is not None


async def test_unsupported_version_is_reported(request, dialect) -> None:
    url = itsm_url(request, dialect, "itsm-7")
    packages = profile_packages("itsm-7")
    packages["ITSMConfigurationManagement"] = "7.4.0"
    seed_packages(url, packages)
    async for client, _factory, _engine in _client(url, admin=True):
        body = (await client.get(BASE)).json()
        assert body["detected"]["profile"] is None
        assert body["detected"]["unsupported_reason"] == (
            "ITSMConfigurationManagement 7.4.0 not surveyed"
        )


@pytest.mark.parametrize("url_fixture", PLAIN_URL_FIXTURES)
async def test_non_admin_forbidden(url_fixture, request) -> None:
    url = plain_url(request, url_fixture)
    async for client, _factory, _engine in _client(url, admin=False):
        assert (await client.get(BASE)).status_code == 403
        assert (await client.put(f"{BASE}/mode", json={"mode": "off"})).status_code == 403
        assert (await client.get(SECTIONS)).status_code == 403
        assert (await client.put(SECTIONS, json={"values": {}})).status_code == 403
    assert stored(url, SETTING_MODE) is None


# --- settings section ----------------------------------------------------------------------------


@pytest.fixture
async def section_client(request, dialect) -> AsyncIterator[tuple[AsyncClient, Any, str]]:
    url = plain_url(request, "mariadb_znuny_url" if dialect == "mysql" else "postgres_znuny_url")
    async for client, factory, _engine in _client(url, admin=True):
        yield client, factory, url


def test_section_schema_is_renderable_and_leaves_the_mode_keys_alone() -> None:
    validate_section_schema(ITSM_SECTION)
    assert not {"mode", "mode_meta", "switch", "znuny_readonly"} & set(ItsmSettings.model_fields)


async def test_settings_section_defaults_and_validation(section_client) -> None:
    client, factory, url = section_client
    listed = {s["key"]: s for s in (await client.get("/api/v1/admin/settings-sections")).json()}
    assert listed["itsm"]["title_key"] == "admin.settings.itsm.title"

    first = (await client.get(SECTIONS)).json()
    assert first["i18n_prefix"] == "admin.settings.itsm"
    assert first["values"] == ItsmSettings().model_dump()
    assert first["values"]["incident_type"] == "Incident"
    assert first["values"]["major_incident_type"] == "Incident::Major"
    assert first["values"]["workaround_state"] == "closed with workaround"
    assert first["values"]["ci_ticket_link_type"] == "RelevantTo"
    assert first["values"]["major_incident_customer_reply"] is False
    props = first["json_schema"]["properties"]
    assert props["linked_articles_max_tickets"]["maximum"] == 1000
    assert props["min_minutes_between_incidents"]["maximum"] == 10080

    bad = await client.put(
        SECTIONS,
        json={
            "values": {
                "znuny_agent_url": "ftp://znuny.example.org/otrs",
                "linked_articles_max_tickets": 0,
                "min_minutes_between_incidents": 10081,
            }
        },
    )
    assert bad.status_code == 422
    assert set(bad.json()["detail"]["errors"]) == {
        "znuny_agent_url",
        "linked_articles_max_tickets",
        "min_minutes_between_incidents",
    }
    assert (await client.get(SECTIONS)).json()["values"] == first["values"]  # nothing stored

    good = await client.put(
        SECTIONS,
        json={
            "values": {
                "znuny_agent_url": "https://znuny.example.org/otrs",
                "linked_articles_max_tickets": 50,
                "priority_matrix_enabled": False,
                "known_error_kb_category_id": 3,
            }
        },
    )
    assert good.status_code == 200
    values = good.json()["values"]
    assert values["znuny_agent_url"] == "https://znuny.example.org/otrs"
    assert values["linked_articles_max_tickets"] == 50
    assert values["priority_matrix_enabled"] is False and values["known_error_kb_category_id"] == 3
    assert stored(url, "itsm.linked_articles_max_tickets") == "50"
    assert stored(url, "itsm.priority_matrix_enabled") == "0"

    async with factory() as session:
        loaded = await load_itsm_settings(session, Settings(environment="test"))
    assert loaded.linked_articles_max_tickets == 50 and loaded.priority_matrix_enabled is False
    assert loaded.incident_type == "Incident"


async def test_empty_agent_url_is_allowed(section_client) -> None:
    client, _factory, _url = section_client
    resp = await client.put(SECTIONS, json={"values": {"znuny_agent_url": ""}})
    assert resp.status_code == 200 and resp.json()["values"]["znuny_agent_url"] == ""


async def test_the_mode_keys_cannot_be_written_through_the_section(section_client) -> None:
    client, _factory, url = section_client
    for name in ("mode", "mode_meta", "switch", "znuny_readonly"):
        resp = await client.put(SECTIONS, json={"values": {name: "tiqora"}})
        assert resp.status_code == 422, name
        assert name in resp.json()["detail"]["errors"]
    assert stored(url, SETTING_MODE) is None


def test_helper_signatures_are_importable() -> None:
    # guards the names the later tasks import from the mode module
    from tiqora.itsm import mode

    for name in (
        "get_itsm_mode",
        "set_itsm_mode",
        "switch_to_tiqora",
        "rollback_to_znuny",
        "allowed_transitions",
        "ensure_itsm_mode_initialised",
        "detect_znuny_itsm",
        "require_itsm_enabled",
    ):
        assert isinstance(getattr(mode, name), Callable)
