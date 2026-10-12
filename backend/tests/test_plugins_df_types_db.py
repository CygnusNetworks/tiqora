"""Dynamic field types from plugins (D8, both dialects).

Seed band 55170-55209: ticket 55171; the field ``PP55Vertrag`` is created through the admin API,
so ``dynamic_field`` / ``dynamic_field_value`` rows are removed above their marks and by name.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import structlog
import yaml
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugin_points import boot_points_plugin, points_behaviour
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.dynamic_field_values import (
    DynamicFieldValueError,
    df_display_value,
    load_ticket_dynamic_field,
)
from tiqora.domain.ticket_write_service import InvalidInput, update_dynamic_field
from tiqora.extensions.core import get_registry
from tiqora.plugins.df_types import (
    DYNAMIC_FIELD_TYPES,
    display_value_bounded,
    plugin_type_of,
    validate_plugin_values,
)
from tiqora.plugins.state import set_loaded_plugins
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

TICKET = 55171
NOW = datetime(2026, 10, 12, 12, 0, 0)
FIELD = "PP55Vertrag"
TYPE = "Plugin_points_Contract"
BASE = "/api/v1/admin/dynamic-fields"
MARK_TABLES: tuple[str, ...] = (
    "tiqora_event_outbox",
    "tiqora_cache_invalidation",
    "ticket_history",
    "dynamic_field_value",
    "dynamic_field",
)
ROOT = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _clean(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ticket_history WHERE ticket_id = :t"), {"t": TICKET})
        conn.execute(text("DELETE FROM ticket WHERE id = :t"), {"t": TICKET})
        conn.execute(
            text(
                "DELETE FROM dynamic_field_value WHERE field_id IN"
                " (SELECT id FROM dynamic_field WHERE name LIKE 'PP55%')"
            )
        )
        conn.execute(text("DELETE FROM dynamic_field WHERE name LIKE 'PP55%'"))
    engine.dispose()


def _seed(sync_url: str) -> None:
    from tiqora.db.tiqora.base import TiqoraBase

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn, checkfirst=True)
    engine.dispose()
    _clean(sync_url)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:id, '2026101255171', 'pp55 df', 1, 1, 1, 1, 1, 3, 4, 'pp55', 'pp55',"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {"id": TICKET, "t": NOW},
        )
    engine.dispose()


class Env:
    def __init__(self, sync_url: str, fac: async_sessionmaker[AsyncSession]) -> None:
        self.sync_url = sync_url
        self.factory = fac

    async def set_values(self, values: list[str]) -> None:
        async with self.factory() as session:
            await update_dynamic_field(
                session, ticket_id=TICKET, field_name=FIELD, values=values, user_id=1
            )
            await session.commit()

    def stored(self) -> list[str]:
        engine = create_engine(self.sync_url)
        try:
            with engine.begin() as conn:
                rows = conn.execute(
                    text(
                        "SELECT value_text FROM dynamic_field_value v JOIN dynamic_field f"
                        " ON f.id = v.field_id WHERE f.name = :n AND v.object_id = :t"
                    ),
                    {"n": FIELD, "t": TICKET},
                ).fetchall()
        finally:
            engine.dispose()
        return [str(r[0]) for r in rows]


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    marks = snapshot_marks(sync_url, MARK_TABLES)
    engine = create_async_engine(_to_async_url(sync_url))
    fac = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    boot_points_plugin(tmp_path, monkeypatch)
    yield Env(sync_url, fac)
    await engine.dispose()
    delete_above_marks(sync_url, marks)
    _clean(sync_url)


@pytest.fixture
async def admin(env: Env) -> AsyncIterator[AsyncClient]:
    async def _db() -> Any:
        async with env.factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_admin_user] = lambda: ROOT
    app.dependency_overrides[get_current_user] = lambda: ROOT
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": FIELD,
        "label": "Vertrag",
        "field_order": 9955,
        "field_type": "Text",
        "plugin_type": TYPE,
        "config": {"TiqoraPluginConfig": {"prefix": "V-"}},
    }
    body.update(over)
    return body


async def _create(admin: AsyncClient, **over: Any) -> dict[str, Any]:
    resp = await admin.post(BASE, json=_body(**over))
    assert resp.status_code == 201, resp.text
    out: dict[str, Any] = resp.json()
    return out


def _events(logs: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry["event"] == event]


# -- admin ---------------------------------------------------------------------------------------


async def test_admin_creates_plugin_field_as_base_type(env: Env, admin: AsyncClient) -> None:
    out = await _create(admin)
    assert out["field_type"] == "Text"
    assert out["plugin_type"] == TYPE
    assert out["config"] == {
        "TiqoraPluginType": TYPE,
        "TiqoraPluginConfig": {"prefix": "V-", "style": "plain"},
    }
    engine = create_engine(env.sync_url)
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT field_type, config FROM dynamic_field WHERE name = :n"), {"n": FIELD}
        ).one()
    engine.dispose()
    assert row[0] == "Text"
    assert yaml.safe_load(row[1])["TiqoraPluginType"] == TYPE
    got = (await admin.get(f"{BASE}/{out['id']}")).json()
    assert got["plugin_type"] == TYPE


async def test_create_without_plugin_settings_stores_the_defaults(
    env: Env, admin: AsyncClient
) -> None:
    out = await _create(admin, config={})
    assert out["config"]["TiqoraPluginConfig"] == {"prefix": "V-", "style": "plain"}


async def test_create_rejects_mismatched_base_type_and_bad_config(
    env: Env, admin: AsyncClient
) -> None:
    wrong = await admin.post(BASE, json=_body(field_type="Dropdown"))
    assert wrong.status_code == 422
    assert "Text" in wrong.json()["detail"]
    bad = await admin.post(BASE, json=_body(config={"TiqoraPluginConfig": {"prefix": 5}}))
    assert bad.status_code == 422
    assert bad.json()["detail"].startswith("Einstellungen der Erweiterung: ")
    unknown = await admin.post(BASE, json=_body(plugin_type="Plugin_points_Nope"))
    assert unknown.status_code == 422
    assert "nicht verfügbar" in unknown.json()["detail"]
    # the plugin keys are not accepted on a plain field, nor can the body set the type
    plain = await admin.post(BASE, json=_body(plugin_type=None, config={"TiqoraPluginType": TYPE}))
    assert plain.status_code == 422
    smuggled = await admin.post(
        BASE, json=_body(config={"TiqoraPluginType": "Plugin_points_Other"})
    )
    assert smuggled.status_code == 422
    stray = await admin.post(BASE, json=_body(config={"Nonsense": 1}))
    assert stray.status_code == 422
    listing = (await admin.get(BASE, params={"page_size": 200})).json()
    assert [f for f in listing["items"] if f["name"] == FIELD] == []


async def test_plugin_types_route_comes_before_the_id_route(env: Env, admin: AsyncClient) -> None:
    resp = await admin.get(f"{BASE}/plugin-types")
    assert resp.status_code == 200, resp.text
    (item,) = resp.json()
    assert item["key"] == TYPE
    assert item["label"] == "Vertragsnummer"
    assert item["base_type"] == "Text"
    assert item["plugin_id"] == "org.example.points"
    assert item["config_schema"]["properties"]["prefix"]["default"] == "V-"
    set_loaded_plugins([])
    assert (await admin.get(f"{BASE}/plugin-types")).json() == []


async def test_update_cannot_change_plugin_type(env: Env, admin: AsyncClient) -> None:
    created = await _create(admin)
    url = f"{BASE}/{created['id']}"
    kept = await admin.patch(url, json={"config": {"DefaultValue": "V-1"}})
    assert kept.status_code == 200, kept.text
    assert kept.json()["plugin_type"] == TYPE
    assert kept.json()["config"]["TiqoraPluginType"] == TYPE
    assert kept.json()["config"]["TiqoraPluginConfig"] == {"prefix": "V-", "style": "plain"}
    assert kept.json()["config"]["DefaultValue"] == "V-1"
    changed = await admin.patch(
        url, json={"config": {"TiqoraPluginType": "Plugin_points_Other", "DefaultValue": "x"}}
    )
    assert changed.status_code == 422
    bad_settings = await admin.patch(url, json={"config": {"TiqoraPluginConfig": {"prefix": 5}}})
    assert bad_settings.status_code == 422
    good_settings = await admin.patch(
        url, json={"config": {"TiqoraPluginConfig": {"prefix": "W-"}}}
    )
    assert good_settings.json()["config"]["TiqoraPluginConfig"] == {
        "prefix": "W-",
        "style": "plain",
    }
    assert good_settings.json()["config"]["TiqoraPluginType"] == TYPE
    still = (await admin.get(url)).json()
    assert still["plugin_type"] == TYPE


async def test_update_cannot_make_a_plain_field_a_plugin_field(
    env: Env, admin: AsyncClient
) -> None:
    created = await _create(admin, plugin_type=None, config={})
    resp = await admin.patch(f"{BASE}/{created['id']}", json={"config": {"TiqoraPluginType": TYPE}})
    assert resp.status_code == 422
    assert (await admin.get(f"{BASE}/{created['id']}")).json()["plugin_type"] is None


# -- validation ----------------------------------------------------------------------------------


async def test_validate_normalizes_and_rejects(env: Env, admin: AsyncClient) -> None:
    await _create(admin)
    await env.set_values(["v-12"])
    assert env.stored() == ["V-12"]
    with pytest.raises(InvalidInput) as raised:
        await env.set_values(["12"])
    assert str(raised.value) == f"{FIELD}: Vertragsnummer muss wie V-123 aussehen"
    assert env.stored() == ["V-12"]  # nothing was written
    calls = [c for c in points_behaviour().CALLS if c[0] == "df.validate"]
    values, settings = calls[0][1]
    assert values == ["v-12"]
    assert settings["prefix"] == "V-"  # the plugin's settings come with defaults
    assert settings["style"].name == "PLAIN"  # typed Enum, not its text


@pytest.mark.parametrize("mode", ["raise", "hang", "badresult"])
async def test_crashing_validator_fails_open(env: Env, admin: AsyncClient, mode: str) -> None:
    await _create(admin)
    points_behaviour().MODE["df"] = mode
    started = time.monotonic()
    with structlog.testing.capture_logs() as logs:
        await env.set_values(["12"])
    assert time.monotonic() - started < 2.5
    assert env.stored() == ["12"]  # stored as the base Text would store it
    if mode == "badresult":
        assert len(_events(logs, "plugin_df_validate_result_invalid")) == 1
    else:
        (failed,) = _events(logs, "plugin_call_failed")
        assert failed["plugin"] == "org.example.points"
        assert failed["timed_out"] is (mode == "hang")
        assert "12" not in repr(logs)  # the value is never logged


async def test_missing_plugin_behaves_as_base_type(env: Env, admin: AsyncClient) -> None:
    await _create(admin)
    set_loaded_plugins([])
    await env.set_values(["12"])
    assert env.stored() == ["12"]
    assert points_behaviour().CALLS == []
    async with env.factory() as session:
        field = await load_ticket_dynamic_field(session, FIELD)
    assert field is not None
    assert field.plugin_type == TYPE  # still recorded, but not served
    assert plugin_type_of(field.config) is None


async def test_core_fields_pay_no_plugin_parse(env: Env, admin: AsyncClient) -> None:
    await _create(admin, name="PP55Plain", plugin_type=None, config={})
    async with env.factory() as session:
        field = await load_ticket_dynamic_field(session, "PP55Plain")
        assert field is not None
        assert field.config == {}
        assert field.plugin_type is None
        assert await validate_plugin_values(field, ["abc"]) == ["abc"]


# -- display and reference -----------------------------------------------------------------------


async def test_display_and_reference(
    env: Env, admin: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await _create(admin)
    config = created["config"]
    assert df_display_value("Text", config, ["V-12"], plugin=True) == "Vertrag V-12"
    assert await display_value_bounded("Text", config, ["V-12"]) == "Vertrag V-12"
    assert df_display_value("Text", {}, ["V-12"]) == "V-12"  # a core field is unchanged
    # a failing display falls back to the core display
    points_behaviour().MODE["df.display"] = "raise"
    assert df_display_value("Text", config, ["V-12"], plugin=True) == "V-12"
    assert await display_value_bounded("Text", config, ["V-12"]) == "V-12"
    points_behaviour().MODE.pop("df.display")

    real_get = SysConfig.get

    async def fake_get(self: SysConfig, name: str, default: Any = None) -> Any:
        if name == "Ticket::Frontend::AgentTicketPhone###DynamicField":
            return {FIELD: 1}
        return await real_get(self, name, default)

    monkeypatch.setattr(SysConfig, "get", fake_get)
    resp = await admin.get(
        "/api/v1/reference/dynamic-fields", params={"screen": "AgentTicketPhone"}
    )
    assert resp.status_code == 200, resp.text
    (definition,) = [d for d in resp.json() if d["name"] == FIELD]
    assert definition["plugin_type"] == TYPE
    assert definition["field_type"] == "Text"  # the agent form keeps the base type's input


async def test_error_text_is_capped(env: Env, admin: AsyncClient) -> None:
    await _create(admin)
    async with env.factory() as session:
        field = await load_ticket_dynamic_field(session, FIELD)
    assert field is not None
    item = next(t for t in get_registry().items(DYNAMIC_FIELD_TYPES) if t.key == TYPE)
    assert item.base_type == "Text"
    with pytest.raises(DynamicFieldValueError, match="Vertragsnummer muss wie V-123 aussehen"):
        await validate_plugin_values(field, ["nope"])


async def test_enum_settings_are_stored_as_json_and_reach_the_plugin_typed(
    env: Env, admin: AsyncClient
) -> None:
    out = await _create(admin, config={"TiqoraPluginConfig": {"prefix": "V-", "style": "bold"}})
    assert out["config"]["TiqoraPluginConfig"] == {"prefix": "V-", "style": "bold"}
    engine = create_engine(env.sync_url)
    with engine.begin() as conn:
        raw = conn.execute(
            text("SELECT config FROM dynamic_field WHERE name = :n"), {"n": FIELD}
        ).scalar_one()
    engine.dispose()
    assert yaml.safe_load(raw)["TiqoraPluginConfig"] == {"prefix": "V-", "style": "bold"}
    patched = await admin.patch(
        f"{BASE}/{out['id']}", json={"config": {"TiqoraPluginConfig": {"style": "plain"}}}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["config"]["TiqoraPluginConfig"]["style"] == "plain"
    bad = await admin.patch(
        f"{BASE}/{out['id']}", json={"config": {"TiqoraPluginConfig": {"style": "loud"}}}
    )
    assert bad.status_code == 422
    await admin.patch(
        f"{BASE}/{out['id']}", json={"config": {"TiqoraPluginConfig": {"style": "bold"}}}
    )
    await env.set_values(["v-12"])
    calls = [c for c in points_behaviour().CALLS if c[0] == "df.validate"]
    assert calls[-1][1][1]["style"].name == "BOLD"  # re-validation restores the Enum


async def test_hanging_display_is_bounded_across_a_chain(env: Env, admin: AsyncClient) -> None:
    from tiqora.channels.email.placeholder import expand_placeholders
    from tiqora.plugins.placeholders import placeholder_budget_scope

    await _create(admin)
    await env.set_values(["V-12"])
    points_behaviour().MODE["df.display"] = "hang"
    points_behaviour().CALLS.clear()
    started = time.monotonic()
    outs: list[str] = []
    async with env.factory() as session:
        with placeholder_budget_scope(1.2):
            for _ in range(20):
                outs.append(
                    await expand_placeholders(
                        session,
                        SysConfig(session),
                        f"<OTRS_TICKET_DynamicField_{FIELD}_Value>",
                        ticket_id=TICKET,
                        user_id=1,
                    )
                )
    assert time.monotonic() - started < 3.5  # 20 x 1 s without the shared budget
    assert len([c for c in points_behaviour().CALLS if c[0] == "df.display"]) <= 3
    assert set(outs) == {"V-12"}  # the core display once the budget is used up


# -- registration --------------------------------------------------------------------------------


def _boot_registrar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from tiqora.plugins.context import make_plugin_context
    from tiqora.plugins.discovery import discover_plugins
    from tiqora.plugins.loader import import_and_register

    boot_points_plugin(tmp_path, monkeypatch)
    (discovered,) = [d for d in discover_plugins() if d.plugin_id == "org.example.points"]
    return import_and_register(discovered, make_plugin_context(discovered))


class _Unschematic(BaseModel):
    callback: Callable[[], int]


def _sync(values: list[str], config: Any) -> list[str]:
    return values


async def _async(values: list[str], config: Any) -> list[str]:
    return values


def test_registration_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.plugins.registrar import RegistrationRejected

    registrar = _boot_registrar(tmp_path, monkeypatch)
    ok: dict[str, Any] = {"label": "Typ", "base_type": "Text"}

    def reject(match: str, name: str = "Fine", **over: Any) -> None:
        with pytest.raises(RegistrationRejected, match=match):
            registrar.dynamic_field_type(name, **{**ok, **over})

    reject("nicht erlaubt", base_type="Password")
    reject("nicht erlaubt", base_type="Nonsense")
    reject("Ungültiger Name", name="lower")
    reject("Bezeichnung", label="")
    reject("gewöhnliche Funktion", validate=_async)
    reject("gewöhnliche Funktion", display=_async)
    reject("Pydantic", config_model=dict)
    reject("JSON-Schema", config_model=_Unschematic)
    # a core-sounding name is qualified, never shadowed
    assert registrar.dynamic_field_type("Text", validate=_sync, **ok) == "Plugin_points_Text"
    staged = [i.key for p, i in registrar.staged if p is DYNAMIC_FIELD_TYPES]
    assert "Plugin_points_Text" in staged
    assert "Text" not in staged
    with pytest.raises(RegistrationRejected, match="bereits registriert"):
        registrar.dynamic_field_type("Text", **ok)


def test_dynamic_field_type_point_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boot_points_plugin(tmp_path, monkeypatch)
    types = {t.key: t for t in get_registry().items(DYNAMIC_FIELD_TYPES)}
    assert set(types) == {TYPE}
    contract = types[TYPE]
    assert contract.base_type == "Text"
    assert contract.plugin_id == "org.example.points"
    assert contract.validate is not None and contract.display is not None
    assert contract.config_model is not None
    assert DYNAMIC_FIELD_TYPES.key == "core.dynamic_fields.types"
    assert DYNAMIC_FIELD_TYPES.sdk_stable is True
