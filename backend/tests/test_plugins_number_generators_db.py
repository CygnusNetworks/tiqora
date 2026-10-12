"""Ticket number generators from plugins (D7, both dialects).

Seed band 55130-55169: tickets 55131 (tn ``BRK-000777``), 55132 (``2026-000042``) and 55133
(``2026101210000017``) are inserted by hand; counter rows are removed with the row marks and
the ``tickets.number_generator`` setting by key.
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugin_points import boot_points_plugin, points_behaviour
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.settings_store import get_setting, set_setting
from tiqora.extensions.core import get_registry
from tiqora.plugins import number_generators
from tiqora.plugins.number_generators import SETTING_KEY, TICKET_NUMBER_GENERATORS
from tiqora.plugins.state import set_loaded_plugins
from tiqora.znuny.followup import detect_followup
from tiqora.znuny.sysconfig import SysConfig
from tiqora.znuny.ticket_number import ticket_create_number

pytestmark = pytest.mark.db

TICKET_SAME, TICKET_PLUGIN, TICKET_ZNUNY = 55131, 55132, 55133
TICKET_IDS = (TICKET_SAME, TICKET_PLUGIN, TICKET_ZNUNY)
NOW = datetime(2026, 10, 12, 12, 0, 0)
MARK_TABLES: tuple[str, ...] = ("ticket_number_counter",)
YEARLY, DAILY, BROKEN = "Plugin_points_Yearly", "Plugin_points_Daily", "Plugin_points_Broken"
ROOT = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)
ZNUNY_AUTO = "Kernel::System::Ticket::Number::AutoIncrement"
ZNUNY_CHECKSUM = "Kernel::System::Ticket::Number::DateChecksum"


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
        conn.execute(text("DELETE FROM ticket WHERE id IN (55131, 55132, 55133)"))
        conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key == SETTING_KEY))
    engine.dispose()


def _seed(sync_url: str) -> None:
    from tiqora.db.tiqora.base import TiqoraBase

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn, checkfirst=True)
    _clean(sync_url)
    with engine.begin() as conn:
        for tid, tn in (
            (TICKET_SAME, "BRK-000777"),
            (TICKET_PLUGIN, "2026-000042"),
            (TICKET_ZNUNY, "2026101210000017"),
        ):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time, escalation_solution_time,"
                    " archive_flag, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'pp55 number', 1, 1, 1, 1, 1, 3, 4, 'pp55', 'pp55',"
                    " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": tn, "t": NOW},
            )
    engine.dispose()


class Env:
    def __init__(self, sync_url: str, fac: async_sessionmaker[AsyncSession]) -> None:
        self.sync_url = sync_url
        self.factory = fac

    async def select(self, key: str) -> None:
        async with self.factory() as session:
            await set_setting(session, SETTING_KEY, key)

    async def create_number(self) -> str:
        async with self.factory() as session:
            return await ticket_create_number(self.factory, SysConfig(session))


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    marks = snapshot_marks(sync_url, MARK_TABLES)
    engine = create_async_engine(_to_async_url(sync_url))
    fac = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    number_generators._warned_unavailable.clear()
    boot_points_plugin(tmp_path, monkeypatch)
    yield Env(sync_url, fac)
    await engine.dispose()
    delete_above_marks(sync_url, marks)
    _clean(sync_url)


def _events(logs: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry["event"] == event]


def _calls(name: str) -> list[Any]:
    return [c for c in points_behaviour().CALLS if c[0] == name]


async def test_plugin_generator_used_when_selected(env: Env) -> None:
    await env.select(YEARLY)
    tn = await env.create_number()
    assert re.fullmatch(r"\d{4}-\d{6}", tn)
    (_, request), *rest = _calls("number.Yearly")
    assert rest == []
    assert request.counter is not None and request.counter >= 1
    assert request.offset == 0
    assert request.now_local.tzinfo is not None
    assert tn == f"{request.now_local:%Y}-{request.counter:06d}"
    # the second ticket gets the next counter value
    tn2 = await env.create_number()
    assert int(tn2.split("-")[1]) == int(tn.split("-")[1]) + 1


async def test_unset_uses_znuny_generator(env: Env) -> None:
    await env.select("")
    tn = await env.create_number()
    assert re.fullmatch(r"\d{8}\d{2}\d{5}\d", tn)  # yyyymmdd + SystemID + 5-digit counter + check
    assert points_behaviour().CALLS == []
    # no setting row at all is the same
    async with env.factory() as session:
        assert await get_setting(session, SETTING_KEY) in ("", None)


@pytest.mark.parametrize("mode", ["raise", "empty", "long", "nomatch"])
async def test_broken_generator_falls_back(env: Env, mode: str) -> None:
    points_behaviour().MODE["tn"] = mode
    await env.select(BROKEN)
    with structlog.testing.capture_logs() as logs:
        tn = await env.create_number()
    assert re.fullmatch(r"\d{16}", tn)  # the Znuny DateChecksum number, not "BRK-…"
    assert len(_calls("number.Broken")) == 1
    (invalid,) = _events(logs, "plugin_ticket_number_invalid")
    assert invalid["generator"] == BROKEN
    assert (
        invalid["reason"]
        == {
            "raise": "call_failed",
            "empty": "empty",
            "long": "too_long",
            "nomatch": "pattern_mismatch",
        }[mode]
    )


async def test_header_unsafe_number_matching_the_pattern_is_refused(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Core's own character rule applies even when the plugin's pattern would allow it (F8)."""
    gen = get_registry().require(TICKET_NUMBER_GENERATORS, BROKEN)
    loose = type(gen)(
        key=gen.key,
        plugin_id=gen.plugin_id,
        label=gen.label,
        build=lambda req: "BRK 1\r\nBcc: x@example.org",
        pattern=r"BRK .*",
        uses_counter=False,
        date_based=False,
    )
    monkeypatch.setattr(number_generators, "_generators", lambda: [loose])
    await env.select(BROKEN)
    with structlog.testing.capture_logs() as logs:
        tn = await env.create_number()
    assert re.fullmatch(r"\d{16}", tn)
    assert [e["reason"] for e in _events(logs, "plugin_ticket_number_invalid")] == [
        "bad_characters"
    ]


