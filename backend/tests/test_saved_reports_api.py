"""HTTP tests for the saved-report routes of ``/api/v1/reports`` and ``/reference/share-groups``.

Both dialects. Band 48060-48119 (names contain ``rep48``): users 48060 ``rep48.owner``
(Rhea Owner; ``rw`` on Znuny's ``stats`` group, ``ro`` on group 48070), 48061 ``rep48.viewer``
(``ro`` on 48070), 48062 ``rep48.stranger`` (``ro`` on 48071 only); groups 48070 and 48071.
Run / export by saved key are covered by Task 4's module. Saved rows are deleted again by owner.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Generator
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import tiqora.reporting.jobs as report_jobs
import tiqora.reporting.saved as saved_module
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.reports import get_report_cache
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions import get_registry
from tiqora.jobs.runner import JobRunner
from tiqora.reporting.catalogue import BUILTIN_REPORTS
from tiqora.reporting.settings import REPORTING_SECTION
from tiqora.reporting.state import load_state
from tiqora.settings_sections.store import save_section_values

pytestmark = pytest.mark.db

LO, HI = 48060, 48119
OWNER, VIEWER, STRANGER = 48060, 48061, 48062
G70, G71 = 48070, 48071
LOGINS = {OWNER: "rep48.owner", VIEWER: "rep48.viewer", STRANGER: "rep48.stranger"}
NAMES = {OWNER: ("Rhea", "Owner"), VIEWER: ("Vera", "Viewer"), STRANGER: ("Sam", "Stranger")}
TICKETS_DEF = {
    "source": "tickets",
    "dimensions": [{"key": "queue"}],
    "measures": [{"key": "tickets"}],
}
_BAND = {"lo": LO, "hi": HI}
_RESET = (
    "DELETE FROM tiqora_report_group WHERE report_id IN"
    " (SELECT id FROM tiqora_report WHERE owner_user_id BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_report WHERE owner_user_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_job_file WHERE job_id IN"
    " (SELECT id FROM tiqora_job WHERE kind IN ('core.report_run', 'core.report_export'))",
    "DELETE FROM tiqora_job WHERE kind IN ('core.report_run', 'core.report_export')",
    "DELETE FROM tiqora_report_state",
    "DELETE FROM tiqora_settings WHERE {key} LIKE 'reporting.%'",
)
_CLEANUP = (
    *_RESET,
    "DELETE FROM group_user WHERE user_id BETWEEN :lo AND :hi",
    "DELETE FROM permission_groups WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM users WHERE id BETWEEN :lo AND :hi",
)
_URLS: set[str] = set()
_SEEDED: set[str] = set()
#: the owner's group membership as seeded (re-inserted by tests that remove it)
_OWNER_G70 = (
    "INSERT INTO group_user (user_id, group_id, permission_key,"
    " create_time, create_by, change_time, change_by)"
    " VALUES (48060, 48070, 'ro', '2026-10-01', 1, '2026-10-01', 1)"
)


def _builtin_keys() -> list[str]:
    return [r.key for r in get_registry().items(BUILTIN_REPORTS)]


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
    first, last = ("Admin", "OTRS") if uid == 1 else NAMES[uid]
    return AuthenticatedUser(
        id=uid, login=login, first_name=first, last_name=last, auth_method="session"
    )


def _seed(sync_url: str) -> None:
    t0 = datetime(2026, 10, 1)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        stats = conn.execute(
            text("SELECT id FROM permission_groups WHERE name = 'stats' AND valid_id = 1")
        ).scalar_one()
        for uid, login in LOGINS.items():
            first, last = NAMES[uid]
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :l, 'x', :f, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "l": login, "f": first, "n": last, "t": t0},
            )
        for gid in (G70, G71):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"rep48-group-{gid}", "t": t0},
            )
        for uid, gid, key in (
            (OWNER, stats, "rw"),
            (OWNER, G70, "ro"),
            (VIEWER, G70, "ro"),
            (STRANGER, G71, "ro"),
        ):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:u, :g, :k, :t, 1, :t, 1)"
                ),
                {"u": uid, "g": gid, "k": key, "t": t0},
            )
    engine.dispose()


class _MemoryCache:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int) -> None:
        self.store[key] = value


class Env:
    def __init__(
        self, sync_url: str, factory: async_sessionmaker[AsyncSession], client: AsyncClient
    ) -> None:
        self.sync_url = sync_url
        self.factory = factory
        self.client = client
        self.user = user(OWNER)
        self.cache = _MemoryCache()

    def as_user(self, uid: int) -> None:
        self.user = user(uid)

    async def run_jobs(self) -> None:
        runner = JobRunner(
            session_factory=self.factory,
            settings=Settings(environment="test"),
            registry=get_registry(),
        )
        await runner.run_once()
        await runner.wait_idle()

    async def job(self, job_id: int) -> dict[str, Any]:
        resp = await self.client.get(f"/api/v1/jobs/{job_id}")
        assert resp.status_code == 200, resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def ensure_state(self) -> None:
        async with self.factory() as s:
            await load_state(s)
            await s.commit()

    async def facts_ready(self) -> None:
        async with self.factory() as s:
            (await load_state(s)).backfill_status = "done"
            await s.commit()

    async def set_access(self, access: str) -> None:
        async with self.factory() as s:
            await save_section_values(
                s,
                REPORTING_SECTION,
                {"access": access},
                secret_key=Settings(environment="test").secret_key,
            )
            await s.commit()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    if sync_url not in _SEEDED:
        _run_sql(sync_url, _CLEANUP)
        _seed(sync_url)
        _SEEDED.add(sync_url)
    _run_sql(sync_url, _RESET)

    cache = _MemoryCache()
    monkeypatch.setattr(report_jobs, "cache_backend_from_settings", lambda _settings: cache)

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
            holder[0].cache = cache
            await holder[0].set_access("all_agents")
            yield holder[0]
    finally:
        await engine.dispose()
        _run_sql(sync_url, _RESET)


async def _create(env: Env, body: dict[str, Any]) -> dict[str, Any]:
    resp = await env.client.post("/api/v1/reports", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def _keys(env: Env, query: str = "") -> list[str]:
    resp = await env.client.get(f"/api/v1/reports{query}")
    assert resp.status_code == 200, resp.text
    return [r["key"] for r in resp.json()["reports"]]


# ---------------------------------------------------------------- create, list, get


async def test_create_list_get_saved(env: Env) -> None:
    env.as_user(OWNER)
    made = await _create(env, {"name": "rep48 Queues", "definition": TICKETS_DEF})
    assert made["kind"] == "saved" and made["key"] == str(made["id"])
    assert made["read_only"] is False
    assert made["owner"] == {"user_id": OWNER, "name": "Rhea Owner"}
    assert made["visibility"] == "private" and made["group_ids"] == []
    cat = (await env.client.get("/api/v1/reports")).json()["reports"]
    builtin_keys = _builtin_keys()
    assert len(builtin_keys) == 25
    assert [r["key"] for r in cat[:25]] == builtin_keys  # built-ins first, order unchanged
    assert [r["kind"] for r in cat[:25]] == ["builtin"] * 25
    assert cat[25]["key"] == made["key"] and len(cat) == 26
    got = (await env.client.get(f"/api/v1/reports/{made['key']}")).json()
    assert got["definition"]["source"] == "tickets" and got["version"] == 1
    assert got["name"] == "rep48 Queues" and got["definition_error"] is None


async def test_builtin_entries_carry_null_saved_fields(env: Env) -> None:
    first = (await env.client.get("/api/v1/reports")).json()["reports"][0]
    assert first["kind"] == "builtin" and first["id"] is None and first["read_only"] is True
    assert first["group_ids"] == [] and first["owner"] is None and first["version"] is None
    assert first["name"] is None and first["visibility"] is None
    assert first["title_key"] == "reports.builtin.ticket_kpis.title"


async def test_unknown_keys_are_404(env: Env) -> None:
    builtin = await env.client.get("/api/v1/reports/builtin.nope")
    assert builtin.status_code == 404
    assert "builtin.nope" in builtin.json()["detail"]["message"]
    for key in ("987654321", "12x", "abc"):
        resp = await env.client.get(f"/api/v1/reports/{key}")
        assert resp.status_code == 404, key
        assert resp.json()["detail"]["message"] == "Bericht nicht gefunden."


async def test_invisible_report_is_404_on_every_route(env: Env) -> None:
    env.as_user(OWNER)
    made = await _create(env, {"name": "rep48 privat", "definition": TICKETS_DEF})
    env.as_user(STRANGER)
    key = made["key"]
    for resp in (
        await env.client.get(f"/api/v1/reports/{key}"),
        await env.client.get(f"/api/v1/reports/{key}/file?format=json"),
        await env.client.put(
            f"/api/v1/reports/{made['id']}",
            json={"name": "x", "definition": TICKETS_DEF, "version": 1},
        ),
        await env.client.delete(f"/api/v1/reports/{made['id']}"),
    ):
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"]["message"] == "Bericht nicht gefunden."
    assert key not in await _keys(env)
    env.as_user(OWNER)  # the failed writes changed nothing
    assert (await env.client.get(f"/api/v1/reports/{key}")).json()["name"] == "rep48 privat"


async def test_group_share_and_delete_gives_410(env: Env) -> None:
    env.as_user(OWNER)
    made = await _create(
        env,
        {
            "name": "rep48 Team",
            "definition": TICKETS_DEF,
            "visibility": "groups",
            "group_ids": [G70],
        },
    )
    env.as_user(VIEWER)
    seen = (await env.client.get(f"/api/v1/reports/{made['key']}")).json()
    assert seen["read_only"] is True and seen["group_ids"] == [G70]
    assert made["key"] in await _keys(env)
    assert (await env.client.delete(f"/api/v1/reports/{made['id']}")).status_code == 403
    env.as_user(STRANGER)
    assert made["key"] not in await _keys(env)
    env.as_user(OWNER)
    assert (await env.client.delete(f"/api/v1/reports/{made['id']}")).status_code == 204
    assert made["key"] not in await _keys(env)
    env.as_user(VIEWER)
    gone = await env.client.get(f"/api/v1/reports/{made['key']}")
    assert gone.status_code == 410 and "rep48 Team" in gone.json()["detail"]["message"]
    again = await env.client.delete(f"/api/v1/reports/{made['id']}")
    assert again.status_code == 410  # deleted reports are gone for everyone who saw them
    env.as_user(OWNER)
    assert (await env.client.delete(f"/api/v1/reports/{made['id']}")).status_code == 410


async def test_update_with_stale_version_is_409(env: Env) -> None:
    env.as_user(OWNER)
    made = await _create(env, {"name": "rep48 v", "definition": TICKETS_DEF})
    body = {"name": "rep48 v2", "definition": TICKETS_DEF, "version": 1}
    first = await env.client.put(f"/api/v1/reports/{made['id']}", json=body)
    assert first.status_code == 200 and first.json()["version"] == 2
    second = await env.client.put(f"/api/v1/reports/{made['id']}", json=body)
    assert second.status_code == 409
    assert second.json()["detail"] == {
        "message": "Der Bericht wurde inzwischen geändert. Bitte neu laden.",
        "current_version": 2,
    }
    assert (await env.client.get(f"/api/v1/reports/{made['key']}")).json()["name"] == "rep48 v2"


async def test_update_keeps_group_share_after_owner_left_the_group(env: Env) -> None:
    """R-SHAREKEEP: unchanged sharing is not re-validated against the owner's groups."""
    env.as_user(OWNER)
    made = await _create(
        env,
        {"name": "rep48 bleibt", "definition": TICKETS_DEF, "visibility": "groups",
         "group_ids": [G70]},
    )  # fmt: skip
    _run_sql(
        env.sync_url,
        ("DELETE FROM group_user WHERE user_id = 48060 AND group_id = 48070",),
    )
    try:
        body = {
            "name": "rep48 bleibt 2",
            "definition": TICKETS_DEF,
            "visibility": "groups",
            "group_ids": [G70],
            "version": 1,
        }
        kept = await env.client.put(f"/api/v1/reports/{made['id']}", json=body)
        assert kept.status_code == 200, kept.text
        assert kept.json()["group_ids"] == [G70] and kept.json()["name"] == "rep48 bleibt 2"
        # adding a group the owner cannot share with is still refused
        widened = await env.client.put(
            f"/api/v1/reports/{made['id']}", json={**body, "version": 2, "group_ids": [G70, G71]}
        )
        assert widened.status_code == 422 and widened.json()["detail"]["path"] == "group_ids"
        # private ignores group ids and stores none
        private = await env.client.put(
            f"/api/v1/reports/{made['id']}",
            json={**body, "version": 2, "visibility": "private", "group_ids": [G71]},
        )
        assert private.status_code == 200 and private.json()["group_ids"] == []
    finally:
        _run_sql(env.sync_url, (_OWNER_G70,))


