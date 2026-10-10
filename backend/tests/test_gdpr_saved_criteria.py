"""GDPR: erased customer logins / company ids inside saved searches and saved reports.

Pure tree walker plus both dialects. Seed ids 48290-48319 (names/logins contain ``rep48``):
saved owner 48291, searches 48290 / 48293-48296, reports 48292 / 48297-48299 (48299: company
only), ticket 48300, customer users 48310 / 48311. Every row the module (and the erasure
runs) writes is deleted again.
"""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator, Generator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.gdpr import erasure
from tiqora.gdpr.erasure import ErasureResult, rollback_job, run_erasure
from tiqora.gdpr.saved_criteria import replace_filter_values, scrub_saved_criteria

LO, HI = 48290, 48319
OWNER = 48291
S_EQ, S_OTHER, S_BAD, S_TITLE, S_CO = 48290, 48293, 48294, 48295, 48296
R_MAIN, R_DELETED, R_OTHER, R_CO = 48292, 48297, 48298, 48299
TICKET, CU_ERASE, CU_KEEP = 48300, 48310, 48311
LOGIN = "rep48.erase@example.org"
OTHER_LOGIN = "other@example.org"
COMPANY = "REP48-CO"
NOW = datetime(2026, 1, 1, 12, 0, 0)
BAND = {"lo": LO, "hi": HI}
_JOBS = "SELECT id FROM tiqora_gdpr_job WHERE actor = 'rep48'"
_CLEANUP = (
    f"DELETE FROM tiqora_gdpr_backup WHERE job_id IN ({_JOBS})",
    "DELETE FROM tiqora_gdpr_job WHERE actor = 'rep48'",
    "DELETE FROM tiqora_gdpr_audit WHERE actor = 'rep48'",
    "DELETE FROM tiqora_cache_invalidation"
    " WHERE ticket_id IS NULL OR ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_event_outbox WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_dirty_ticket WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_group WHERE report_id IN"
    " (SELECT id FROM tiqora_report WHERE owner_user_id BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_report WHERE owner_user_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_saved_search_pref WHERE saved_search_id IN"
    " (SELECT id FROM tiqora_saved_search WHERE owner_user_id BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_saved_search WHERE owner_user_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM customer_user WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM customer_company WHERE customer_id LIKE 'REP48-%'",
)
_URLS: set[str] = set()


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
            conn.execute(text(stmt), BAND)
    engine.dispose()


def _scalar(sync_url: str, sql: str) -> Any:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        value = conn.execute(text(sql)).scalar_one()
    engine.dispose()
    return value


@dataclass
class Env:
    sync_url: str
    factory: async_sessionmaker[AsyncSession]


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _run_sql(url, _CLEANUP)
    _URLS.clear()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    _run_sql(sync_url, _CLEANUP)
    try:
        yield Env(sync_url, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False))
    finally:
        _run_sql(sync_url, _CLEANUP)
        await engine.dispose()


def _eq(field: str, value: Any, op: str = "eq") -> dict[str, Any]:
    return {"field": field, "op": op, "value": value}


def _insert_search(conn: Any, sid: int, name: str, criteria: str) -> None:
    conn.execute(
        text(
            "INSERT INTO tiqora_saved_search (id, owner_user_id, name, criteria, include_archived,"
            " sort_by, sort_order, source, version, created_at, changed_at)"
            " VALUES (:id, :o, :n, :c, :arch, 'age', 'desc', 'tiqora', 1, :t, :t)"
        ),
        {"id": sid, "o": OWNER, "n": name, "c": criteria, "arch": False, "t": NOW},
    )


def _insert_report(conn: Any, rid: int, name: str, definition: dict[str, Any], valid: int) -> None:
    conn.execute(
        text(
            "INSERT INTO tiqora_report (id, name, owner_user_id, visibility, definition,"
            " schema_version, version, valid, created_at, created_by, changed_at, changed_by)"
            " VALUES (:id, :n, :o, 'private', :d, 1, 1, :v, :t, :o, :t, :o)"
        ),
        {"id": rid, "n": name, "o": OWNER, "d": json.dumps(definition), "v": bool(valid), "t": NOW},
    )


def _report_definition(login_filter: dict[str, Any], who: str = LOGIN) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "tickets",
        "dimensions": [{"key": "queue"}],
        "measures": [{"key": "count"}],
        "filter": {"version": 1, "where": login_filter},
        "parameters": [
            {
                "key": "who",
                "field": "customer_user",
                "op": "in",
                "allowed_values": [who.upper(), "keep@example.org"],
                "default": [who],
            }
        ],
    }


