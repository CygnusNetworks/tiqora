"""ITSM setup: preview and apply of types, workaround state and dynamic fields (both dialects)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from tests._itsm_db import ITSM_PARAMS
from tests._itsm_mode_helpers import itsm_url, make_factory
from tests._itsm_setup_helpers import (
    FIELDS,
    STATE,
    TYPES,
    cleaned_plain_url,
    session_factory,
    signal_floor,
    signals_since,
)
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.dynamic_field_config import config_from_yaml
from tiqora.domain.znuny_admin_writes import create_dynamic_field_row
from tiqora.itsm.settings import ItsmSettings
from tiqora.itsm.setup import apply_setup, plan_setup

pytestmark = pytest.mark.db

DIALECTS = ["mysql", "postgresql"]
USER = 1
CORE = {"incident_problem"}
BASE = "/api/v1/admin/itsm/setup"
ADMIN = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)


@pytest.fixture(params=DIALECTS)
def dialect(request) -> str:
    return request.param


def _by_name(items: list) -> dict[str, Any]:
    return {i.name: i for i in items}


async def _plan(url: str, cfg: ItsmSettings | None = None) -> list:
    engine, factory = session_factory(url)
    try:
        async with factory() as session:
            return await plan_setup(session, cfg or ItsmSettings())
    finally:
        await engine.dispose()


async def _apply(url: str, groups: set[str], cfg: ItsmSettings | None = None) -> list:
    engine, factory = session_factory(url)
    try:
        async with factory() as session:
            items = await apply_setup(session, cfg or ItsmSettings(), groups=groups, user_id=USER)
            await session.commit()
            return items
    finally:
        await engine.dispose()


async def _rows(url: str, sql: str) -> list[dict[str, Any]]:
    engine, factory = session_factory(url)
    try:
        async with factory() as session:
            return [dict(r) for r in (await session.execute(text(sql))).mappings().all()]
    finally:
        await engine.dispose()


async def test_plan_on_plain_db_all_missing(request, dialect: str) -> None:
    url = cleaned_plain_url(request, dialect)
    items = await _plan(url)
    assert {i.status for i in items} == {"missing"}
    assert len(items) == len(TYPES) + 1 + len(FIELDS)
    by_group = {g: [i for i in items if i.group == g] for g in ("incident_problem", "change")}
    assert [i.name for i in by_group["change"]] == ["Change", "Change::Task"]
    assert len(by_group["incident_problem"]) == 4 + 1 + len(FIELDS)


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_plan_on_itsm_db_all_present(request, dialect: str, profile: str) -> None:
    """The packages created everything the incident/problem group needs; apply is a no-op."""
    url = itsm_url(request, dialect, profile)
    items = await _plan(url)
    core = [i for i in items if i.group == "incident_problem"]
    assert core and {i.status for i in core} == {"present"}, [
        i for i in core if i.status != "present"
    ]
    # the change types are not part of what the packages create
    assert {i.status for i in items if i.group == "change"} == {"missing"}
    before = await _rows(url, "SELECT count(*) AS n FROM dynamic_field")
    after_items = await _apply(url, CORE)
    assert after_items == items
    assert await _rows(url, "SELECT count(*) AS n FROM dynamic_field") == before


async def test_conflict_detected_not_changed(request, dialect: str) -> None:
    url = cleaned_plain_url(request, dialect)
    engine, factory = session_factory(url)
    try:
        async with factory() as session:
            await create_dynamic_field_row(
                session,
                name="ITSMImpact",
                label="Mine",
                field_type="Text",
                object_type="Ticket",
                config={"DefaultValue": "x"},
                field_order=910,
                user_id=USER,
            )
            await session.commit()
    finally:
        await engine.dispose()
    before = (await _rows(url, "SELECT * FROM dynamic_field WHERE name = 'ITSMImpact'"))[0]

    plan = _by_name(await _plan(url))
    assert plan["ITSMImpact"].status == "conflict"
    assert "Text" in (plan["ITSMImpact"].detail or "")
    after_items = _by_name(await _apply(url, CORE))
    assert after_items["ITSMImpact"].status == "conflict"
    assert after_items["ITSMCriticality"].status == "present"  # the rest was created
    after = (await _rows(url, "SELECT * FROM dynamic_field WHERE name = 'ITSMImpact'"))[0]
    assert after == before


async def test_invalid_row_reported(request, dialect: str) -> None:
    url = cleaned_plain_url(request, dialect)
    engine, _factory = make_factory(url)
    await engine.dispose()
    from sqlalchemy import create_engine

    sync = create_engine(url)
    try:
        with sync.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO ticket_type (name, valid_id, create_time, create_by,"
                    " change_time, change_by) VALUES ('Problem', 2, '2000-01-01 00:00:00', 1,"
                    " '2000-01-01 00:00:00', 1)"
                )
            )
    finally:
        sync.dispose()
    plan = _by_name(await _plan(url))
    assert plan["Problem"].status == "invalid" and "valid_id" in (plan["Problem"].detail or "")
    after = _by_name(await _apply(url, CORE))
    assert after["Problem"].status == "invalid"
    rows = await _rows(url, "SELECT name, valid_id FROM ticket_type WHERE name = 'Problem'")
    assert rows == [{"name": "Problem", "valid_id": 2}]  # neither duplicated nor revalidated


@pytest.mark.parametrize("dialect", DIALECTS)
async def test_apply_creates_znuny_identical_rows(request, dialect: str) -> None:
    """Compare with the rows the Znuny ITSM packages created (the Task 2 fixture databases)."""
    url = cleaned_plain_url(request, dialect)
    peer = itsm_url(request, dialect, "itsm-6")
    items = await _apply(url, CORE)
    assert {i.status for i in items if i.group == "incident_problem"} == {"present"}

    type_sql = (
        "SELECT name, valid_id FROM ticket_type WHERE name IN"
        " ('Incident', 'Incident::Major', 'ServiceRequest', 'Problem') ORDER BY name"
    )
    assert await _rows(url, type_sql) == await _rows(peer, type_sql)

    state_sql = (
        "SELECT s.name AS name, t.name AS type_name, s.comments AS comments, s.valid_id AS valid_id"
        f" FROM ticket_state s, ticket_state_type t WHERE s.type_id = t.id AND s.name = '{STATE}'"
    )
    states = await _rows(url, state_sql)
    assert states == await _rows(peer, state_sql) and len(states) == 1

    field_sql = (
        "SELECT name, label, field_type, object_type, internal_field, valid_id, config"
        f" FROM dynamic_field WHERE name IN ({', '.join(repr(f) for f in FIELDS)}) ORDER BY name"
    )
    mine, theirs = await _rows(url, field_sql), await _rows(peer, field_sql)
    assert [r["name"] for r in mine] == [r["name"] for r in theirs] == sorted(FIELDS)
    for a, b in zip(mine, theirs, strict=True):
        assert _norm(a) == _norm(b), a["name"]


def _norm(row: dict[str, Any]) -> dict[str, Any]:
    """Decoded config; PyYAML reads Znuny's plain ``No`` as a bool, ours is the string."""

    def fix(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: fix(v) for k, v in value.items()}
        return {True: "Yes", False: "No"}.get(value, value) if isinstance(value, bool) else value

    return {**row, "config": fix(config_from_yaml(row["config"]))}


