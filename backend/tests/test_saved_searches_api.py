"""HTTP tests for ``/api/v1/saved-searches`` (both dialects).

Seed ids 48200-48249 (names contain ``rep48``): agent 48200 ``rep48.searcher`` (``ro`` on
group/queue 48210 and group 48212), 48201 ``rep48.colleague`` (``ro`` on group/queue 48211 and
group 48212), 48202 ``rep48.other`` (no permissions); tickets 48220/48221 (queue 48210, customer
user ``rep48.cust@example.org``) and 48222 (queue 48211); dynamic field 48230 ``Rep48Area``
(Dropdown, ticket) with a value on 48220. The Znuny admin (id 1) only acts as a viewer. Every
row the module writes (also the saved rows created through the API) is deleted again.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import tiqora.api.v1.saved_searches as saved_searches_api
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.saved_searches import get_count_cache
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.reporting.sql import ReportQueryTimeout
from tiqora.saved_searches import store

pytestmark = pytest.mark.db

LO, HI = 48200, 48249
SEARCHER, COLLEAGUE, OTHER = 48200, 48201, 48202
Q_SEARCHER, Q_COLLEAGUE, G_SHARED = 48210, 48211, 48212
T_A, T_B, T_C = 48220, 48221, 48222
DF_ID = 48230
NOW = datetime(2026, 1, 1, 12, 0, 0)
Q210 = {"where": {"field": "queue_id", "op": "in", "value": [48210, 48211]}}
USERS = {
    SEARCHER: AuthenticatedUser(
        id=SEARCHER, login="rep48.searcher", first_name="Sam", last_name="Searcher",
        auth_method="session",
    ),
    COLLEAGUE: AuthenticatedUser(
        id=COLLEAGUE, login="rep48.colleague", first_name="Cleo", last_name="Colleague",
        auth_method="session",
    ),
    OTHER: AuthenticatedUser(
        id=OTHER, login="rep48.other", first_name="Otto", last_name="Other", auth_method="session"
    ),
    1: AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="OTRS", auth_method="session"
    ),
}  # fmt: skip
_BAND = {"lo": LO, "hi": HI}
_OWNED = "SELECT id FROM tiqora_saved_search WHERE owner_user_id BETWEEN :lo AND :hi"
_RESET = (
    f"DELETE FROM tiqora_saved_search_pref WHERE saved_search_id IN ({_OWNED})"
    " OR user_id BETWEEN :lo AND :hi",
    f"DELETE FROM tiqora_saved_search_group WHERE saved_search_id IN ({_OWNED})",
    "DELETE FROM tiqora_saved_search WHERE owner_user_id BETWEEN :lo AND :hi",
    # the admin owns a row only in a test that says so
    "DELETE FROM tiqora_saved_search WHERE owner_user_id = 1 AND name LIKE 'rep48%'",
    "UPDATE dynamic_field SET valid_id = 1 WHERE id = 48230",
)
_CLEANUP = (
    *_RESET[:4],
    "DELETE FROM dynamic_field_value WHERE field_id = 48230",
    "DELETE FROM dynamic_field WHERE id = 48230",
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM group_user WHERE user_id BETWEEN :lo AND :hi",
    "DELETE FROM queue WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM permission_groups WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM users WHERE id BETWEEN :lo AND :hi",
)
_URLS: set[str] = set()
_SEEDED: set[str] = set()


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _run_sql(sync_url: str, statements: tuple[str, ...]) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt), _BAND)
    engine.dispose()


def _scalar(sync_url: str, sql: str) -> int:
    """First column of the first row of ``sql`` as an int (a ``COUNT(*)``)."""
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        value = conn.execute(text(sql)).scalar_one()
    engine.dispose()
    return int(value)


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _run_sql(url, _CLEANUP)
    _URLS.clear()
    _SEEDED.clear()


def _grant(conn: Any, user: int, group: int) -> None:
    conn.execute(
        text(
            "INSERT INTO group_user (user_id, group_id, permission_key,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:u, :g, 'ro', :t, 1, :t, 1)"
        ),
        {"u": user, "g": group, "t": NOW},
    )


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for uid, login in ((SEARCHER, "searcher"), (COLLEAGUE, "colleague"), (OTHER, "other")):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :l, 'x', :f, :s, 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "l": f"rep48.{login}", "f": login.title(), "s": "Rep", "t": NOW},
            )
        for gid in (Q_SEARCHER, Q_COLLEAGUE, G_SHARED):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"rep48-group-{gid}", "t": NOW},
            )
        for qid in (Q_SEARCHER, Q_COLLEAGUE):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :id, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": qid, "n": f"rep48-queue-{qid}", "t": NOW},
            )
        for user, group in (
            (SEARCHER, Q_SEARCHER),
            (SEARCHER, G_SHARED),
            (COLLEAGUE, Q_COLLEAGUE),
            (COLLEAGUE, G_SHARED),
        ):
            _grant(conn, user, group)
        for tid, queue in ((T_A, Q_SEARCHER), (T_B, Q_SEARCHER), (T_C, Q_COLLEAGUE)):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, :title, :q, 1, 1, 1, 1, 3, 4, 'rep48-cust',"
                    " 'rep48.cust@example.org', 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"2026010{tid}", "title": f"rep48 ticket {tid}", "q": queue,
                 "t": NOW},
            )  # fmt: skip
        conn.execute(
            text(
                "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                " field_type, object_type, config, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, 0, 'Rep48Area', 'Rep48Area', 48230,"
                " 'Dropdown', 'Ticket', '---', 1, :t, 1, :t, 1)"
            ),
            {"id": DF_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO dynamic_field_value (field_id, object_id, value_text)"
                " VALUES (:f, :o, 'Nord')"
            ),
            {"f": DF_ID, "o": T_A},
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
    def __init__(self, sync_url: str, client: AsyncClient, cache: _MemoryCache) -> None:
        self.sync_url = sync_url
        self.client = client
        self.cache = cache
        self.user = USERS[SEARCHER]

    def as_user(self, user_id: int) -> None:
        self.user = USERS[user_id]


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

    async def _db() -> Any:
        async with factory() as session:
            yield session

    cache = _MemoryCache()
    app = create_app(Settings(environment="test"))
    holder: list[Env] = []
    app.dependency_overrides[get_current_user] = lambda: holder[0].user
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_count_cache] = lambda: cache
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            holder.append(Env(sync_url, c, cache))
            yield holder[0]
    finally:
        await engine.dispose()
        _run_sql(sync_url, _RESET)


async def _create(env: Env, name: str, **extra: Any) -> dict[str, Any]:
    body = {"name": name, "criteria": Q210, **extra}
    resp = await env.client.post("/api/v1/saved-searches", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def _counts(env: Env) -> dict[str, int | None]:
    resp = await env.client.get("/api/v1/saved-searches/counts")
    assert resp.status_code == 200, resp.text
    return resp.json()["counts"]  # type: ignore[no-any-return]


# ---------------------------------------------------------------- create, list, get


async def test_create_list_get_and_prefs(env: Env) -> None:
    env.as_user(SEARCHER)
    made = await _create(env, "rep48 Meine", show_count=True)
    assert made["is_owner"] and made["can_edit"] and made["show_in_sidebar"] and made["show_count"]
    assert made["sort_by"] == "age" and made["source"] == "tiqora" and made["version"] == 1
    assert made["owner"] == {"user_id": SEARCHER, "name": "Searcher Rep"}
    assert made["criteria"] == Q210 | {"version": 1}  # no "value": null noise
    listed = (await env.client.get("/api/v1/saved-searches")).json()
    assert [s["id"] for s in listed] == [made["id"]]
    got = (await env.client.get(f"/api/v1/saved-searches/{made['id']}")).json()
    assert got == listed[0]


async def test_create_from_list_params(env: Env) -> None:
    env.as_user(SEARCHER)
    resp = await env.client.post(
        "/api/v1/saved-searches",
        json={
            "name": "rep48 Ansicht",
            "list_params": {
                "queue_id": Q_SEARCHER,
                "state_type": "todo",
                "unassigned": True,
                "include_archived": True,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    made = resp.json()
    where = made["criteria"]["where"]["all"]
    assert {"field": "queue_id", "op": "eq", "value": Q_SEARCHER} in where
    assert {"field": "unassigned", "op": "is_true"} in where
    assert made["include_archived"] is True


async def test_empty_list_params_alone_is_a_valid_source(env: Env) -> None:
    env.as_user(SEARCHER)
    resp = await env.client.post(
        "/api/v1/saved-searches", json={"name": "rep48 Alle", "list_params": {}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["criteria"] == {"version": 1}


async def test_exactly_one_of_criteria_or_list_params(env: Env) -> None:
    env.as_user(SEARCHER)
    both = await env.client.post(
        "/api/v1/saved-searches", json={"name": "rep48 x", "criteria": Q210, "list_params": {}}
    )
    none = await env.client.post("/api/v1/saved-searches", json={"name": "rep48 y"})
    assert both.status_code == 422 and none.status_code == 422
    assert (
        _scalar(env.sync_url, "SELECT COUNT(*) FROM tiqora_saved_search WHERE name LIKE 'rep48%'")
        == 0
    )


async def test_invalid_criteria_is_422_with_path(env: Env) -> None:
    env.as_user(SEARCHER)
    resp = await env.client.post(
        "/api/v1/saved-searches",
        json={
            "name": "rep48 z",
            "criteria": {"where": {"field": "queue_id", "op": "contains", "value": "x"}},
        },
    )
    assert resp.status_code == 422 and resp.json()["detail"]["path"] == "criteria.where.op"


async def test_blank_name_and_foreign_sort_key_are_422(env: Env) -> None:
    env.as_user(SEARCHER)
    blank = await env.client.post("/api/v1/saved-searches", json={"name": "  ", "criteria": Q210})
    assert blank.status_code == 422 and blank.json()["detail"]["path"] == "name"
    # R-SORTKEY: the queue list's "owner" column is not a backend sort key
    owner = await env.client.post(
        "/api/v1/saved-searches", json={"name": "rep48 s", "criteria": Q210, "sort_by": "owner"}
    )
    assert owner.status_code == 422
    activity = await env.client.post(
        "/api/v1/saved-searches",
        json={"name": "rep48 s", "criteria": Q210, "sort_by": "activity", "sort_order": "asc"},
    )
    assert activity.status_code == 201 and activity.json()["sort_by"] == "activity"


async def test_per_user_limit(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "MAX_SAVED_SEARCHES_PER_USER", 1)
    env.as_user(SEARCHER)
    await _create(env, "rep48 eins")
    resp = await env.client.post(
        "/api/v1/saved-searches", json={"name": "rep48 zwei", "criteria": Q210}
    )
    assert resp.status_code == 422
    assert "gespeicherte Suchen pro Person" in resp.json()["detail"]["message"]


async def test_positions_append_and_list_order(env: Env) -> None:
    env.as_user(SEARCHER)
    first = await _create(env, "rep48 B")
    second = await _create(env, "rep48 A")
    assert (first["position"], second["position"]) == (0, 1)
    listed = (await env.client.get("/api/v1/saved-searches")).json()
    assert [s["id"] for s in listed] == [first["id"], second["id"]]  # position before name
    moved = await env.client.put(
        f"/api/v1/saved-searches/{second['id']}/prefs",
        json={"show_in_sidebar": True, "show_count": False, "position": 0},
    )
    assert moved.json()["position"] == 0
    listed = (await env.client.get("/api/v1/saved-searches")).json()
    assert [s["id"] for s in listed] == [second["id"], first["id"]]  # tie 0/0 -> name A before B


# ---------------------------------------------------------------- sharing and visibility


async def test_shared_search_lists_only_viewer_queues(env: Env) -> None:
    env.as_user(SEARCHER)
    made = await _create(env, "rep48 Team", group_ids=[G_SHARED])
    env.as_user(COLLEAGUE)
    seen = next(
        s for s in (await env.client.get("/api/v1/saved-searches")).json() if s["id"] == made["id"]
    )
    assert seen["is_owner"] is False and seen["can_edit"] is False
    assert seen["show_in_sidebar"] is False
    assert seen["owner"]["name"] == "Searcher Rep"
    page = (
        await env.client.post("/api/v1/tickets/query", json={"filter": seen["criteria"]})
    ).json()
    assert [t["id"] for t in page["items"]] == [T_C]  # the colleague's own queue only
    resp = await env.client.put(
        f"/api/v1/saved-searches/{made['id']}/prefs",
        json={"show_in_sidebar": True, "show_count": True},
    )
    assert resp.status_code == 200, resp.text
    counts = await _counts(env)
    assert counts[str(made["id"])] == 1
    env.as_user(SEARCHER)
    assert (await _counts(env)) == {}  # the owner's own prefs are untouched (count off)
    env.as_user(OTHER)
    assert (await env.client.get(f"/api/v1/saved-searches/{made['id']}")).status_code == 404
    assert made["id"] not in [
        s["id"] for s in (await env.client.get("/api/v1/saved-searches")).json()
    ]


async def test_private_search_is_invisible_to_colleague_but_not_to_admin_by_id(env: Env) -> None:
    env.as_user(SEARCHER)
    made = await _create(env, "rep48 Privat")
    env.as_user(COLLEAGUE)
    assert (await env.client.get(f"/api/v1/saved-searches/{made['id']}")).status_code == 404
    env.as_user(1)
    assert [s["id"] for s in (await env.client.get("/api/v1/saved-searches")).json()] == []
    admin_view = await env.client.get(f"/api/v1/saved-searches/{made['id']}")
    assert admin_view.status_code == 200 and admin_view.json()["can_edit"] is True


async def test_prefs_on_an_invisible_search_is_404_and_writes_nothing(env: Env) -> None:
    env.as_user(SEARCHER)
    made = await _create(env, "rep48 Geheim", group_ids=[G_SHARED])
    env.as_user(OTHER)
    resp = await env.client.put(
        f"/api/v1/saved-searches/{made['id']}/prefs",
        json={"show_in_sidebar": True, "show_count": True},
    )
    assert resp.status_code == 404
    assert (
        resp.json()["detail"]["message"]
        == (await env.client.get(f"/api/v1/saved-searches/{made['id']}")).json()["detail"][
            "message"
        ]
    )
    unknown = await env.client.put(
        "/api/v1/saved-searches/999999999/prefs", json={"show_in_sidebar": True, "show_count": True}
    )
    assert unknown.status_code == 404
    assert (
        _scalar(
            env.sync_url, f"SELECT COUNT(*) FROM tiqora_saved_search_pref WHERE user_id = {OTHER}"
        )
        == 0
    )


async def test_prefs_without_position_keep_the_stored_one(env: Env) -> None:
    env.as_user(SEARCHER)
    made = await _create(env, "rep48 P")
    other = await _create(env, "rep48 Q")
    assert other["position"] == 1
    resp = await env.client.put(
        f"/api/v1/saved-searches/{other['id']}/prefs",
        json={"show_in_sidebar": False, "show_count": True},
    )
    body = resp.json()
    assert (body["position"], body["show_in_sidebar"], body["show_count"]) == (1, False, True)
    assert made["position"] == 0


async def test_only_owner_or_admin_edits_and_deletes(env: Env) -> None:
    env.as_user(SEARCHER)
    made = await _create(env, "rep48 E", group_ids=[G_SHARED])
    env.as_user(COLLEAGUE)
    upd = {"name": "rep48 E2", "criteria": Q210, "version": 1, "group_ids": [G_SHARED]}
    assert (
        await env.client.put(f"/api/v1/saved-searches/{made['id']}", json=upd)
    ).status_code == 403
    assert (await env.client.delete(f"/api/v1/saved-searches/{made['id']}")).status_code == 403
    env.as_user(OTHER)  # cannot even see it
    assert (
        await env.client.put(f"/api/v1/saved-searches/{made['id']}", json=upd)
    ).status_code == 404
    assert (await env.client.delete(f"/api/v1/saved-searches/{made['id']}")).status_code == 404
    env.as_user(1)
    resp = await env.client.put(f"/api/v1/saved-searches/{made['id']}", json=upd)
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "rep48 E2" and resp.json()["owner"]["user_id"] == SEARCHER


async def test_search_update_with_stale_version_is_409(env: Env) -> None:
    env.as_user(SEARCHER)
    made = await _create(env, "rep48 V", group_ids=[G_SHARED])
    body = {"name": "rep48 V2", "criteria": Q210, "version": 1, "group_ids": [G_SHARED]}
    assert (
        await env.client.put(f"/api/v1/saved-searches/{made['id']}", json=body)
    ).status_code == 200
    stale = await env.client.put(
        f"/api/v1/saved-searches/{made['id']}", json=body | {"name": "rep48 lost", "group_ids": []}
    )
    assert stale.status_code == 409 and stale.json()["detail"]["current_version"] == 2
    now = (await env.client.get(f"/api/v1/saved-searches/{made['id']}")).json()
    assert now["name"] == "rep48 V2" and now["group_ids"] == [G_SHARED]  # nothing overwritten


async def test_sharing_rules_on_create_and_update(env: Env) -> None:
    env.as_user(SEARCHER)
    # OTHER holds nothing, so cannot share into a group (and the group stays unnamed in the text)
    env.as_user(OTHER)
    denied = await env.client.post(
        "/api/v1/saved-searches",
        json={"name": "rep48 s", "criteria": Q210, "group_ids": [G_SHARED]},
    )
    assert denied.status_code == 422 and denied.json()["detail"]["path"] == "group_ids"
    env.as_user(SEARCHER)
    made = await _create(env, "rep48 K", group_ids=[G_SHARED])
    # the owner loses the group, the stored sharing may still be kept (R-SHAREKEEP) ...
    _run_sql(
        env.sync_url,
        (f"DELETE FROM group_user WHERE user_id = {SEARCHER} AND group_id = {G_SHARED}",),
    )
    try:
        keep = {"name": "rep48 K2", "criteria": Q210, "version": 1, "group_ids": [G_SHARED]}
        resp = await env.client.put(f"/api/v1/saved-searches/{made['id']}", json=keep)
        assert resp.status_code == 200, resp.text
        assert resp.json()["group_ids"] == [G_SHARED]
        # ... but a group that is not already stored is checked
        add = keep | {"version": 2, "group_ids": [G_SHARED, Q_COLLEAGUE]}
        added = await env.client.put(f"/api/v1/saved-searches/{made['id']}", json=add)
        assert added.status_code == 422 and added.json()["detail"]["path"] == "group_ids"
    finally:
        _run_sql(env.sync_url, ())
        engine = create_engine(env.sync_url)
        with engine.begin() as conn:
            _grant(conn, SEARCHER, G_SHARED)
        engine.dispose()


# ---------------------------------------------------------------- broken criteria, counts


async def test_deleted_dynamic_field_marks_search_broken(env: Env) -> None:
    env.as_user(SEARCHER)
    made = await _create(
        env, "rep48 DF", show_count=True,
        criteria={"where": {"field": "df.Rep48Area", "op": "in", "value": ["Nord"]}},
    )  # fmt: skip
    assert (await _counts(env))[str(made["id"])] == 1
    env.cache.store.clear()
    _run_sql(env.sync_url, ("UPDATE dynamic_field SET valid_id = 2 WHERE id = 48230",))
    item = next(
        s for s in (await env.client.get("/api/v1/saved-searches")).json() if s["id"] == made["id"]
    )
    assert item["criteria"] is not None  # stored JSON still parses
    assert (await _counts(env))[str(made["id"])] is None  # cannot be counted, no 500
    resp = await env.client.post("/api/v1/tickets/query", json={"filter": item["criteria"]})
    assert resp.status_code == 422
    update = await env.client.put(
        f"/api/v1/saved-searches/{made['id']}",
        json={"name": "rep48 DF", "criteria": item["criteria"], "version": 1},
    )
    assert update.status_code == 422  # saving the stale criteria is refused


async def test_unreadable_stored_criteria_lists_with_error_and_null_count(env: Env) -> None:
    env.as_user(SEARCHER)
    made = await _create(env, "rep48 Kaputt", show_count=True)
    _run_sql(
        env.sync_url,
        (
            "UPDATE tiqora_saved_search SET criteria = '{\"version\": 2}'"
            f" WHERE id = {made['id']}",
        ),
    )
    item = (await env.client.get(f"/api/v1/saved-searches/{made['id']}")).json()
    assert item["criteria"] is None
    assert item["criteria_error"] == "Die gespeicherte Suche kann nicht mehr gelesen werden."
    assert (await _counts(env))[str(made["id"])] is None


async def test_counts_only_sidebar_with_count_and_capped(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "MAX_SIDEBAR_COUNTS", 2)
    env.as_user(SEARCHER)
    ids = [(await _create(env, f"rep48 C{i}", show_count=i != 1))["id"] for i in range(4)]
    counts = await _counts(env)
    assert set(counts) == {str(ids[0]), str(ids[2])}  # filter first, then cap: first two counted
    assert counts[str(ids[0])] == 2 and counts[str(ids[2])] == 2  # the searcher's queue only
    # hidden from the sidebar -> no count even with show_count
    await env.client.put(
        f"/api/v1/saved-searches/{ids[0]}/prefs",
        json={"show_in_sidebar": False, "show_count": True},
    )
    assert set(await _counts(env)) == {str(ids[2]), str(ids[3])}


async def test_count_timeout_gives_null(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _slow(*_a: Any, **_k: Any) -> int:
        raise ReportQueryTimeout()

    monkeypatch.setattr(saved_searches_api, "count_tickets", _slow)
    env.as_user(SEARCHER)
    made = await _create(env, "rep48 T", show_count=True)
    assert (await _counts(env))[str(made["id"])] is None
    assert env.cache.store == {}  # a failed count is never cached


async def test_counts_are_cached_for_the_viewer_and_keyed_by_version(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    real = saved_searches_api.count_tickets

    async def _spy(*a: Any, **k: Any) -> int:
        calls.append(1)
        return await real(*a, **k)

    monkeypatch.setattr(saved_searches_api, "count_tickets", _spy)
    env.as_user(SEARCHER)
    made = await _create(env, "rep48 Cache", show_count=True)
    sid = str(made["id"])
    assert (await _counts(env))[sid] == 2 and len(calls) == 1
    assert (await _counts(env))[sid] == 2 and len(calls) == 1  # second request: cache hit
    narrower = {"where": {"field": "queue_id", "op": "eq", "value": Q_COLLEAGUE}}
    upd = await env.client.put(
        f"/api/v1/saved-searches/{made['id']}",
        json={"name": "rep48 Cache", "criteria": narrower, "version": 1},
    )
    assert upd.status_code == 200
    assert (await _counts(env))[sid] == 0 and len(calls) == 2  # new version -> new key


async def test_counts_evaluate_the_viewer_scope_once_per_request(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    real = saved_searches_api.ticket_query_scope

    async def _spy(*a: Any, **k: Any) -> Any:
        calls.append(1)
        return await real(*a, **k)

    monkeypatch.setattr(saved_searches_api, "ticket_query_scope", _spy)
    env.as_user(SEARCHER)
    ids = [(await _create(env, f"rep48 S{i}", show_count=True))["id"] for i in range(3)]
    counts = await _counts(env)
    assert [counts[str(i)] for i in ids] == [2, 2, 2]  # still the searcher's queue only
    assert len(calls) == 1
    calls.clear()
    assert (await _counts(env))[str(ids[0])] == 2 and calls == []  # all cached: no scope at all


async def test_counts_stop_at_the_shared_deadline(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.as_user(SEARCHER)
    ids = [(await _create(env, f"rep48 D{i}", show_count=True))["id"] for i in range(2)]
    monkeypatch.setattr(store, "COUNTS_DEADLINE_SECONDS", 0)  # budget already used up
    assert await _counts(env) == {str(ids[0]): None, str(ids[1]): None}


# ---------------------------------------------------------------- delete, import upsert


async def test_delete_cascades_prefs_and_groups(env: Env) -> None:
    env.as_user(SEARCHER)
    made = await _create(env, "rep48 D", group_ids=[G_SHARED])
    sid = made["id"]
    env.as_user(COLLEAGUE)
    await env.client.put(
        f"/api/v1/saved-searches/{sid}/prefs", json={"show_in_sidebar": True, "show_count": False}
    )
    env.as_user(SEARCHER)
    prefs_sql = f"SELECT COUNT(*) FROM tiqora_saved_search_pref WHERE saved_search_id = {sid}"
    groups_sql = f"SELECT COUNT(*) FROM tiqora_saved_search_group WHERE saved_search_id = {sid}"
    assert _scalar(env.sync_url, prefs_sql) == 2 and _scalar(env.sync_url, groups_sql) == 1
    assert (await env.client.delete(f"/api/v1/saved-searches/{sid}")).status_code == 204
    assert _scalar(env.sync_url, prefs_sql) == 0 and _scalar(env.sync_url, groups_sql) == 0
    assert _scalar(env.sync_url, f"SELECT COUNT(*) FROM tiqora_saved_search WHERE id = {sid}") == 0
    assert (await env.client.get(f"/api/v1/saved-searches/{sid}")).status_code == 404


async def test_upsert_imported_creates_then_updates_and_survives_a_race(env: Env) -> None:
    from tiqora.filters import Filter

    flt = Filter.model_validate(Q210)
    other = Filter.model_validate({"where": {"field": "queue_id", "op": "eq", "value": Q_SEARCHER}})
    engine = create_async_engine(_to_async_url(env.sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as s:
            sid, how = await store.upsert_imported(
                s, SEARCHER, source_name="rep48 Profil", criteria=flt, include_archived=False
            )
            await s.commit()
        assert how == "created"
        async with factory() as s:
            sid2, how2 = await store.upsert_imported(
                s, SEARCHER, source_name="rep48 Profil", criteria=other, include_archived=True
            )
            await s.commit()
        assert (sid2, how2) == (sid, "updated")

        # race: the first lookup finds nothing, the insert hits the unique index
        async with factory() as s:
            real_find = store._find_imported
            calls = {"n": 0}

            async def _blind(session: AsyncSession, user_id: int, name: str, **kw: Any) -> Any:
                calls["n"] += 1
                return None if calls["n"] == 1 else await real_find(session, user_id, name, **kw)

            store._find_imported = _blind  # type: ignore[assignment]
            try:
                sid3, how3 = await store.upsert_imported(
                    s, SEARCHER, source_name="rep48 Profil", criteria=flt, include_archived=False
                )
            finally:
                store._find_imported = real_find  # type: ignore[assignment]
            await s.commit()
        assert (sid3, how3) == (sid, "updated")
    finally:
        await engine.dispose()
    env.as_user(SEARCHER)
    item = (await env.client.get(f"/api/v1/saved-searches/{sid}")).json()
    assert item["source"] == "znuny" and item["source_name"] == "rep48 Profil"
    assert item["name"] == "rep48 Profil" and item["version"] == 3
    assert item["criteria"] == Q210 | {"version": 1} and item["include_archived"] is False
    assert (
        _scalar(
            env.sync_url,
            "SELECT COUNT(*) FROM tiqora_saved_search WHERE source_name = 'rep48 Profil'",
        )
        == 1
    )


async def test_upsert_imported_race_between_two_open_transactions(env: Env) -> None:
    """Session B's snapshot predates A's commit (MariaDB REPEATABLE READ): B's plain lookup
    finds nothing, its insert hits the unique index, the locking re-read must see A's row."""
    from sqlalchemy import select

    from tiqora.db.tiqora.models import TiqoraSavedSearch
    from tiqora.filters import Filter

    first = Filter.model_validate(Q210)
    second = Filter.model_validate(
        {"where": {"field": "queue_id", "op": "eq", "value": Q_SEARCHER}}
    )
    engine = create_async_engine(_to_async_url(env.sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as a, factory() as b:
            # both transactions start (consistent snapshot) before either writes
            await a.execute(select(TiqoraSavedSearch.id).limit(1))
            await b.execute(select(TiqoraSavedSearch.id).limit(1))
            sid_a, how_a = await store.upsert_imported(
                a, SEARCHER, source_name="rep48 Race", criteria=first, include_archived=False
            )
            await a.commit()
            sid_b, how_b = await store.upsert_imported(
                b, SEARCHER, source_name="rep48 Race", criteria=second, include_archived=True
            )
            await b.commit()
    finally:
        await engine.dispose()
    assert how_a == "created" and (sid_b, how_b) == (sid_a, "updated")
    env.as_user(SEARCHER)
    item = (await env.client.get(f"/api/v1/saved-searches/{sid_a}")).json()
    assert item["version"] == 2 and item["include_archived"] is True
    assert item["criteria"]["where"]["value"] == Q_SEARCHER
    count_sql = "SELECT COUNT(*) FROM tiqora_saved_search WHERE source_name = 'rep48 Race'"
    assert _scalar(env.sync_url, count_sql) == 1


async def test_counts_still_running_at_the_deadline_are_cancelled(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import time

    async def _hang(*_a: Any, **_k: Any) -> int:
        await asyncio.sleep(60)
        return 99

    monkeypatch.setattr(saved_searches_api, "count_tickets", _hang)
    monkeypatch.setattr(store, "COUNTS_DEADLINE_SECONDS", 2)
    env.as_user(SEARCHER)
    ids = [(await _create(env, f"rep48 H{i}", show_count=True))["id"] for i in range(2)]
    started = time.monotonic()
    counts = await _counts(env)
    assert counts == {str(ids[0]): None, str(ids[1]): None}
    assert time.monotonic() - started < 10
    assert env.cache.store == {}