def _seed_rows(sync_url: str) -> None:
    """Searches 48290/48293-48296 and reports 48292/48297/48298, all owned by 48291."""
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        # eq on the erased login (+ an untouched title condition)
        _insert_search(
            conn,
            S_EQ,
            "rep48 g1",
            json.dumps(
                {"version": 1, "where": {"all": [_eq("customer_user_id", LOGIN),
                                                 _eq("title", LOGIN, "contains")]}}
            ),
        )  # fmt: skip
        # only mentions somebody else
        _insert_search(
            conn, S_OTHER, "rep48 other",
            json.dumps({"version": 1, "where": _eq("customer_user_id", OTHER_LOGIN)}),
        )  # fmt: skip
        _insert_search(conn, S_BAD, "rep48 broken", "{")
        # the login only appears in a free-text condition: never scrubbed
        _insert_search(
            conn, S_TITLE, "rep48 title",
            json.dumps({"version": 1, "where": _eq("title", LOGIN, "contains")}),
        )  # fmt: skip
        # company id inside a Not
        _insert_search(
            conn, S_CO, "rep48 company",
            json.dumps({"version": 1, "where": {"not": _eq("customer_id", COMPANY)}}),
        )  # fmt: skip
        _insert_report(
            conn, R_MAIN, "rep48 report",
            _report_definition({"any": [_eq("customer_user", [LOGIN, OTHER_LOGIN], "in")]}), 1,
        )  # fmt: skip
        _insert_report(
            conn, R_DELETED, "rep48 deleted",
            _report_definition(_eq("customer_user", LOGIN.upper())), 0,
        )  # fmt: skip
        _insert_report(
            conn, R_OTHER, "rep48 untouched",
            _report_definition(_eq("customer_user", OTHER_LOGIN), OTHER_LOGIN), 1,
        )  # fmt: skip
        # company only: a `customer` filter (in list) and a `customer` parameter
        co_def = _report_definition({"all": [_eq("customer", [COMPANY, "REP48-KEEP"], "in")]})
        co_def["parameters"] = [
            {"key": "co", "field": "customer", "op": "in",
             "allowed_values": [COMPANY.lower(), "REP48-KEEP"], "default": [COMPANY]}
        ]  # fmt: skip
        _insert_report(conn, R_CO, "rep48 company report", co_def, 1)
    engine.dispose()


def _criteria(sync_url: str, sid: int) -> str:
    return str(_scalar(sync_url, f"SELECT criteria FROM tiqora_saved_search WHERE id = {sid}"))


def _definition(sync_url: str, rid: int) -> dict[str, Any]:
    raw = _scalar(sync_url, f"SELECT definition FROM tiqora_report WHERE id = {rid}")
    return json.loads(raw)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# pure tree walker
# ---------------------------------------------------------------------------


def test_replace_filter_values_walks_tree_case_insensitively() -> None:
    where = {"any": [
        {"field": "customer_user_id", "op": "in", "value": ["Max@Example.org", "keep@example.org"]},
        {"not": {"field": "customer_user_id", "op": "eq", "value": "max@example.org"}},
        {"field": "title", "op": "contains", "value": "max@example.org"}]}  # fmt: skip
    original = copy.deepcopy(where)
    new, changed = replace_filter_values(
        where, frozenset({"customer_user_id"}), {"max@example.org": "anon-1"}
    )
    assert changed is True
    assert new == {"any": [
        {"field": "customer_user_id", "op": "in", "value": ["anon-1", "keep@example.org"]},
        {"not": {"field": "customer_user_id", "op": "eq", "value": "anon-1"}},
        {"field": "title", "op": "contains", "value": "max@example.org"}]}  # fmt: skip
    assert where == original  # input is not mutated
    assert replace_filter_values(None, frozenset({"customer_user_id"}), {"a": "b"}) == (None, False)


def test_replace_filter_values_nested_all_any_not_and_no_match() -> None:
    fields = frozenset({"customer_id"})
    where = {"all": [{"any": [{"not": {"not": _eq("customer_id", "ACME")}}, _eq("queue_id", 3)]},
                     _eq("customer_id", "Other", "neq")]}  # fmt: skip
    new, changed = replace_filter_values(where, fields, {"acme": "tok-9"})
    assert changed is True
    assert new["all"][0]["any"][0]["not"]["not"]["value"] == "tok-9"
    assert new["all"][0]["any"][1] == _eq("queue_id", 3)
    assert new["all"][1] == _eq("customer_id", "Other", "neq")
    same, changed = replace_filter_values(where, fields, {"nobody": "x"})
    assert (same, changed) == (where, False)
    # a non-string value (number) never matches, and a missing value key is fine
    odd = {"field": "customer_id", "op": "is_empty"}
    assert replace_filter_values(odd, fields, {"acme": "t"}) == (odd, False)
    assert replace_filter_values(_eq("customer_id", 7), fields, {"7": "t"})[1] is False


