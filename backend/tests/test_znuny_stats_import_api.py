"""HTTP tests for the Znuny statistics import routes of ``/api/v1/reports/import/znuny``.

Both dialects. Band 52040-52099 (names contain ``rep52``): agents 52040 ``rep52.importer``
(``ro`` on group 52060), 52041 ``rep52.viewonly`` (``ro`` on Znuny's ``stats`` group, no ``rw``),
52042 ``rep52.outsider`` (``ro`` on 52061 only); groups 52060 / 52061; queue 52070 in group 52060;
stats in ``xml_storage`` keys 52080 (matrix, Permission 52060), 52081 (TicketList, Permission
52060, Title "rep52 Ticketliste"), 52082 (matrix, Permission 52061), 52083 (Permission 52060,
broken content key). Saved rows are deleted again by owner (the admin's by ``copied_from``).
"""

from __future__ import annotations

import base64
import time
from collections.abc import AsyncIterator, Generator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.test_znuny_stats_map_matrix import FIX
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1 import reports as reports_api
from tiqora.api.v1.reports import get_report_cache
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.reporting.engine import QueryBudget
from tiqora.reporting.settings import REPORTING_SECTION, ReportingSettings
from tiqora.reporting.sql import ReportQueryTimeout
from tiqora.reporting.znuny_stats.reader import list_stat_ids_page, load_stats
from tiqora.settings_sections.store import save_section_values

pytestmark = pytest.mark.db

LO, HI = 52040, 52099
IMPORTER, VIEWONLY, OUTSIDER = 52040, 52041, 52042
G60, G61 = 52060, 52061
QUEUE = 52070
STAT_KEYS = ("52080", "52081", "52082", "52083")
LOGINS = {
    IMPORTER: "rep52.importer",
    VIEWONLY: "rep52.viewonly",
    OUTSIDER: "rep52.outsider",
}
BASE = "[1]{'otrs_stats'}[1]"
_BAND = {"lo": LO, "hi": HI}
_OWNED_REPORTS = (
    "SELECT id FROM tiqora_report WHERE owner_user_id BETWEEN :lo AND :hi"
    " OR copied_from LIKE 'znuny.stat.520%'"
)
_OWNED_SEARCHES = (
    "SELECT id FROM tiqora_saved_search WHERE owner_user_id BETWEEN :lo AND :hi"
    " OR source_name LIKE 'znuny.stat.520%'"
)
_RESET = (
    f"DELETE FROM tiqora_report_group WHERE report_id IN ({_OWNED_REPORTS})",
    f"DELETE FROM tiqora_report WHERE id IN ({_OWNED_REPORTS})",
    f"DELETE FROM tiqora_saved_search_pref WHERE saved_search_id IN ({_OWNED_SEARCHES})",
    f"DELETE FROM tiqora_saved_search_group WHERE saved_search_id IN ({_OWNED_SEARCHES})",
    f"DELETE FROM tiqora_saved_search WHERE id IN ({_OWNED_SEARCHES})",
    "DELETE FROM tiqora_settings WHERE {key} LIKE 'reporting.%'",
)
_CLEANUP = (
    *_RESET,
    "DELETE FROM group_user WHERE user_id BETWEEN :lo AND :hi",
    "DELETE FROM queue WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM permission_groups WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM users WHERE id BETWEEN :lo AND :hi",
)
_URLS: set[str] = set()
_SEEDED: set[str] = set()


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _run_sql(sync_url: str, statements: tuple[str, ...]) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    key = "`key`" if engine.dialect.name == "mysql" else '"key"'
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt.replace("{key}", key)), _BAND)
        conn.execute(
            text("DELETE FROM xml_storage WHERE xml_type = 'Stats' AND xml_key IN :k").bindparams(
                bindparam("k", expanding=True)
            ),
            {"k": list(STAT_KEYS)},
        )
    engine.dispose()


def _scalar(sync_url: str, sql: str) -> Any:
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        value = conn.execute(text(sql)).scalar_one()
    engine.dispose()
    return value


def _element(use: str, index: int, element: str, values: tuple[str, ...], **attrs: str) -> list:
    """XMLHash rows of one ``UseAs...`` node (``Fixed`` defaults to 1)."""
    base = f"{BASE}{{'{use}'}}[{index}]"
    rows = [(f"{base}{{'Element'}}", element), (f"{base}{{'Fixed'}}", attrs.pop("Fixed", "1"))]
    rows += [(f"{base}{{'{k}'}}", v) for k, v in attrs.items()]
    rows += [
        (f"{base}{{'SelectedValues'}}[{i}]{{'Content'}}", v) for i, v in enumerate(values, start=1)
    ]
    return rows


