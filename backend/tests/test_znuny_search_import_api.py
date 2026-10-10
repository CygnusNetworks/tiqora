"""Tests for the read-only Znuny search-profile import (both dialects).

Seed ids 48250-48289 (logins/names contain ``rep48``): agent 48250 ``rep48.Import`` (mixed case
on purpose; ``ro`` on group/queue 48260), tickets 48270 (open) and 48271 (closed) in queue 48260,
dynamic field 48280 ``Rep48Ref`` of a type the filter catalogue does not know. ``search_profile``
has no id column: its test rows are found by login (``TicketSearch::rep48...``) and deleted
again. Every row the module writes (also the saved rows created through the API) is removed.
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
from tiqora.db.legacy.base import LegacyBase
from tiqora.db.legacy.misc import SEARCH_PROFILE
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.filters import FilterError
from tiqora.saved_searches.znuny_import import ARCHIVE_REASON, DF_TYPE_REASON
from tiqora.saved_searches.znuny_reader import load_lookups, load_znuny_profiles

pytestmark = pytest.mark.db

LO, HI = 48250, 48289
AGENT, QUEUE, GROUP = 48250, 48260, 48260
T_OPEN, T_CLOSED = 48270, 48271
DF_ID = 48280
NOW = datetime(2026, 1, 1, 12, 0, 0)
LOGIN = "TicketSearch::rep48.Import"
USER = AuthenticatedUser(
    id=AGENT, login="rep48.Import", first_name="Ina", last_name="Import", auth_method="session"
)
_BAND = {"lo": LO, "hi": HI}
_OWNED = "SELECT id FROM tiqora_saved_search WHERE owner_user_id BETWEEN :lo AND :hi"
_PROFILES = (
    "DELETE FROM search_profile WHERE login LIKE 'TicketSearch::rep48%'"
    " OR login LIKE 'TicketSearch::REP48%'"
)
_RESET = (
    f"DELETE FROM tiqora_saved_search_pref WHERE saved_search_id IN ({_OWNED})"
    " OR user_id BETWEEN :lo AND :hi",
    f"DELETE FROM tiqora_saved_search_group WHERE saved_search_id IN ({_OWNED})",
    "DELETE FROM tiqora_saved_search WHERE owner_user_id BETWEEN :lo AND :hi",
    _PROFILES,
)
_CLEANUP = (
    *_RESET,
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM dynamic_field WHERE id = 48280",
    "DELETE FROM group_user WHERE user_id BETWEEN :lo AND :hi",
    "DELETE FROM queue WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM permission_groups WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM users WHERE id BETWEEN :lo AND :hi",
)
_URLS: set[str] = set()
_SEEDED: set[str] = set()

# (login, profile name, type, key, value)
_PROFILE_ROWS = (
    (LOGIN, "Offen Support", "ARRAY", "QueueIDs", "48260"),
    (LOGIN, "Offen Support", "ARRAY", "StateTypeIDs", "{open}"),
    (LOGIN, "Offen Support", "ARRAY", "ShownAttributes", "LabelTitle"),
    (LOGIN, "Volltext", "SCALAR", "Fulltext", "drucker"),
    (LOGIN, "last-search", "SCALAR", "Title", "x"),
    (LOGIN, "Gemischt", "SCALAR", "Title", "Drucker"),
    (LOGIN, "Gemischt", "SCALAR", "Subject", "y"),
    ("TicketSearch::REP48.IMPORT", "Gross", "SCALAR", "TicketNumber", "1*"),
)


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


def _rows(sync_url: str, sql: str) -> list[tuple[Any, ...]]:
    """All rows of ``sql`` as plain tuples (a read on a short-lived sync connection)."""
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        rows = [tuple(r) for r in conn.execute(text(sql)).all()]
    engine.dispose()
    return rows


def _insert_profile(
    sync_url: str, login: str, name: str, key: str, ptype: str, value: str | None
) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO search_profile (login, profile_name, profile_type, profile_key,"
                " profile_value) VALUES (:l, :n, :t, :k, :v)"
            ),
            {"l": login, "n": name, "t": ptype, "k": key, "v": value},
        )
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _run_sql(url, _CLEANUP)
    _URLS.clear()
    _SEEDED.clear()


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep48.Import', 'x', 'Ina', 'Import', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, 'rep48-group-48260', 1, :t, 1, :t, 1)"
            ),
            {"id": GROUP, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep48-queue-48260', :id, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": QUEUE, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:u, :g, 'ro', :t, 1, :t, 1)"
            ),
            {"u": AGENT, "g": GROUP, "t": NOW},
        )
        # state 4 = open (type open), state 2 = closed successful (type closed)
        for tid, state in ((T_OPEN, 4), (T_CLOSED, 2)):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, :title, :q, 1, 1, 1, 1, 3, :s, 'rep48-cust',"
                    " 'rep48.cust@example.org', 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"2026010{tid}", "title": f"rep48 ticket {tid}", "q": QUEUE,
                 "s": state, "t": NOW},
            )  # fmt: skip
        conn.execute(
            text(
                "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                " field_type, object_type, config, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, 0, 'Rep48Ref', 'Rep48Ref', 48280,"
                " 'Rep48UnknownType', 'Ticket', '---', 1, :t, 1, :t, 1)"
            ),
            {"id": DF_ID, "t": NOW},
        )
    engine.dispose()


def _seed_profiles(sync_url: str) -> None:
    open_type = _rows(sync_url, "SELECT id FROM ticket_state_type WHERE name = 'open'")[0][0]
    for login, name, ptype, key, value in _PROFILE_ROWS:
        _insert_profile(sync_url, login, name, key, ptype, value.replace("{open}", str(open_type)))


class _MemoryCache:
    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, ex: int) -> None:
        return None


class Env:
    def __init__(self, sync_url: str, client: AsyncClient, factory: Any) -> None:
        self.sync_url = sync_url
        self.client = client
        self.factory = factory
        self.user = USER

    def as_user(self, user_id: int) -> None:
        assert user_id == AGENT  # the module has a single agent
        self.user = USER


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
    _seed_profiles(sync_url)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    holder: list[Env] = []
    app.dependency_overrides[get_current_user] = lambda: holder[0].user
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_count_cache] = lambda: _MemoryCache()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            holder.append(Env(sync_url, c, factory))
            yield holder[0]
    finally:
        await engine.dispose()
        _run_sql(sync_url, _RESET)


_PROFILE_SQL = (
    "SELECT login, profile_name, profile_type, profile_key, profile_value"
    " FROM search_profile WHERE login LIKE 'TicketSearch::rep48%%' OR login LIKE"
    " 'TicketSearch::REP48%%' ORDER BY 1, 2, 4, 5"
)


def _values(node: Any) -> list[Any]:
    """Every dict value below ``node`` (conditions included), to look for stray ``None``."""
    if isinstance(node, dict):
        return [v for x in node.values() for v in [x, *_values(x)]]
    if isinstance(node, list):
        return [v for x in node for v in [x, *_values(x)]]
    return []


async def test_reader_groups_arrays_and_skips_last_search(env: Env) -> None:
    async with env.factory() as s:
        profiles = await load_znuny_profiles(s, "rep48.Import")
    assert set(profiles) == {"Offen Support", "Volltext", "Gemischt", "Gross"}
    assert profiles["Offen Support"]["QueueIDs"] == ["48260"]
    assert profiles["Volltext"]["Fulltext"] == "drucker"


async def test_reader_matches_login_case_insensitively(env: Env) -> None:
    async with env.factory() as s:
        profiles = await load_znuny_profiles(s, "rep48.import")
    assert profiles["Gross"] == {"TicketNumber": "1*"}
    # the same profile set is found whatever case the caller's login has
    async with env.factory() as s:
        assert set(await load_znuny_profiles(s, "REP48.IMPORT")) == set(profiles)


async def test_reader_null_value_is_empty_string_and_lookups(env: Env) -> None:
    _insert_profile(env.sync_url, LOGIN, "Leer", "Title", "SCALAR", None)
    async with env.factory() as s:
        profiles = await load_znuny_profiles(s, "rep48.Import")
        lookups = await load_lookups(s, AGENT)
    assert profiles["Leer"] == {"Title": ""}
    assert lookups.queue_names[QUEUE] == "rep48-queue-48260"
    assert "open" in lookups.state_type_names.values()
    assert {"unlock", "lock"} <= set(lookups.lock_names.values())
    assert lookups.dynamic_fields["Rep48Ref"] == "Rep48UnknownType"
    assert lookups.time_zone  # resolved through resolve_user_time_zone


async def test_preview_lists_mapped_and_unmapped(env: Env) -> None:
    env.as_user(AGENT)
    prev = (await env.client.get("/api/v1/saved-searches/import/znuny")).json()["profiles"]
    by = {p["name"]: p for p in prev}
    assert [p["name"] for p in prev] == sorted(by, key=str.casefold)
    assert by["Offen Support"]["importable"] is True and by["Offen Support"]["unmapped"] == []
    assert by["Offen Support"]["partial"] is False
    assert by["Volltext"]["importable"] is False
    assert by["Volltext"]["unmapped"][0]["key"] == "Fulltext"
    assert [u["key"] for u in by["Gemischt"]["unmapped"]] == ["Subject"]
    # R-PARTIAL: importable but flagged as only partly taken over
    assert by["Gemischt"]["importable"] is True and by["Gemischt"]["partial"] is True
    assert all(p["existing_id"] is None for p in prev)
    # no ``"value": null`` in the served conditions
    assert None not in _values(by["Offen Support"]["criteria"])


async def test_preview_reports_unsupported_df_type_per_key_and_archive_for_non_admin(
    env: Env,
) -> None:
    _insert_profile(env.sync_url, LOGIN, "Archiv", "SearchInArchive", "SCALAR", "ArchivedTickets")
    _insert_profile(env.sync_url, LOGIN, "Archiv", "Title", "SCALAR", "Alt")
    _insert_profile(env.sync_url, LOGIN, "Fremdfeld", "Search_DynamicField_Rep48Ref", "SCALAR", "a")
    _insert_profile(env.sync_url, LOGIN, "Fremdfeld", "Title", "SCALAR", "Neu")
    env.as_user(AGENT)
    prev = (await env.client.get("/api/v1/saved-searches/import/znuny")).json()["profiles"]
    by = {p["name"]: p for p in prev}
    assert by["Archiv"]["include_archived"] is False
    assert [(u["key"], u["reason"]) for u in by["Archiv"]["unmapped"]] == [
        ("SearchInArchive", ARCHIVE_REASON)
    ]
    # only that key is lost, the title condition survives
    assert by["Fremdfeld"]["importable"] is True
    assert [(u["key"], u["reason"]) for u in by["Fremdfeld"]["unmapped"]] == [
        ("Search_DynamicField_Rep48Ref", DF_TYPE_REASON)
    ]


async def test_apply_creates_then_updates_never_duplicates(env: Env) -> None:
    env.as_user(AGENT)
    first = (
        await env.client.post(
            "/api/v1/saved-searches/import/znuny",
            json={"names": ["Offen Support", "Volltext", "Gibt es nicht"]},
        )
    ).json()
    assert len(first["created"]) == 1 and first["updated"] == []
    assert {s["name"]: s["reason"] for s in first["skipped"]} == {
        "Volltext": "Keine Bedingung übernehmbar.",
        "Gibt es nicht": "Suchvorlage nicht gefunden.",
    }
    second = (
        await env.client.post(
            "/api/v1/saved-searches/import/znuny", json={"names": ["Offen Support"]}
        )
    ).json()
    assert second["updated"] == first["created"] and second["created"] == []
    mine = [
        s for s in (await env.client.get("/api/v1/saved-searches")).json() if s["source"] == "znuny"
    ]
    assert len(mine) == 1 and mine[0]["source_name"] == "Offen Support"
    assert mine[0]["version"] == 2
    assert mine[0]["show_in_sidebar"] is True and mine[0]["group_ids"] == []
    # the imported criteria run and list exactly the open ticket (not the closed one)
    page = (
        await env.client.post("/api/v1/tickets/query", json={"filter": mine[0]["criteria"]})
    ).json()
    assert [t["id"] for t in page["items"]] == [T_OPEN]
    # the preview now points at the imported search
    prev = (await env.client.get("/api/v1/saved-searches/import/znuny")).json()["profiles"]
    assert {p["name"]: p["existing_id"] for p in prev}["Offen Support"] == mine[0]["id"]


async def test_apply_partial_profile_is_imported_with_mapped_part(env: Env) -> None:
    env.as_user(AGENT)
    res = (
        await env.client.post("/api/v1/saved-searches/import/znuny", json={"names": ["Gemischt"]})
    ).json()
    assert len(res["created"]) == 1 and res["skipped"] == []


async def test_apply_skips_profile_whose_criteria_fail_validation(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(session: Any, flt: Any) -> None:
        raise FilterError("Feld ist nicht mehr vorhanden.", path="where")

    monkeypatch.setattr(saved_searches_api.store, "validate_criteria", _boom)
    env.as_user(AGENT)
    prev = (await env.client.get("/api/v1/saved-searches/import/znuny")).json()["profiles"]
    offen = next(p for p in prev if p["name"] == "Offen Support")
    assert offen["importable"] is False
    assert offen["unmapped"][-1] == {
        "key": "criteria",
        "value": "",
        "reason": "Feld ist nicht mehr vorhanden.",
    }
    res = (
        await env.client.post(
            "/api/v1/saved-searches/import/znuny", json={"names": ["Offen Support"]}
        )
    ).json()
    assert res["created"] == [] and res["skipped"] == [
        {"name": "Offen Support", "reason": "Feld ist nicht mehr vorhanden."}
    ]
    assert (
        _rows(env.sync_url, f"SELECT id FROM tiqora_saved_search WHERE owner_user_id = {AGENT}")
        == []
    )


async def test_apply_validates_body(env: Env) -> None:
    env.as_user(AGENT)
    assert (
        await env.client.post("/api/v1/saved-searches/import/znuny", json={"names": []})
    ).status_code == 422


async def test_import_never_writes_search_profile(env: Env) -> None:
    before = _rows(env.sync_url, _PROFILE_SQL)
    assert len(before) == len(_PROFILE_ROWS)  # every seeded row is there to be compared
    env.as_user(AGENT)
    await env.client.get("/api/v1/saved-searches/import/znuny")
    await env.client.post(
        "/api/v1/saved-searches/import/znuny", json={"names": ["Offen Support", "Gemischt"]}
    )
    after = _rows(env.sync_url, _PROFILE_SQL)
    assert after == before


async def test_other_users_profiles_are_invisible(env: Env) -> None:
    _insert_profile(
        env.sync_url, "TicketSearch::rep48.someoneelse", "Fremd", "Title", "SCALAR", "x"
    )
    env.as_user(AGENT)
    names = {
        p["name"]
        for p in (await env.client.get("/api/v1/saved-searches/import/znuny")).json()["profiles"]
    }
    assert "Fremd" not in names
    res = (
        await env.client.post("/api/v1/saved-searches/import/znuny", json={"names": ["Fremd"]})
    ).json()
    assert res["created"] == [] and res["skipped"] == [
        {"name": "Fremd", "reason": "Suchvorlage nicht gefunden."}
    ]


def test_search_profile_table_is_not_in_legacy_metadata() -> None:
    assert "search_profile" not in LegacyBase.metadata.tables
    assert SEARCH_PROFILE.metadata is not LegacyBase.metadata
    assert SEARCH_PROFILE.metadata is not TiqoraBase.metadata
    assert {c.name for c in SEARCH_PROFILE.columns} == {
        "login",
        "profile_name",
        "profile_type",
        "profile_key",
        "profile_value",
    }