# ---------------------------------------------------------------------------
# scrub_saved_criteria on both dialects
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_scrub_rewrites_searches_and_report_filters_and_parameters(env: Env) -> None:
    _seed_rows(env.sync_url)
    backed_up: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def _backup(table: str, pk: dict[str, Any], original: dict[str, Any]) -> None:
        backed_up.append((table, pk, original))

    async with env.factory() as s:
        n = await scrub_saved_criteria(s, {LOGIN: "anon-1"}, backup=_backup)
        await s.commit()
    # search g1 + reports 48292 and (soft-deleted) 48297; company id not requested
    assert n == 3
    crit = json.loads(_criteria(env.sync_url, S_EQ))
    assert crit["where"]["all"][0]["value"] == "anon-1"
    assert crit["where"]["all"][1]["value"] == LOGIN  # free text stays
    defn = _definition(env.sync_url, R_MAIN)
    assert defn["filter"]["where"]["any"][0]["value"] == ["anon-1", OTHER_LOGIN]
    assert defn["parameters"][0]["allowed_values"] == ["anon-1", "keep@example.org"]
    assert defn["parameters"][0]["default"] == ["anon-1"]
    assert _scalar(env.sync_url, f"SELECT version FROM tiqora_report WHERE id = {R_MAIN}") == 2
    assert _scalar(env.sync_url, f"SELECT version FROM tiqora_saved_search WHERE id = {S_EQ}") == 2
    # soft-deleted report is rewritten too
    assert _definition(env.sync_url, R_DELETED)["filter"]["where"]["value"] == "anon-1"
    assert _scalar(env.sync_url, f"SELECT valid FROM tiqora_report WHERE id = {R_DELETED}") in (
        0,
        False,
    )
    # untouched rows: byte-identical, version still 1
    for sid in (S_OTHER, S_TITLE, S_CO):
        assert (
            _scalar(env.sync_url, f"SELECT version FROM tiqora_saved_search WHERE id = {sid}") == 1
        )
    assert _criteria(env.sync_url, S_BAD) == "{"
    assert _scalar(env.sync_url, f"SELECT version FROM tiqora_report WHERE id = {R_OTHER}") == 1
    assert _definition(env.sync_url, R_OTHER)["filter"]["where"]["value"] == OTHER_LOGIN
    # every changed row handed its original to the backup callback
    assert sorted((t, p["id"]) for t, p, _ in backed_up) == [
        ("tiqora_report", R_MAIN), ("tiqora_report", R_DELETED), ("tiqora_saved_search", S_EQ)
    ]  # fmt: skip
    by_id = {p["id"]: o for t, p, o in backed_up if t == "tiqora_saved_search"}
    assert LOGIN in by_id[S_EQ]["criteria"] and by_id[S_EQ]["version"] == 1


@pytest.mark.db
async def test_scrub_rewrites_company_ids_only_when_given(env: Env) -> None:
    _seed_rows(env.sync_url)
    async with env.factory() as s:
        assert await scrub_saved_criteria(s, {"nobody@example.org": "x"}) == 0
        n = await scrub_saved_criteria(s, {}, {COMPANY: "co-tok"})
        await s.commit()
    assert n == 2  # search 48296 and report 48299
    assert json.loads(_criteria(env.sync_url, S_CO))["where"]["not"]["value"] == "co-tok"
    co = _definition(env.sync_url, R_CO)
    assert co["filter"]["where"]["all"][0]["value"] == ["co-tok", "REP48-KEEP"]
    assert co["parameters"][0]["allowed_values"] == ["co-tok", "REP48-KEEP"]
    assert co["parameters"][0]["default"] == ["co-tok"]
    assert _scalar(env.sync_url, f"SELECT version FROM tiqora_report WHERE id = {R_CO}") == 2
    # a report `customer` filter is rewritten with the company map, a login is not touched by it
    async with env.factory() as s:
        n = await scrub_saved_criteria(s, {}, {OTHER_LOGIN: "never"})
        assert n == 0