def _stat_rows(title: str, module: str, permission: str, *nodes: list) -> list:
    head = [
        (f"{BASE}{{'Title'}}[1]{{'Content'}}", title),
        (f"{BASE}{{'ObjectModule'}}[1]{{'Content'}}", module),
        (f"{BASE}{{'StatType'}}[1]{{'Content'}}", "dynamic"),
        (f"{BASE}{{'Valid'}}[1]{{'Content'}}", "1"),
        (f"{BASE}{{'Description'}}[1]{{'Content'}}", f"{title} (Beschreibung)"),
        (f"{BASE}{{'Permission'}}[1]{{'Content'}}", permission),
    ]
    for node in nodes:
        head += node
    return head


def _seed(sync_url: str) -> None:
    from datetime import datetime

    t0 = datetime(2026, 10, 1)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        stats_gid = conn.execute(
            text("SELECT id FROM permission_groups WHERE name = 'stats' AND valid_id = 1")
        ).scalar_one()
        for uid, login in LOGINS.items():
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :l, 'x', 'Rep', :l, 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "l": login, "t": t0},
            )
        for gid in (G60, G61):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"rep52-group-{gid}", "t": t0},
            )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": QUEUE, "n": f"rep52-queue-{QUEUE}", "g": G60, "t": t0},
        )
        for uid, gid in ((IMPORTER, G60), (VIEWONLY, stats_gid), (OUTSIDER, G61)):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:u, :g, 'ro', :t, 1, :t, 1)"
                ),
                {"u": uid, "g": gid, "t": t0},
            )
    engine.dispose()


def _seed_stats(sync_url: str) -> None:
    matrix = "Kernel::System::Stats::Dynamic::Ticket"
    ticket_list = "Kernel::System::Stats::Dynamic::TicketList"
    time_x = _element(
        "UseAsXvalue",
        1,
        "CreateTime",
        ("Month",),
        TimeRelativeCount="12",
        TimeRelativeUnit="Month",
        TimeRelativeUpcomingCount="0",
        TimeScaleCount="1",
    )
    queue_series = _element("UseAsValueSeries", 1, "QueueIDs", (str(QUEUE),), Fixed="0")
    stats = {
        "52080": _stat_rows("rep52 Matrix", matrix, str(G60), time_x, queue_series),
        "52081": _stat_rows(
            "rep52 Ticketliste",
            ticket_list,
            str(G60),
            _element("UseAsXvalue", 1, "TicketAttributes", ("TicketNumber", "Title")),
            _element("UseAsRestriction", 1, "QueueIDs", (str(QUEUE),), Fixed="0"),
        ),
        "52082": _stat_rows("rep52 Fremd", matrix, str(G61), time_x),
        "52083": _stat_rows("rep52 Kaputt", matrix, str(G60)) + [("[1]{'otrs_stats'", "x")],
    }
    ins = text(
        "INSERT INTO xml_storage (xml_type, xml_key, xml_content_key, xml_content_value) "
        "VALUES ('Stats', :k, :ck, :v)"
    )
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for key, rows in stats.items():
            for content_key, value in rows:
                conn.execute(ins, {"k": key, "ck": content_key, "v": value})
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _run_sql(url, _CLEANUP)
    _URLS.clear()
    _SEEDED.clear()


def user(uid: int) -> AuthenticatedUser:
    login = "root@localhost" if uid == 1 else LOGINS[uid]
    return AuthenticatedUser(
        id=uid,
        login=login,
        first_name="Rep",
        last_name=login,
        auth_method="session",
        email=f"{login.replace('@localhost', '')}@example.org",
    )


class _MemoryCache:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int) -> None:
        self.store[key] = value


class Env:
    def __init__(self, sync_url: str, factory: async_sessionmaker[AsyncSession], client: Any):
        self.sync_url = sync_url
        self.factory = factory
        self.client = client
        self.user = user(IMPORTER)

    async def set_access(self, access: str) -> None:
        async with self.factory() as s:
            await save_section_values(
                s,
                REPORTING_SECTION,
                {"access": access},
                secret_key=Settings(environment="test").secret_key,
            )
            await s.commit()

    async def count_rows(self, tables: tuple[str, ...]) -> dict[str, int]:
        return {t: int(_scalar(self.sync_url, f"SELECT COUNT(*) FROM {t}")) for t in tables}


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    if sync_url not in _SEEDED:
        _run_sql(sync_url, _CLEANUP)
        _seed(sync_url)
        _SEEDED.add(sync_url)
    _run_sql(sync_url, _RESET)
    _seed_stats(sync_url)
    cache = _MemoryCache()

    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    holder: list[Env] = []
    app.dependency_overrides[get_current_user] = lambda: holder[0].user
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_report_cache] = lambda: cache
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            holder.append(Env(sync_url, factory, c))
            await holder[0].set_access("all_agents")
            yield holder[0]
    finally:
        await engine.dispose()
        _run_sql(sync_url, _RESET)