async def test_hanging_generator_falls_back_within_a_second_or_two(env: Env) -> None:
    points_behaviour().MODE["tn"] = "hang"
    await env.select(BROKEN)
    started = time.monotonic()
    with structlog.testing.capture_logs() as logs:
        tn = await env.create_number()
    assert time.monotonic() - started < 2.5
    assert re.fullmatch(r"\d{16}", tn)
    assert _events(logs, "plugin_call_failed")[0]["timed_out"] is True
    assert len(_events(logs, "plugin_ticket_number_invalid")) == 1


async def test_collision_loop_uses_offset(env: Env) -> None:
    mode = points_behaviour().MODE
    mode["tn"], mode["tn.same"] = "same", "BRK-000777"  # ticket 55131 owns this number
    await env.select(BROKEN)
    with structlog.testing.capture_logs() as logs:
        tn = await env.create_number()
    assert re.fullmatch(r"\d{16}", tn)  # a fresh Znuny number after the collisions
    assert [c[1].offset for c in _calls("number.Broken")] == [0, 1, 2]
    (collisions,) = _events(logs, "plugin_ticket_number_collisions")
    assert collisions["offset"] == 3
    async with env.factory() as session:
        taken = (
            await session.execute(text("SELECT COUNT(*) FROM ticket WHERE tn = :tn"), {"tn": tn})
        ).scalar_one()
    assert taken == 0


async def test_disabled_plugin_generator_falls_back(env: Env) -> None:
    await env.select(YEARLY)
    set_loaded_plugins([])
    with structlog.testing.capture_logs() as logs:
        tn = await env.create_number()
        await env.create_number()
    assert re.fullmatch(r"\d{16}", tn)
    assert points_behaviour().CALLS == []
    assert len(_events(logs, "plugin_number_generator_unavailable")) == 1  # once per process


async def test_unknown_selected_key_falls_back(env: Env) -> None:
    await env.select("Plugin_points_Gone")
    assert re.fullmatch(r"\d{16}", await env.create_number())


async def test_followup_finds_plugin_and_znuny_numbers(env: Env) -> None:
    await env.select(YEARLY)
    kw: dict[str, Any] = {"hook": "Ticket#", "hook_divider": "", "references": []}
    async with env.factory() as session:
        cfg = SysConfig(session)
        plugin = await detect_followup(session, cfg, subject="Re: [Ticket#2026-000042] x", **kw)
        znuny = await detect_followup(session, cfg, subject="Re: [Ticket#2026101210000017] x", **kw)
        unknown = await detect_followup(session, cfg, subject="Re: [Ticket#2099-000001] x", **kw)
    assert plugin == ("2026-000042", TICKET_PLUGIN)
    assert znuny == ("2026101210000017", TICKET_ZNUNY)
    assert unknown is None