async def test_apply_change_group_only_when_selected(request, dialect: str) -> None:
    url = cleaned_plain_url(request, dialect)
    after_core = _by_name(await _apply(url, CORE))
    assert after_core["Change"].status == "missing"
    assert await _rows(url, "SELECT name FROM ticket_type WHERE name LIKE 'Change%'") == []
    after_both = _by_name(await _apply(url, {"change"}))
    assert after_both["Change"].status == after_both["Change::Task"].status == "present"
    names = [r["name"] for r in await _rows(url, "SELECT name FROM ticket_type")]
    assert {"Change", "Change::Task", "Incident"} <= set(names)


async def test_apply_is_idempotent(request, dialect: str) -> None:
    url = cleaned_plain_url(request, dialect)
    first = await _apply(url, {"incident_problem", "change"})
    assert {i.status for i in first} == {"present"}
    floor = signal_floor(url)
    counts = await _rows(
        url,
        "SELECT (SELECT count(*) FROM ticket_type) AS t, (SELECT count(*) FROM ticket_state) AS s,"
        " (SELECT count(*) FROM dynamic_field) AS d",
    )
    second = await _apply(url, {"incident_problem", "change"})
    assert second == first
    assert signals_since(url, floor) == []  # nothing written, nothing to invalidate
    assert (
        await _rows(
            url,
            "SELECT (SELECT count(*) FROM ticket_type) AS t,"
            " (SELECT count(*) FROM ticket_state) AS s,"
            " (SELECT count(*) FROM dynamic_field) AS d",
        )
        == counts
    )