async def test_share_needs_share_right(env: Env) -> None:
    env.as_user(VIEWER)  # all_agents: may create, may not share
    resp = await env.client.post(
        "/api/v1/reports",
        json={"name": "rep48 s", "definition": TICKETS_DEF, "visibility": "all"},
    )
    assert resp.status_code == 403 and "Teilen" in resp.json()["detail"]["message"]
    resp = await env.client.post(
        "/api/v1/reports",
        json={
            "name": "rep48 g",
            "definition": TICKETS_DEF,
            "visibility": "groups",
            "group_ids": [G70],
        },
    )
    assert resp.status_code == 403
    assert not any(k.isdigit() for k in await _keys(env))  # nothing was stored


async def test_group_outside_the_owners_groups_is_422(env: Env) -> None:
    env.as_user(OWNER)
    resp = await env.client.post(
        "/api/v1/reports",
        json={
            "name": "rep48 fremd",
            "definition": TICKETS_DEF,
            "visibility": "groups",
            "group_ids": [G71],
        },
    )
    assert resp.status_code == 422 and resp.json()["detail"]["path"] == "group_ids"


async def test_invalid_definition_is_422_with_path(env: Env) -> None:
    env.as_user(OWNER)
    resp = await env.client.post(
        "/api/v1/reports",
        json={
            "name": "rep48 bad",
            "definition": {
                "source": "tickets",
                "measures": [{"key": "tickets"}],
                "dimensions": [{"key": "nope"}],
            },
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["path"].startswith("definition.dimensions")
    blank = await env.client.post(
        "/api/v1/reports", json={"name": "   ", "definition": TICKETS_DEF}
    )
    assert blank.status_code == 422 and blank.json()["detail"]["path"] == "name"


async def test_broken_definition_lists_and_gets_without_definition(env: Env) -> None:
    env.as_user(OWNER)
    made = await _create(env, {"name": "rep48 kaputt", "definition": TICKETS_DEF})
    _run_sql(
        env.sync_url,
        (
            "UPDATE tiqora_report SET definition = '{\"schema_version\": 2}'"
            f" WHERE id = {made['id']}",
        ),
    )
    item = next(
        r for r in (await env.client.get("/api/v1/reports")).json()["reports"]
        if r["key"] == made["key"]
    )  # fmt: skip
    assert item["definition"] is None and "nicht mehr gelesen" in item["definition_error"]
    got = await env.client.get(f"/api/v1/reports/{made['key']}")
    assert got.status_code == 200 and got.json()["definition"] is None
    file = await env.client.get(f"/api/v1/reports/{made['key']}/file")
    assert file.status_code == 422 and file.json()["detail"]["path"] == "definition"


async def test_admin_all_lists_others_private(env: Env) -> None:
    env.as_user(OWNER)
    made = await _create(env, {"name": "rep48 nur ich", "definition": TICKETS_DEF})
    env.as_user(1)
    assert made["key"] not in await _keys(env)
    assert made["key"] in await _keys(env, "?all=true")
    assert (await env.client.get(f"/api/v1/reports/{made['key']}")).status_code == 200  # by id
    env.as_user(VIEWER)
    assert made["key"] not in await _keys(env, "?all=true")  # ignored for non-admins


async def test_requires_report_access(env: Env) -> None:
    await env.set_access("stats_group")
    env.as_user(OWNER)  # rw on stats
    assert (await env.client.get("/api/v1/reports")).status_code == 200
    env.as_user(VIEWER)
    assert (await env.client.get("/api/v1/reports")).status_code == 403
    resp = await env.client.post(
        "/api/v1/reports", json={"name": "rep48 x", "definition": TICKETS_DEF}
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------- files


async def test_file_download_and_import(env: Env) -> None:
    env.as_user(OWNER)
    made = await _create(env, {"name": "rep48 Größe", "definition": TICKETS_DEF})
    resp = await env.client.get(f"/api/v1/reports/{made['key']}/file?format=yaml")
    assert resp.status_code == 200
    assert 'filename="rep48-groesse.yaml"' in resp.headers["content-disposition"]
    assert resp.headers["content-type"].startswith("application/yaml")
    parsed = await env.client.post("/api/v1/reports/import-file", json={"content": resp.text})
    assert parsed.status_code == 200 and parsed.json()["name"] == "rep48 Größe"
    assert parsed.json()["definition"]["source"] == "tickets"
    as_json = await env.client.get(f"/api/v1/reports/{made['key']}/file")
    assert as_json.headers["content-disposition"].endswith('rep48-groesse.json"')
    builtin = await env.client.get("/api/v1/reports/builtin.ticket_kpis/file?format=json")
    assert builtin.status_code == 200 and builtin.json()["name"] == "builtin.ticket_kpis"
    bad = await env.client.post(
        "/api/v1/reports/import-file",
        json={
            "content": json.dumps(
                {
                    "format": "tiqora-report",
                    "format_version": 1,
                    "name": "x",
                    "definition": {"source": "nope", "measures": [{"key": "tickets"}]},
                }
            )
        },
    )
    assert bad.status_code == 422 and bad.json()["detail"]["path"].startswith("definition.source")
    assert (await _keys(env)).count(made["key"]) == 1  # importing saved nothing


async def test_import_of_pathological_files_is_422(env: Env) -> None:
    for content in ("[" * 100_000, "a: &a [x]\nb: *a\n", "{"):
        resp = await env.client.post("/api/v1/reports/import-file", json={"content": content})
        assert resp.status_code == 422, content[:20]
        assert resp.json()["detail"]["path"] == "content"


# ---------------------------------------------------------------- share groups


async def test_share_groups_endpoint(env: Env) -> None:
    env.as_user(VIEWER)
    groups = (await env.client.get("/api/v1/reference/share-groups")).json()
    assert {"id": G70, "name": "rep48-group-48070"} in groups
    assert all(g["id"] != G71 for g in groups)  # negative control: seeded, but not theirs
    env.as_user(STRANGER)
    groups = (await env.client.get("/api/v1/reference/share-groups")).json()
    assert {"id": G71, "name": "rep48-group-48071"} in groups
    assert all(g["id"] != G70 for g in groups)
    env.as_user(1)  # admin: every valid group
    groups = (await env.client.get("/api/v1/reference/share-groups")).json()
    assert {g["id"] for g in groups} >= {G70, G71}


async def test_deleted_private_report_is_404_for_a_stranger(env: Env) -> None:
    env.as_user(OWNER)
    made = await _create(env, {"name": "rep48 weg", "definition": TICKETS_DEF})
    assert (await env.client.delete(f"/api/v1/reports/{made['id']}")).status_code == 204
    assert (await env.client.get(f"/api/v1/reports/{made['key']}")).status_code == 410  # owner
    env.as_user(STRANGER)
    gone = await env.client.get(f"/api/v1/reports/{made['key']}")
    assert gone.status_code == 404  # existence of the deleted report stays hidden
    assert "rep48 weg" not in gone.text
    assert (await env.client.delete(f"/api/v1/reports/{made['id']}")).status_code == 404


async def test_oversized_report_ids_are_not_500(env: Env) -> None:
    body = {"name": "x", "definition": TICKETS_DEF, "version": 1}
    for rid in (2**63, 10**30, 0):
        put = await env.client.put(f"/api/v1/reports/{rid}", json=body)
        assert put.status_code == 422, (rid, put.text)
        assert (await env.client.delete(f"/api/v1/reports/{rid}")).status_code == 422
    huge = await env.client.get(f"/api/v1/reports/{10**30}")
    assert huge.status_code == 404  # key route: not a valid saved key
    absent = await env.client.delete(f"/api/v1/reports/{2**63 - 1}")
    assert absent.status_code == 404


# ---------------------------------------------------------------- final fix wave


#: VIEWER gets ``ro`` on Znuny's ``stats`` group (view without the save right under stats_group)
_VIEWER_STATS_RO = (
    "INSERT INTO group_user (user_id, group_id, permission_key,"
    " create_time, create_by, change_time, change_by)"
    " SELECT 48061, id, 'ro', '2026-10-01', 1, '2026-10-01', 1"
    " FROM permission_groups WHERE name = 'stats' AND valid_id = 1"
)
_VIEWER_STATS_RO_UNDO = (
    "DELETE FROM group_user WHERE user_id = 48061 AND group_id IN"
    " (SELECT id FROM permission_groups WHERE name = 'stats')"
)


async def test_owner_without_report_access_may_still_delete(env: Env) -> None:
    """R-OWNDELETE over HTTP: after the switch to stats_group the owner can clean up."""
    env.as_user(VIEWER)  # all_agents: may save private reports, holds nothing on stats
    made = await _create(env, {"name": "rep48 verwaist", "definition": TICKETS_DEF})
    await env.set_access("stats_group")
    assert (await env.client.get("/api/v1/reports")).status_code == 403  # no view right now
    env.as_user(STRANGER)  # invisible stays 404, existence is not revealed
    assert (await env.client.delete(f"/api/v1/reports/{made['id']}")).status_code == 404
    env.as_user(VIEWER)
    assert (await env.client.delete(f"/api/v1/reports/{made['id']}")).status_code == 204
    await env.set_access("all_agents")
    assert (await env.client.get(f"/api/v1/reports/{made['key']}")).status_code == 410


async def test_owner_without_save_right_is_told_so(env: Env) -> None:
    env.as_user(VIEWER)
    made = await _create(env, {"name": "rep48 ro", "definition": TICKETS_DEF})
    _run_sql(env.sync_url, (_VIEWER_STATS_RO,))
    try:
        await env.set_access("stats_group")  # stats ro: view yes, save no
        resp = await env.client.put(
            f"/api/v1/reports/{made['id']}",
            json={"name": "rep48 ro2", "definition": TICKETS_DEF, "version": 1},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["message"] == (
            "Für das Speichern von Berichten fehlt die Berechtigung."
        )
        # a colleague's shared report keeps the owner text
        env.as_user(OWNER)
        shared = await _create(
            env,
            {"name": "rep48 geteilt", "definition": TICKETS_DEF, "visibility": "groups",
             "group_ids": [G70]},
        )  # fmt: skip
        env.as_user(VIEWER)
        other = await env.client.put(
            f"/api/v1/reports/{shared['id']}",
            json={"name": "rep48 x", "definition": TICKETS_DEF, "version": 1},
        )
        assert other.status_code == 403 and "Besitzer" in other.json()["detail"]["message"]
    finally:
        _run_sql(env.sync_url, (_VIEWER_STATS_RO_UNDO,))


async def test_per_user_limit_is_422_like_searches(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(saved_module, "MAX_SAVED_REPORTS_PER_USER", 1)
    env.as_user(OWNER)
    await _create(env, {"name": "rep48 eins", "definition": TICKETS_DEF})
    resp = await env.client.post(
        "/api/v1/reports", json={"name": "rep48 zwei", "definition": TICKETS_DEF}
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["path"] == "name"
    assert "Berichte pro Person" in resp.json()["detail"]["message"]