async def test_followup_takes_first_existing_candidate(env: Env) -> None:
    # the plugin pattern finds 2099-000001 first (no such ticket), the Znuny number second
    await env.select(YEARLY)
    kw: dict[str, Any] = {"hook": "Ticket#", "hook_divider": "", "references": []}
    async with env.factory() as session:
        found = await detect_followup(
            session,
            SysConfig(session),
            subject="Re: [Ticket#2099-000001] fwd Ticket#: 2026101210000017",
            **kw,
        )
    assert found == ("2026101210000017", TICKET_ZNUNY)


async def test_followup_ignores_plugin_scheme_when_not_selected(env: Env) -> None:
    kw: dict[str, Any] = {"hook": "Ticket#", "hook_divider": "", "references": []}
    async with env.factory() as session:
        cfg = SysConfig(session)
        assert await detect_followup(session, cfg, subject="[Ticket#2026-000042]", **kw) is None
        assert await number_generators.plugin_tn_patterns(session) == ()
        await set_setting(session, SETTING_KEY, YEARLY)
        assert await number_generators.plugin_tn_patterns(session) == (r"\d{4}-\d{6}",)
        # only the SELECTED scheme's pattern is followed
        await set_setting(session, SETTING_KEY, BROKEN)
        assert await number_generators.plugin_tn_patterns(session) == (r"BRK-\d{6}",)


# -- registration -------------------------------------------------------------------------------


def _boot_registrar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from tiqora.plugins.context import make_plugin_context
    from tiqora.plugins.discovery import discover_plugins
    from tiqora.plugins.loader import import_and_register

    boot_points_plugin(tmp_path, monkeypatch)
    (discovered,) = [d for d in discover_plugins() if d.plugin_id == "org.example.points"]
    return import_and_register(discovered, make_plugin_context(discovered))


def _build(req: Any) -> str:
    return f"{req.counter:06d}"


def test_registration_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.plugins.registrar import RegistrationRejected

    registrar = _boot_registrar(tmp_path, monkeypatch)
    ok: dict[str, Any] = {"label": "Nummer", "pattern": r"\d{6}"}

    def reject(match: str, name: str = "Fine", build: Any = _build, **over: Any) -> None:
        with pytest.raises(RegistrationRejected, match=match):
            registrar.number_generator(name, build, **{**ok, **over})

    reject("Ungültiger Name", name="lower")
    reject("Bezeichnung", label="")
    reject("Bezeichnung", label="x" * 101)
    reject("gewöhnliche Funktion", build=_async_build)
    reject("Gruppen", pattern=r"(\d{6})")
    reject("Gruppen", pattern=r"(?P<n>\d{6})")
    reject("kein gültiger regulärer Ausdruck", pattern=r"[0-9")
    reject("1 bis 200 Zeichen", pattern=r"\d" * 101)
    reject("1 bis 200 Zeichen", pattern="")
    reject("passt nicht zum Muster", pattern=r"\d{5}")  # the example "000001" is 6 digits
    reject("unzulässige Zeichen", build=lambda req: "A B", pattern=r"A B")
    reject("länger als 50 Zeichen", build=lambda req: "9" * 51, pattern=r"\d+")
    reject("leer", build=lambda req: "", pattern=r".*")
    reject("keine Zeichenkette", build=lambda req: 42, pattern=r".*")
    reject("konnte keine Beispielnummer bilden", build=_raising)
    reject("True oder False", uses_counter="yes")
    reject("keine Anker", pattern=r"^\d{6}$")
    # a core generator name is qualified, never shadowed
    assert registrar.number_generator("DateChecksum", _build, **ok) == "Plugin_points_DateChecksum"
    staged = [i.key for p, i in registrar.staged if p is TICKET_NUMBER_GENERATORS]
    assert "Plugin_points_DateChecksum" in staged
    assert "DateChecksum" not in staged
    with pytest.raises(RegistrationRejected, match="bereits registriert"):
        registrar.number_generator("DateChecksum", _build, **ok)


async def _async_build(req: Any) -> str:
    return "000001"


def _raising(req: Any) -> str:
    raise RuntimeError("kaputt")