async def test_apply_unknown_group_is_refused(request, dialect: str) -> None:
    url = cleaned_plain_url(request, dialect)
    with pytest.raises(ValueError, match="unknown setup group"):
        await _apply(url, {"nonsense"})


async def test_type_names_follow_settings(request, dialect: str) -> None:
    cfg = ItsmSettings(
        incident_type="Stoerung",
        major_incident_type="Stoerung::Gross",
        service_request_type="Anfrage",
        problem_type="Ursache",
        workaround_state="geschlossen mit Umgehung",
        criticality_field="Kritikalitaet",
        impact_field="Auswirkung",
    )
    names_types = ("Stoerung", "Stoerung::Gross", "Anfrage", "Ursache")
    url = cleaned_plain_url(
        request,
        dialect,
        types=names_types,
        states=("geschlossen mit Umgehung",),
        fields=("Kritikalitaet", "Auswirkung", *FIELDS),
    )
    items = await _apply(url, CORE, cfg)
    names = {i.name for i in items if i.group == "incident_problem"}
    assert {*names_types, "geschlossen mit Umgehung", "Kritikalitaet", "Auswirkung"} <= names
    assert "ITSMCriticality" not in names and "ITSMImpact" not in names
    assert {i.status for i in items if i.group == "incident_problem"} == {"present"}
    assert await _rows(url, "SELECT name FROM ticket_type WHERE name = 'Incident'") == []
    # a renamed matrix field carries the label and config of the Znuny field it replaces
    row = (await _rows(url, "SELECT * FROM dynamic_field WHERE name = 'Auswirkung'"))[0]
    assert row["label"] == "Impact" and row["field_type"] == "Dropdown"
    assert config_from_yaml(row["config"])["DefaultValue"] == "3 normal"
    # the other fields are created with Znuny's own names, appended behind the existing ones
    other = await _rows(url, "SELECT field_order FROM dynamic_field WHERE name = 'ITSMDueDate'")
    assert other[0]["field_order"] > 0


# --- HTTP ---------------------------------------------------------------------------------


async def _client(url: str, *, admin: bool) -> AsyncIterator[AsyncClient]:
    engine, factory = make_factory(url)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    if admin:
        app.dependency_overrides[get_admin_user] = lambda: ADMIN
    else:
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
            id=94012, login="itsm94.agent", first_name="Ina", last_name="Agent",
            auth_method="session",
        )  # fmt: skip
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


async def test_setup_routes_preview_then_apply(request, dialect: str) -> None:
    url = cleaned_plain_url(request, dialect)
    async for client in _client(url, admin=True):
        preview = await client.get(BASE)
        assert preview.status_code == 200
        body = preview.json()
        assert body["complete"] is False
        assert {i["status"] for i in body["items"]} == {"missing"}
        assert await _rows(url, "SELECT name FROM ticket_type WHERE name = 'Incident'") == []

        # default body = the core group; the change types stay out
        applied = await client.post(BASE, json={})
        assert applied.status_code == 200
        out = applied.json()
        assert out["complete"] is True
        statuses = {i["name"]: i["status"] for i in out["items"]}
        assert statuses["Incident"] == "present" and statuses["Change"] == "missing"
        assert (await client.get(BASE)).json() == out  # committed

        refused = await client.post(BASE, json={"groups": ["banana"]})
        assert refused.status_code == 422
        assert (await client.post(BASE, json={"groups": []})).status_code == 422


async def test_setup_routes_need_admin(request) -> None:
    url = cleaned_plain_url(request, "mysql")
    async for client in _client(url, admin=False):
        assert (await client.get(BASE)).status_code in (401, 403)
        assert (await client.post(BASE, json={})).status_code in (401, 403)
    assert await _rows(url, "SELECT name FROM ticket_type WHERE name = 'Incident'") == []