URL = "/api/v1/reports/import/znuny"


async def test_preview_lists_only_visible_stats(env: Env) -> None:
    env.user = user(IMPORTER)
    out = (await env.client.get(URL)).json()
    keys = {s["import_key"] for s in out["stats"]}
    assert {"znuny.stat.52080", "znuny.stat.52081"} <= keys and "znuny.stat.52082" not in keys
    # least disclosure: a non-admin gets a count, never the id of an unreadable stat
    assert out["errors"] == [
        {"stat_id": None, "message": "Eine Statistik konnte nicht gelesen werden."}
    ]
    s80 = next(s for s in out["stats"] if s["stat_id"] == 52080)
    assert s80["target"] == "report" and s80["definition"]["source"] == "tickets"
    assert s80["already_imported"] is None and s80["object_label"] == "Ticket-Matrix"
    s81 = next(s for s in out["stats"] if s["stat_id"] == 52081)
    assert s81["target"] == "saved_search" and s81["object_label"] == "Ticketliste"


async def test_admin_sees_all(env: Env) -> None:
    env.user = user(1)
    out = (await env.client.get(URL)).json()
    assert "znuny.stat.52082" in {s["import_key"] for s in out["stats"]}
    assert any(e["stat_id"] == 52083 for e in out["errors"])


async def test_outsider_sees_no_importer_stats(env: Env) -> None:
    env.user = user(OUTSIDER)
    keys = {s["import_key"] for s in (await env.client.get(URL)).json()["stats"]}
    assert "znuny.stat.52080" not in keys and "znuny.stat.52082" in keys


async def test_apply_creates_then_updates(env: Env) -> None:
    env.user = user(IMPORTER)
    r1 = (await env.client.post(f"{URL}/apply", json={"stat_ids": [52080, 52081]})).json()
    by_key = {i["import_key"]: i for i in r1["imported"]}
    assert by_key["znuny.stat.52080"]["target"] == "report"
    assert by_key["znuny.stat.52080"]["action"] == "created"
    assert by_key["znuny.stat.52081"]["target"] == "saved_search"
    rep = (await env.client.get(f"/api/v1/reports/{by_key['znuny.stat.52080']['id']}")).json()
    assert rep["visibility"] == "private" and rep["copied_from"] == "znuny.stat.52080"
    sid = by_key["znuny.stat.52081"]["id"]
    search = (await env.client.get(f"/api/v1/saved-searches/{sid}")).json()
    assert search["name"] == "rep52 Ticketliste" and search["source"] == "znuny"
    r2 = (await env.client.post(f"{URL}/apply", json={"stat_ids": [52080]})).json()
    assert r2["imported"][0]["action"] == "updated"
    assert r2["imported"][0]["id"] == by_key["znuny.stat.52080"]["id"]
    prev = (await env.client.get(URL)).json()
    s80 = next(s for s in prev["stats"] if s["stat_id"] == 52080)
    assert s80["already_imported"] == "report" and s80["existing_id"] == r2["imported"][0]["id"]


async def test_reimport_keeps_sharing_and_search_name(env: Env) -> None:
    env.user = user(1)
    first = (await env.client.post(f"{URL}/apply", json={"stat_ids": [52080, 52081]})).json()
    by_key = {i["import_key"]: i for i in first["imported"]}
    rid = by_key["znuny.stat.52080"]["id"]
    rep = (await env.client.get(f"/api/v1/reports/{rid}")).json()
    shared = await env.client.put(
        f"/api/v1/reports/{rid}",
        json={
            "name": "rep52 umbenannt",
            "description": "",
            "definition": rep["definition"],
            "visibility": "all",
            "group_ids": [],
            "version": rep["version"],
        },
    )
    assert shared.status_code == 200, shared.text
    sid = by_key["znuny.stat.52081"]["id"]
    engine = create_engine(env.sync_url)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE tiqora_saved_search SET name = 'rep52 mein Name' WHERE id = :i"),
            {"i": sid},
        )
    engine.dispose()
    again = (await env.client.post(f"{URL}/apply", json={"stat_ids": [52080, 52081]})).json()
    assert {i["action"] for i in again["imported"]} == {"updated"}
    after = (await env.client.get(f"/api/v1/reports/{rid}")).json()
    assert after["visibility"] == "all" and after["name"] == "rep52 Matrix"
    assert after["version"] == rep["version"] + 2  # share bump, then the re-import's update
    search = (await env.client.get(f"/api/v1/saved-searches/{sid}")).json()
    assert search["name"] == "rep52 mein Name"


async def test_apply_needs_create_right(env: Env) -> None:
    env.user = user(VIEWONLY)
    await env.set_access("stats_group")
    resp = await env.client.post(f"{URL}/apply", json={"stat_ids": [52080]})
    assert resp.status_code == 403 and "Berechtigung" in resp.json()["detail"]["message"]