def test_number_generator_point_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boot_points_plugin(tmp_path, monkeypatch)
    gens = {g.key: g for g in get_registry().items(TICKET_NUMBER_GENERATORS)}
    assert set(gens) == {YEARLY, DAILY, BROKEN}
    assert gens[YEARLY].uses_counter and not gens[YEARLY].date_based
    assert gens[DAILY].date_based and not gens[BROKEN].uses_counter
    assert TICKET_NUMBER_GENERATORS.key == "core.tickets.number_generators"
    assert TICKET_NUMBER_GENERATORS.sdk_stable is True


# -- admin endpoints ----------------------------------------------------------------------------

BASE = "/api/v1/admin/plugins/number-generators"


@pytest.fixture
async def admin(env: Env) -> AsyncIterator[AsyncClient]:
    async def _db() -> Any:
        async with env.factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_admin_user] = lambda: ROOT
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _znuny_generator(monkeypatch: pytest.MonkeyPatch, full_name: str) -> None:
    async def fake(self: SysConfig) -> str:
        return full_name

    monkeypatch.setattr(SysConfig, "ticket_number_generator", fake)


async def test_admin_endpoints(
    env: Env, admin: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _znuny_generator(monkeypatch, ZNUNY_AUTO)
    body = (await admin.get(BASE)).json()
    assert body["active"] is None
    assert body["znuny_generator"] == "AutoIncrement"
    by_key = {a["key"]: a for a in body["available"]}
    assert set(by_key) == {YEARLY, DAILY, BROKEN}
    assert by_key[YEARLY]["label"] == "Jahresnummer"
    assert by_key[YEARLY]["plugin_id"] == "org.example.points"
    assert re.fullmatch(r"\d{4}-000001", by_key[YEARLY]["example"])
    assert re.fullmatch(r"BRK-000001", by_key[BROKEN]["example"])

    unknown = await admin.put(BASE, json={"key": "Plugin_points_Nope"})
    assert unknown.status_code == 422
    core_name = await admin.put(BASE, json={"key": "DateChecksum"})
    assert core_name.status_code == 422
    async with env.factory() as session:
        assert await get_setting(session, SETTING_KEY) in (None, "")

    chosen = await admin.put(BASE, json={"key": YEARLY})
    assert chosen.status_code == 200
    assert chosen.json()["active"] == YEARLY
    async with env.factory() as session:
        assert await get_setting(session, SETTING_KEY) == YEARLY
    assert (await admin.get(BASE)).json()["active"] == YEARLY

    reset = await admin.put(BASE, json={"key": None})
    assert reset.status_code == 200
    assert reset.json()["active"] is None
    async with env.factory() as session:
        assert await get_setting(session, SETTING_KEY) == ""


async def test_admin_example_is_none_when_the_generator_fails(env: Env, admin: AsyncClient) -> None:
    points_behaviour().MODE["tn"] = "raise"
    by_key = {a["key"]: a for a in (await admin.get(BASE)).json()["available"]}
    assert by_key[BROKEN]["example"] is None
    assert by_key[YEARLY]["example"] is not None


async def test_admin_refuses_mixed_counting(
    env: Env, admin: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plugin shares ticket_number_counter with Znuny: daily reset must match (F14)."""
    _znuny_generator(monkeypatch, ZNUNY_CHECKSUM)  # resets daily
    mixed = await admin.put(BASE, json={"key": YEARLY})  # counts through
    assert mixed.status_code == 422
    assert "Zähler" in mixed.json()["detail"]
    ok = await admin.put(BASE, json={"key": DAILY})
    assert ok.status_code == 200 and ok.json()["active"] == DAILY

    _znuny_generator(monkeypatch, ZNUNY_AUTO)  # counts through
    assert (await admin.put(BASE, json={"key": DAILY})).status_code == 422
    assert (await admin.put(BASE, json={"key": YEARLY})).status_code == 200
    # a generator without a counter never conflicts
    _znuny_generator(monkeypatch, ZNUNY_CHECKSUM)
    assert (await admin.put(BASE, json={"key": BROKEN})).status_code == 200


async def test_admin_inactive_plugin_is_not_offered(env: Env, admin: AsyncClient) -> None:
    set_loaded_plugins([])
    assert (await admin.get(BASE)).json()["available"] == []
    assert (await admin.put(BASE, json={"key": YEARLY})).status_code == 422
