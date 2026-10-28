"""``/api/v1/itsm/legacy-changes``: the read-only Mode A view of Znuny changes (Task 18).

Both dialects x both profiles for the live view (``itsm_db_url``); the seeded rows (see
``test_itsm_legacy_change_reader.seed_legacy_world``, band 946060-946098) are flushed in a
session that is rolled back (the routes' ``commit`` is turned into ``flush``). The mode row, the
profile packages and the Znuny URL setting are committed before the session starts and removed by
``prepare``.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests._itsm_adapter_helpers import count_statements, itsm_session
from tests._itsm_db import ITSM_PARAMS
from tests._itsm_mode_helpers import (
    itsm_url,
    make_factory,
    plain_url,
    profile_packages,
    seed_packages,
    store,
)
from tests.test_itsm_legacy_change_reader import (
    AGENT,
    FIRMWARE,
    GROUP_A,
    GROUP_B,
    MAIL,
    MANAGER,
    MEMBER,
    SECRET_GROUP,
    SERVICE,
    TICKET_A,
    TICKET_B,
    UPTIME,
    LegacyWorld,
    grant,
    group_id,
    seed_legacy_world,
)
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.domain.api_key_scopes import path_to_area
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions import set_registry_for_tests
from tiqora.itsm.change.legacy import reader
from tiqora.itsm.change.legacy.sources import ZnunyLiveSource, get_legacy_source
from tiqora.itsm.mode import SETTING_MODE
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache

pytestmark = pytest.mark.db

SETTINGS = Settings(environment="test")
ADMIN = 1
BASE = "/api/v1/itsm/legacy-changes"
ZNUNY_URL = "https://znuny.example.org/znuny/"


def _user(uid: int) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=uid, login=f"chg94.{uid}", first_name="Erika", last_name="Beispiel",
        auth_method="session",
    )  # fmt: skip


@dataclass
class World:
    client: AsyncClient
    session: AsyncSession
    engine: AsyncEngine
    seeded: LegacyWorld
    as_user: dict[str, AuthenticatedUser]

    def login(self, uid: int) -> None:
        self.as_user["u"] = _user(uid)


@asynccontextmanager
async def _client(
    session: AsyncSession, user: dict[str, AuthenticatedUser]
) -> AsyncIterator[AsyncClient]:
    async def _db() -> Any:
        yield session

    # A commit would persist the seed rows; the test owns the transaction.
    session.commit = session.flush  # type: ignore[method-assign]
    app = create_app(SETTINGS)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user["u"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@asynccontextmanager
async def live_world(
    request: pytest.FixtureRequest, dialect: str, profile: str
) -> AsyncIterator[World]:
    """Mode A on an ITSM database with the seeded changes.

    MEMBER: ro in ``itsm-change``, on queue A and on ``itsm-configitem`` (not the secret CI
    class); MANAGER: the same plus queue B and the secret group; AGENT: no change group.
    """
    url = itsm_url(request, dialect, profile)
    seed_packages(url, profile_packages(profile))
    store(url, SETTING_MODE, "znuny_readthrough")
    store(url, "itsm.znuny_agent_url", ZNUNY_URL)
    set_registry_for_tests(None)  # the real registry: register_core -> ZnunyCiProvider
    try:
        async with itsm_session(request, dialect, profile) as (session, _reader, engine):
            seeded = await seed_legacy_world(session, profile)
            change_group = await group_id(session, "itsm-change")
            for uid in (MEMBER, MANAGER):
                await grant(session, uid, change_group)
                await grant(session, uid, GROUP_A)
                await grant(session, uid, seeded.ci_group)
            await grant(session, MANAGER, GROUP_B)
            await grant(session, MANAGER, SECRET_GROUP)
            await session.flush()
            who = {"u": _user(MEMBER)}
            async with _client(session, who) as client:
                yield World(client, session, engine, seeded, who)
    finally:
        set_registry_for_tests(None)
        clear_detect_cache()


async def _checksums(session: AsyncSession) -> dict[str, str]:
    """Dialect-neutral checksum of every change table and ``link_relation`` (F33)."""
    out: dict[str, str] = {}
    for table in (*reader.CHANGE_TABLES, "link_relation"):  # constants, never input
        rows = (await session.execute(text(f"SELECT * FROM {table}"))).all()  # noqa: S608
        blob = "\n".join(sorted(repr(tuple(r)) for r in rows))
        out[table] = hashlib.sha256(blob.encode()).hexdigest()
    return out


def _ids(body: dict[str, Any]) -> list[int]:
    return [i["znuny_change_id"] for i in body["items"]]


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_live_view_writes_nothing(
    request: pytest.FixtureRequest, dialect: str, profile: str
) -> None:
    async with live_world(request, dialect, profile) as w:
        assert len(reader.CHANGE_TABLES) == 20
        before = await _checksums(w.session)
        assert before["change_item"] != hashlib.sha256(b"").hexdigest()  # the seed is seen
        with count_statements(w.engine) as seen:
            calls = [
                (BASE, {}),
                (BASE, {"q": "relay"}),
                (BASE, {"ticket_id": TICKET_A}),
                (BASE, {"ticket_id": TICKET_B}),
                (BASE, {"ci": f"znuny:{w.seeded.ci_visible}"}),
                (BASE, {"ci": f"znuny:{w.seeded.ci_hidden}"}),
                (f"{BASE}/{MAIL}", {}),
                (f"{BASE}/{FIRMWARE}", {}),
                (f"{BASE}/{UPTIME}", {}),
            ]
            for path, params in calls:
                res = await w.client.get(path, params=params)
                assert res.status_code == 200, (path, res.text)
            assert (await w.client.get(f"{BASE}/946099")).status_code == 404
            w.login(AGENT)
            assert (await w.client.get(BASE)).status_code == 403
        writes = [
            s for s in seen if s.lstrip().split(" ", 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}
        ]
        assert writes == []
        assert any("change_item" in s for s in seen)  # the calls really read Znuny
        assert await _checksums(w.session) == before


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_legacy_requires_group(
    request: pytest.FixtureRequest, dialect: str, profile: str
) -> None:
    async with live_world(request, dialect, profile) as w:
        w.login(AGENT)  # no ro in itsm-change
        for path in (BASE, f"{BASE}/{MAIL}", f"{BASE}/946099"):
            res = await w.client.get(path)
            assert res.status_code == 403, path
            assert res.json()["detail"] == {"code": "legacy_forbidden"}
        # filters do not get around the group either
        res = await w.client.get(BASE, params={"ticket_id": TICKET_A})
        assert res.status_code == 403

        w.login(MEMBER)
        page = await w.client.get(BASE)
        assert page.status_code == 200
        assert page.json()["source"] == "live"
        assert _ids(page.json()) == [UPTIME, FIRMWARE, MAIL] and page.json()["total"] == 3
        assert (await w.client.get(f"{BASE}/{MAIL}")).status_code == 200
        missing = await w.client.get(f"{BASE}/946099")
        assert missing.status_code == 404
        assert missing.json()["detail"] == {"code": "legacy_change_not_found"}

        w.login(ADMIN)  # admins always
        assert (await w.client.get(f"{BASE}/{MAIL}")).status_code == 200


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_legacy_links_filtered_by_ro(
    request: pytest.FixtureRequest, dialect: str, profile: str
) -> None:
    """F20: unreadable tickets and CIs are left out of the detail and of the filters."""
    async with live_world(request, dialect, profile) as w:
        res = await w.client.get(f"{BASE}/{MAIL}")
        assert res.status_code == 200, res.text
        body = res.json()
        assert [
            (t["ticket_id"], t["direction"], t["workorder_number"]) for t in body["tickets"]
        ] == [(TICKET_A, "source", 0)]
        assert body["tickets"][0]["title"] == f"chg94 ticket {TICKET_A}"
        assert body["tickets"][0]["tn"] == f"2026100194{TICKET_A}"
        assert [c["ref"] for c in body["cis"]] == [f"znuny:{w.seeded.ci_visible}"]
        ci = body["cis"][0]
        assert (ci["number"], ci["name"], ci["class_name"]) == (
            "94607001",
            "chg94-srv-01",
            "Computer",
        )
        assert (ci["link_type"], ci["workorder_number"]) == ("DependsOn", 1)
        assert body["services"] == [
            {
                "service_id": SERVICE,
                "name": "chg94 Mail",
                "link_type": "Normal",
                "direction": "source",
                "workorder_number": 2,
            }
        ]
        assert body["hidden_links"] == 2  # TICKET_B and the secret CI, as a number only
        assert "chg94-secret-01" not in res.text and f"chg94 ticket {TICKET_B}" not in res.text

        # filters by something the caller cannot read answer an empty page
        hidden_ticket = (await w.client.get(BASE, params={"ticket_id": TICKET_B})).json()
        assert (hidden_ticket["items"], hidden_ticket["total"]) == ([], 0)
        hidden_ci = (await w.client.get(BASE, params={"ci": f"znuny:{w.seeded.ci_hidden}"})).json()
        assert (hidden_ci["items"], hidden_ci["total"]) == ([], 0)
        assert _ids((await w.client.get(BASE, params={"ticket_id": TICKET_A})).json()) == [MAIL]
        by_ci = (await w.client.get(BASE, params={"ci": f"znuny:{w.seeded.ci_visible}"})).json()
        assert _ids(by_ci) == [MAIL]
        tiqora_ci = (await w.client.get(BASE, params={"ci": "tiqora:946099"})).json()
        assert tiqora_ci["total"] == 0  # a Tiqora CI is never linked to a Znuny work order
        bad = await w.client.get(BASE, params={"ci": "srv-01"})
        assert bad.status_code == 422 and bad.json()["detail"] == {"code": "ci_ref_invalid"}

        w.login(MANAGER)  # reads queue B and the secret class
        full = (await w.client.get(f"{BASE}/{MAIL}")).json()
        assert {t["ticket_id"] for t in full["tickets"]} == {TICKET_A, TICKET_B}
        assert {c["name"] for c in full["cis"]} == {"chg94-srv-01", "chg94-secret-01"}
        assert full["hidden_links"] == 0
        by_ticket = (await w.client.get(BASE, params={"ticket_id": TICKET_B})).json()
        assert _ids(by_ticket) == [UPTIME, MAIL]


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_legacy_detail_shape(
    request: pytest.FixtureRequest, dialect: str, profile: str
) -> None:
    async with live_world(request, dialect, profile) as w:
        body = (await w.client.get(f"{BASE}/{MAIL}")).json()
        assert body["source"] == "live" and body["znuny_change_id"] == MAIL
        assert (
            body["znuny_url"] == f"{ZNUNY_URL}index.pl?Action=AgentITSMChangeZoom;ChangeID={MAIL}"
        )
        assert (body["state"], body["impact"], body["manager_login"]) == (
            "in progress",
            "4 high",
            "chg94.lmanager",
        )
        assert body["description"] == "Replace the relay mail01.example.org"
        assert body["planned_start"] is None and body["actual_end"] is None
        assert body["actual_start"].startswith("2026-11-02T08:05:00")
        assert body["actual_start"].endswith(("Z", "+00:00"))  # UTC on the wire
        assert body["cab"] == [
            {"kind": "user", "login": "chg94.lmanager"},
            {"kind": "user", "login": "chg94.lagent"},
            {"kind": "customer_user", "login": "chg94-cust@example.org"},
        ]
        assert [wo["number"] for wo in body["workorders"]] == [1, 2, 3]
        assert body["workorders"][0]["dynamic_fields"] == {"chg94LWorkOrderRef": ["WO-94-1"]}
        assert body["dynamic_fields"] == {"chg94LChangeRef": ["CHG-94-MAIL", "CHG-94-MAIL-2"]}
        # Znuny's history screen hides the renumbering rows; so does the view
        assert [h["history_type"] for h in body["history"]] == [
            "ChangeAdd",
            "ChangeUpdate",
            "WorkOrderAdd",
        ]
        page = (await w.client.get(BASE, params={"q": "uptime"})).json()
        (item,) = page["items"]
        assert item["planned_start"].startswith("2026-11-09T18:00:00")
        assert (item["workorder_count"], item["state"]) == (1, "requested")
        assert (await w.client.get(BASE, params={"limit": 101})).status_code == 422


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_mode_a_source_is_live_mode_off_none(
    request: pytest.FixtureRequest, dialect: str, profile: str
) -> None:
    url = itsm_url(request, dialect, profile)
    engine, factory = make_factory(url)
    try:
        for mode, expected in (
            ("znuny_readthrough", "live"),
            (None, "live"),  # auto default with Znuny ITSM installed: Mode A
            ("off", None),
            ("tiqora", None),  # Mode B reads the archive (none stored here)
        ):
            store(url, SETTING_MODE, mode)
            clear_detect_cache()
            async with factory() as session:
                source = await get_legacy_source(session, SETTINGS)
                assert (None if source is None else source.kind) == expected, mode
                if expected == "live":
                    assert isinstance(source, ZnunyLiveSource)
                    assert await source.get(session, 946099) is None
        store(url, SETTING_MODE, "off")
        clear_detect_cache()
        async with factory() as session:
            who = {"u": _user(AGENT)}  # not even a member: the source is checked first
            async with _client(session, who) as client:
                res = await client.get(BASE)
                assert res.status_code == 404
                assert res.json()["detail"] == {"code": "legacy_unavailable"}
                assert (await client.get(f"{BASE}/{MAIL}")).status_code == 404
            await session.rollback()
    finally:
        await engine.dispose()
        clear_detect_cache()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_tables_absent_means_no_source(
    request: pytest.FixtureRequest, url_fixture: str
) -> None:
    """A Znuny without ITSMChangeManagement (the ITSM databases of the same server must not
    count): Mode A stored, still no source."""
    url = plain_url(request, url_fixture)
    store(url, SETTING_MODE, "znuny_readthrough")
    clear_detect_cache()
    engine, factory = make_factory(url)
    try:
        async with factory() as session:
            assert await reader.change_tables_present(session) is False
            assert await get_legacy_source(session, SETTINGS) is None
            who = {"u": _user(ADMIN)}
            async with _client(session, who) as client:
                res = await client.get(BASE, params={"q": "chg94"})
                assert res.status_code == 404
                assert res.json()["detail"] == {"code": "legacy_unavailable"}
            await session.rollback()
    finally:
        await engine.dispose()
        clear_detect_cache()


def test_legacy_routes_are_area_tickets() -> None:
    """The routes exist and an API key needs the ``tickets`` area (they list ticket titles)."""
    paths = set(create_app(SETTINGS).openapi()["paths"])
    legacy = {BASE, f"{BASE}/{{znuny_change_id}}"}
    assert legacy <= paths
    for path in legacy:
        assert path_to_area(path) == "tickets", path
    assert path_to_area(f"{BASE}/{MAIL}") == "tickets"