async def test_apply_ignores_invisible_ids(env: Env) -> None:
    env.user = user(IMPORTER)
    out = (await env.client.post(f"{URL}/apply", json={"stat_ids": [52082, 999999]})).json()
    assert out["imported"] == [] and len(out["skipped"]) == 2


async def test_apply_needs_exactly_one_source(env: Env) -> None:
    env.user = user(IMPORTER)
    resp = await env.client.post(f"{URL}/apply", json={})
    assert resp.status_code == 422


async def test_file_import_unknown_names_listed(env: Env) -> None:
    env.user = user(IMPORTER)
    content = (FIX / "Stats.TestTicketList.en.xml").read_bytes()
    body = {"content_base64": base64.b64encode(content).decode()}
    prev = (await env.client.post(f"{URL}/file", json=body)).json()
    stat = prev["stats"][0]
    assert stat["target"] == "saved_search" and stat["stat_id"] is None
    assert stat["import_key"].startswith("znuny.file.")
    missing = content.replace(
        b"<SelectedValues>Junk</SelectedValues>",
        b"<SelectedValues>rep52-missing</SelectedValues>",
    )
    prev2 = (
        await env.client.post(
            f"{URL}/file", json={"content_base64": base64.b64encode(missing).decode()}
        )
    ).json()
    assert any(i["value"] == "rep52-missing" for i in prev2["stats"][0]["issues"])


async def test_file_apply_creates_search(env: Env) -> None:
    env.user = user(IMPORTER)
    content = (FIX / "Stats.TestTicketList.en.xml").read_bytes()
    body = {"file_content_base64": base64.b64encode(content).decode()}
    out = (await env.client.post(f"{URL}/apply", json=body)).json()
    assert out["imported"][0]["target"] == "saved_search"
    assert out["imported"][0]["title"] == "Test TicketList statistic"


async def test_file_errors_are_422(env: Env) -> None:
    env.user = user(IMPORTER)
    for raw in (b"not xml", b"<foo/>"):
        resp = await env.client.post(
            f"{URL}/file", json={"content_base64": base64.b64encode(raw).decode()}
        )
        assert resp.status_code == 422 and "Znuny-Statistik" in resp.json()["detail"]["message"]
    resp = await env.client.post(f"{URL}/file", json={"content_base64": "***"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["message"] == "Die Datei konnte nicht gelesen werden."
    big = base64.b64encode(b"<otrs_stats>" + b"x" * 1_000_001 + b"</otrs_stats>").decode()
    resp = await env.client.post(f"{URL}/file", json={"content_base64": big})
    assert resp.status_code == 422


async def test_import_never_writes_znuny_tables(env: Env) -> None:
    tables = ("xml_storage", "search_profile")
    before = await env.count_rows(tables)
    env.user = user(IMPORTER)
    await env.client.post(f"{URL}/apply", json={"stat_ids": [52080, 52081]})
    assert await env.count_rows(tables) == before


async def test_route_order_and_scope(env: Env) -> None:
    env.user = user(IMPORTER)
    assert (await env.client.get(URL)).status_code == 200
    # a saved report still resolves through /{report_key}
    assert (await env.client.get("/api/v1/reports/builtin.nope")).status_code == 404


def _spent_budget() -> QueryBudget:
    """An inline budget whose shared deadline has already passed."""
    return QueryBudget(per_query_seconds=60, deadline=time.monotonic() - 1)


async def test_reader_honours_spent_budget(env: Env) -> None:
    async with env.factory() as session:
        with pytest.raises(ReportQueryTimeout):
            await load_stats(session, [52080], budget=_spent_budget())
        with pytest.raises(ReportQueryTimeout):
            await list_stat_ids_page(session, budget=_spent_budget())
        ids, _more = await list_stat_ids_page(
            session, budget=QueryBudget.inline(ReportingSettings())
        )
        assert 52080 in ids
        stats, errors, _truncated = await load_stats(
            session, [52080], budget=QueryBudget.inline(ReportingSettings())
        )
        assert [s.stat_id for s in stats] == [52080] and not errors


async def test_import_routes_answer_504_when_budget_is_spent(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        reports_api.QueryBudget, "inline", classmethod(lambda *_a, **_k: _spent_budget())
    )
    preview = await env.client.get(URL)
    assert preview.status_code == 504, preview.text
    assert "zu lange" in preview.text
    applied = await env.client.post(f"{URL}/apply", json={"stat_ids": [52080]})
    assert applied.status_code == 504, applied.text
    monkeypatch.undo()
    saved = await env.client.get("/api/v1/reports")
    assert all(r.get("copied_from") != "znuny.stat.52080" for r in saved.json().get("items", []))