@pytest.mark.db
async def test_scrub_rolls_back_with_the_caller_transaction(env: Env) -> None:
    _seed_rows(env.sync_url)
    before = _criteria(env.sync_url, S_EQ)
    async with env.factory() as s:
        assert await scrub_saved_criteria(s, {LOGIN: "anon-1"}) == 3
        await s.rollback()
    assert _criteria(env.sync_url, S_EQ) == before
    assert _scalar(env.sync_url, f"SELECT version FROM tiqora_report WHERE id = {R_MAIN}") == 1


# ---------------------------------------------------------------------------
# run_erasure integration
# ---------------------------------------------------------------------------


def _seed_customer(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO customer_company (customer_id, name, street, zip, city, country,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:c, 'Rep48 Co', 'Main 1', '10115', 'Berlin', 'DE', 1, :t, 1, :t, 1)"
            ),
            {"c": COMPANY, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO customer_user (id, login, email, customer_id, first_name, last_name,"
                " phone, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :l, :l, :c, 'Target', 'Person', '+49 30 1234567', 1, :t, 1, :t, 1)"
            ),
            {"id": CU_ERASE, "l": LOGIN, "c": COMPANY, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
                " responsible_user_id, ticket_priority_id, ticket_state_id, customer_id,"
                " customer_user_id, timeout, until_time, escalation_time, escalation_update_time,"
                " escalation_response_time, escalation_solution_time, archive_flag,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, '2026010148300', 'rep48 erase me', 1, 1, 1, 1, 1, 3, 4, :c, :l,"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {"id": TICKET, "c": COMPANY, "l": LOGIN, "t": NOW},
        )
    engine.dispose()


async def _run_small_anonymise(
    env: Env, *, login: str = LOGIN, mode: str = "anonymize", delete_tickets: bool = False
) -> ErasureResult:
    """Seed one customer user (+ company, one ticket) and the saved rows, then erase it."""
    assert login == LOGIN  # the seeds above are keyed to this login
    _seed_customer(env.sync_url)
    _seed_rows(env.sync_url)
    return await _erase(env, mode=mode, delete_tickets=delete_tickets)


async def _erase(
    env: Env, *, mode: str = "anonymize", delete_tickets: bool = False
) -> ErasureResult:
    return await run_erasure(
        env.factory,
        Settings(),
        customer_user_ids=[CU_ERASE],
        mode=mode,  # type: ignore[arg-type]
        seed=7,
        actor="rep48",
        force_parallel=True,
        delete_tickets=delete_tickets,
    )


@pytest.mark.db
async def test_erasure_anonymise_calls_scrub(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[dict[str, str], dict[str, str] | None]] = []

    async def _spy(session: Any, mapping: Any, company: Any = None, **kw: Any) -> int:
        seen.append((dict(mapping), None if company is None else dict(company)))
        assert callable(kw["backup"])
        return 0

    monkeypatch.setattr(erasure, "scrub_saved_criteria", _spy)
    await _run_small_anonymise(env, login=LOGIN)
    assert len(seen) == 1
    mapping, company = seen[0]
    assert list(mapping) == [LOGIN]
    assert mapping[LOGIN] != LOGIN
    assert company is None  # anonymize keeps the company primary key
    # exactly what the ticket received
    ticket_login = _scalar(env.sync_url, f"SELECT customer_user_id FROM ticket WHERE id = {TICKET}")
    assert ticket_login == mapping[LOGIN]


@pytest.mark.db
async def test_erasure_anonymise_rewrites_saved_criteria_and_rollback_restores(env: Env) -> None:
    result = await _run_small_anonymise(env)
    new_login = _scalar(env.sync_url, f"SELECT customer_user_id FROM ticket WHERE id = {TICKET}")
    assert new_login != LOGIN
    assert result.counts["saved_criteria"] == 3
    assert json.loads(_criteria(env.sync_url, S_EQ))["where"]["all"][0]["value"] == new_login
    assert _definition(env.sync_url, R_MAIN)["parameters"][0]["default"] == [new_login]
    # the company id is kept in anonymize mode
    assert json.loads(_criteria(env.sync_url, S_CO))["where"]["not"]["value"] == COMPANY
    assert _definition(env.sync_url, R_CO)["filter"]["where"]["all"][0]["value"][0] == COMPANY
    assert _scalar(env.sync_url, f"SELECT version FROM tiqora_report WHERE id = {R_CO}") == 1

    await rollback_job(env.factory, Settings(), result.job_id, force_parallel=True, actor="rep48")
    assert json.loads(_criteria(env.sync_url, S_EQ))["where"]["all"][0]["value"] == LOGIN
    assert _definition(env.sync_url, R_MAIN)["parameters"][0]["default"] == [LOGIN]
    # content restored, version moves forward (1 -> 2 by the erasure -> 3): open editors get 409
    assert _scalar(env.sync_url, f"SELECT version FROM tiqora_report WHERE id = {R_MAIN}") == 3
    assert (
        _scalar(env.sync_url, f"SELECT customer_user_id FROM ticket WHERE id = {TICKET}") == LOGIN
    )


@pytest.mark.db
async def test_erasure_delete_mode_uses_the_ticket_tokens(env: Env) -> None:
    result = await _run_small_anonymise(env, mode="delete")
    token_login = _scalar(env.sync_url, f"SELECT customer_user_id FROM ticket WHERE id = {TICKET}")
    token_company = _scalar(env.sync_url, f"SELECT customer_id FROM ticket WHERE id = {TICKET}")
    assert token_login != LOGIN and token_company != COMPANY
    assert "geloescht-" not in token_login
    assert json.loads(_criteria(env.sync_url, S_EQ))["where"]["all"][0]["value"] == token_login
    assert json.loads(_criteria(env.sync_url, S_CO))["where"]["not"]["value"] == token_company
    assert _definition(env.sync_url, R_DELETED)["filter"]["where"]["value"] == token_login
    # report `customer` filter and parameter follow the ticket's company token
    co = _definition(env.sync_url, R_CO)
    assert co["filter"]["where"]["all"][0]["value"] == [token_company, "REP48-KEEP"]
    assert co["parameters"][0]["allowed_values"] == [token_company, "REP48-KEEP"]
    assert co["parameters"][0]["default"] == [token_company]
    assert result.counts["saved_criteria"] == 5
    # rollback brings back the master row, the ticket and the saved criteria
    await rollback_job(env.factory, Settings(), result.job_id, force_parallel=True, actor="rep48")
    assert json.loads(_criteria(env.sync_url, S_CO))["where"]["not"]["value"] == COMPANY
    assert json.loads(_criteria(env.sync_url, S_EQ))["where"]["all"][0]["value"] == LOGIN
    co = _definition(env.sync_url, R_CO)
    assert co["filter"]["where"]["all"][0]["value"] == [COMPANY, "REP48-KEEP"]
    assert co["parameters"][0]["allowed_values"] == [COMPANY.lower(), "REP48-KEEP"]
    assert co["parameters"][0]["default"] == [COMPANY]
    assert _scalar(env.sync_url, f"SELECT version FROM tiqora_report WHERE id = {R_CO}") == 3


@pytest.mark.db
async def test_erasure_with_ticket_delete_rolls_back_saved_criteria_by_update(env: Env) -> None:
    result = await _run_small_anonymise(env, mode="delete", delete_tickets=True)
    assert _scalar(env.sync_url, f"SELECT COUNT(*) FROM ticket WHERE id = {TICKET}") == 0
    assert LOGIN not in json.dumps(json.loads(_criteria(env.sync_url, S_EQ))["where"]["all"][0])
    await rollback_job(env.factory, Settings(), result.job_id, force_parallel=True, actor="rep48")
    assert _scalar(env.sync_url, f"SELECT COUNT(*) FROM ticket WHERE id = {TICKET}") == 1
    assert json.loads(_criteria(env.sync_url, S_EQ))["where"]["all"][0]["value"] == LOGIN
    assert (
        _scalar(
            env.sync_url, f"SELECT COUNT(*) FROM tiqora_saved_search WHERE owner_user_id = {OWNER}"
        )
        == 5
    )


@pytest.mark.db
async def test_failed_erasure_rolls_back_the_saved_criteria_rewrite(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(session: Any, ticket_ids: Any) -> None:
        raise RuntimeError("boom after the scrub")

    monkeypatch.setattr(erasure, "_refresh_report_facts", _boom)
    _seed_customer(env.sync_url)
    _seed_rows(env.sync_url)
    before = _criteria(env.sync_url, S_EQ)
    with pytest.raises(RuntimeError, match="boom"):
        await _erase(env)
    assert _criteria(env.sync_url, S_EQ) == before
    assert (
        _scalar(env.sync_url, f"SELECT customer_user_id FROM ticket WHERE id = {TICKET}") == LOGIN
    )
    assert _scalar(env.sync_url, f"SELECT version FROM tiqora_report WHERE id = {R_MAIN}") == 1
