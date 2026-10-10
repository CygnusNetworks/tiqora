"""Run saved reports by key; run service and dashboard seam (plan Task 4).

Both dialects. Band 48120-48169 (names contain ``rep48``): users 48120 ``rep48.runowner``
(``rw`` on Znuny's ``stats`` group, ``ro`` on group 48130 = queue 48130), 48121
``rep48.runviewer`` (``ro`` on group 48131 = queue 48131 only), 48122 ``rep48.runpeer``
(``ro`` on group 48130, like the owner); tickets 48140 (queue 48130, June 2024), 48141
(queue 48131, June 2024), 48142 (queue 48130, customer ``rep48-cust-b``). Every row this module
writes (report rows, jobs, state, settings, seeds) is deleted again.
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
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.reports import get_report_cache
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions import get_registry
from tiqora.filters import Filter
from tiqora.jobs.runner import JobRunner
from tiqora.reporting.definition import ReportDefinition
from tiqora.reporting.engine import QueryBudget
from tiqora.reporting.facts_store import rebuild_ticket_facts
from tiqora.reporting.run_service import (
    MAX_ACTIVE_REPORT_JOBS_PER_USER,
    ReportRunPayload,
    RunOverrides,
    TooManyReportJobs,
    enqueue_report_ref,
    run_report_ref,
)
from tiqora.reporting.schemas import ReportRunIn
from tiqora.reporting.settings import REPORTING_SECTION, load_reporting_settings
from tiqora.reporting.state import load_state
from tiqora.settings_sections.store import save_section_values

pytestmark = pytest.mark.db

LO, HI = 48120, 48169
OWNER, VIEWER, PEER = 48120, 48121, 48122
G30, G31 = 48130, 48131
T_A, T_B, T_C = 48140, 48141, 48142
TICKETS = (T_A, T_B, T_C)
BUILT = datetime(2024, 6, 20, 11, 0)
JUNE = {"kind": "absolute", "start": "2024-06-01", "end": "2024-06-30"}
LOGINS = {OWNER: "rep48.runowner", VIEWER: "rep48.runviewer", PEER: "rep48.runpeer"}
NAMES = {OWNER: ("Rhea", "Runowner"), VIEWER: ("Vera", "Runviewer"), PEER: ("Paul", "Runpeer")}
_BAND = {"lo": LO, "hi": HI}
_RUN_JOBS = "kind IN ('core.report_run', 'core.report_export') AND created_by BETWEEN :lo AND :hi"
_RESET = (
    "DELETE FROM tiqora_report_group WHERE report_id IN"
    " (SELECT id FROM tiqora_report WHERE owner_user_id BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_report WHERE owner_user_id BETWEEN :lo AND :hi",
    f"DELETE FROM tiqora_job_file WHERE job_id IN (SELECT id FROM tiqora_job WHERE {_RUN_JOBS})",
    f"DELETE FROM tiqora_job WHERE {_RUN_JOBS}",
    "DELETE FROM tiqora_report_state",
    "DELETE FROM tiqora_settings WHERE {key} LIKE 'reporting.%'",
)
_CLEANUP = (
    *_RESET,
    "DELETE FROM tiqora_report_ticket_event WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_fact WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_dirty_ticket WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM article WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
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
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _run_sql(url, _CLEANUP)
    _URLS.clear()
    _SEEDED.clear()


def user(uid: int) -> AuthenticatedUser:
    first, last = NAMES[uid]
    return AuthenticatedUser(
        id=uid, login=LOGINS[uid], first_name=first, last_name=last, auth_method="session"
    )


def _id(conn: Any, sql: str) -> int:
    return int(conn.execute(text(sql)).scalar_one())


def _seed(sync_url: str) -> None:
    t0 = datetime(2024, 5, 1)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        stats = _id(conn, "SELECT id FROM permission_groups WHERE name = 'stats' AND valid_id = 1")
        h_new = _id(conn, "SELECT id FROM ticket_history_type WHERE name = 'NewTicket'")
        s_customer = _id(conn, "SELECT id FROM article_sender_type WHERE name = 'customer'")
        chan = _id(conn, "SELECT id FROM communication_channel WHERE name = 'Email'")
        st_new = _id(conn, "SELECT id FROM ticket_state WHERE name = 'new'")
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
        for gid in (G30, G31):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"rep48-group-{gid}", "t": t0},
            )
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :id, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"rep48-queue-{gid}", "t": t0},
            )
        for uid, gid, key in (
            (OWNER, stats, "rw"),
            (OWNER, G30, "ro"),
            (VIEWER, G31, "ro"),
            (PEER, G30, "ro"),
        ):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:u, :g, :k, :t, 1, :t, 1)"
                ),
                {"u": uid, "g": gid, "k": key, "t": t0},
            )
        for n, (tid, queue, customer, at) in enumerate(
            (
                (T_A, G30, "rep48-cust-a", datetime(2024, 6, 3, 8)),
                (T_B, G31, "rep48-cust-a", datetime(2024, 6, 4, 9)),
                (T_C, G30, "rep48-cust-b", datetime(2024, 6, 5, 10)),
            )
        ):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, :title, :q, 1, 1, :u, 1, 3, :s, :c, :cu,"
                    " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"20240600{tid}", "title": f"rep48 ticket {tid}", "q": queue,
                 "u": OWNER, "s": st_new, "c": customer, "cu": f"{customer}@example.org",
                 "t": at},
            )  # fmt: skip
            conn.execute(
                text(
                    "INSERT INTO ticket_history (id, name, history_type_id, ticket_id,"
                    " article_id, type_id, queue_id, owner_id, priority_id, state_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :ht, :tid, NULL, 1, :q, :o, 3, :s, :t, :o, :t, :o)"
                ),
                {"id": LO + 30 + n, "n": f"%%20240600{tid}%%rep48-queue-{queue}%%3 normal%%new",
                 "ht": h_new, "tid": tid, "q": queue, "o": OWNER, "s": st_new, "t": at},
            )  # fmt: skip
            conn.execute(
                text(
                    "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                    " communication_channel_id, is_visible_for_customer,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tid, :s, :c, 1, :t, 1, :t, 1)"
                ),
                {"id": LO + 30 + n, "tid": tid, "s": s_customer, "c": chan, "t": at},
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
        async with factory() as s:
            await rebuild_ticket_facts(s, TICKETS, now=BUILT)
            await s.commit()
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


def _job_payload(env: Env, job_id: int) -> dict[str, Any]:
    """The stored payload JSON of a job row."""
    engine = create_engine(env.sync_url)
    try:
        with engine.connect() as conn:
            raw = conn.execute(
                text("SELECT payload FROM tiqora_job WHERE id = :id"), {"id": job_id}
            ).scalar_one()
    finally:
        engine.dispose()
    return json.loads(raw)  # type: ignore[no-any-return]


DEF_BODY: dict[str, Any] = {
    "source": "tickets",
    "dimensions": [{"key": "queue"}],
    "measures": [{"key": "tickets"}],
    "time_range": JUNE,
}


async def _saved(env: Env, owner: int, **extra: Any) -> str:
    env.as_user(owner)
    body = {"name": "rep48 run", "definition": DEF_BODY, **extra}
    resp = await env.client.post("/api/v1/reports", json=body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["key"])


async def _sources(env: Env) -> list[dict[str, Any]]:
    resp = await env.client.get("/api/v1/reporting/sources")
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# sources carry ops / enum values / common fields
# ---------------------------------------------------------------------------


async def test_sources_out_carry_ops_enum_values_common_fields(env: Env) -> None:
    env.as_user(OWNER)
    tickets = next(s for s in await _sources(env) if s["key"] == "tickets")
    channel = next(d for d in tickets["dimensions"] if d["key"] == "channel")
    assert channel["ops"] == ["eq", "in", "neq", "not_in"] and "email" in channel["enum_values"]
    created_hour = next(d for d in tickets["dimensions"] if d["key"] == "created_hour")
    assert created_hour["ops"] == []
    assert tickets["common_fields"]["queue"] == "queue"
    events = next(s for s in await _sources(env) if s["key"] == "ticket_events")
    assert events["common_fields"]["queue"] == "ticket_queue"
    assert (
        "channel"
        not in next(s for s in await _sources(env) if s["key"] == "open_tickets")["common_fields"]
    )


# ---------------------------------------------------------------------------
# run by saved key (HTTP)
# ---------------------------------------------------------------------------


async def test_run_saved_report_by_key(env: Env) -> None:
    key = await _saved(env, OWNER)
    resp = await env.client.post("/api/v1/reports/run", json={"report_key": key})
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["report_key"] == key
    assert sorted(r[0] for r in result["rows"]) == [G30]  # the owner's scope: queue 48130 only
    assert sum(r[1] for r in result["rows"]) == 2


async def test_shared_report_runs_in_viewer_scope(env: Env) -> None:
    await env.facts_ready()  # only ready facts are cached: without this nothing pins the key
    key = await _saved(env, OWNER, visibility="all")
    body = {"report_key": key}
    first = await env.client.post("/api/v1/reports/run", json=body)  # owner fills the cache
    assert first.status_code == 200 and first.json()["result"]["cached"] is False
    again = await env.client.post("/api/v1/reports/run", json=body)
    assert again.json()["result"]["cached"] is True  # the cache is filled and hit by the owner
    assert env.cache.store
    env.as_user(VIEWER)
    resp = await env.client.post("/api/v1/reports/run", json=body)
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    # the viewer's own queue, never the owner's numbers, and not the owner's cache entry
    assert [r[0] for r in result["rows"]] == [G31] and result["rows"][0][1] == 1
    assert result["cached"] is False
    peer = await _run_as(env, PEER, body)  # same scope as the owner -> hits the owner's entry
    assert peer["cached"] is True and [r[0] for r in peer["rows"]] == [G30]


async def _run_as(env: Env, uid: int, body: dict[str, Any]) -> dict[str, Any]:
    env.as_user(uid)
    resp = await env.client.post("/api/v1/reports/run", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]  # type: ignore[no-any-return]


async def test_job_fails_after_report_unshared(env: Env) -> None:
    await env.facts_ready()
    key = await _saved(env, OWNER, visibility="all")
    await _run_as(env, OWNER, {"report_key": key})  # the owner's run fills the cache
    cached_before = set(env.cache.store)
    assert cached_before
    env.as_user(VIEWER)
    resp = await env.client.post("/api/v1/reports/run", json={"report_key": key, "force_job": True})
    assert resp.status_code == 202
    job_id = resp.json()["job"]["job_id"]
    env.as_user(OWNER)
    upd = await env.client.put(
        f"/api/v1/reports/{key}",
        json={
            "name": "rep48 run",
            "version": 1,
            "definition": {k: v for k, v in DEF_BODY.items() if k != "time_range"},
        },
    )
    assert upd.status_code == 200, upd.text  # visibility is back to private
    await env.run_jobs()
    env.as_user(VIEWER)
    job = await env.job(job_id)
    assert job["status"] == "failed" and "nicht mehr gültig" in job["error"]
    assert "result" not in job or not job["result"]
    assert set(env.cache.store) == cached_before  # the failed job cached nothing for the viewer


# ---------------------------------------------------------------------------
# R-T3T4: run/export by saved key and the errors of an unusable key
# ---------------------------------------------------------------------------


async def test_invisible_report_is_404_on_run_and_export_by_key(env: Env) -> None:
    key = await _saved(env, OWNER)  # private
    env.as_user(VIEWER)
    for path in ("/api/v1/reports/run", "/api/v1/reports/export"):
        resp = await env.client.post(path, json={"report_key": key})
        assert resp.status_code == 404, (path, resp.text)
        assert resp.json()["detail"]["message"] == "Bericht nicht gefunden."
        assert resp.json()["detail"]["path"] == "report_key"
    missing = await env.client.post("/api/v1/reports/run", json={"report_key": "99999999999"})
    assert missing.status_code == 404  # unknown is indistinguishable from invisible
    assert missing.json()["detail"]["message"] == "Bericht nicht gefunden."
    env.as_user(OWNER)  # the owner still can, and the export by saved key is a job
    exp = await env.client.post("/api/v1/reports/export", json={"report_key": key})
    assert exp.status_code == 202, exp.text
    await env.run_jobs()
    assert (await env.job(exp.json()["job_id"]))["status"] == "succeeded"


async def test_unknown_builtin_keeps_the_wave1_text_on_run_and_export(env: Env) -> None:
    env.as_user(OWNER)
    for path in ("/api/v1/reports/run", "/api/v1/reports/export"):
        resp = await env.client.post(path, json={"report_key": "builtin.nope"})
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"]["message"] == "Unbekannter Bericht „builtin.nope“."
        assert resp.json()["detail"]["path"] == "report_key"


async def test_run_after_group_share_removed_and_delete_is_410(env: Env) -> None:
    key = await _saved(env, OWNER, visibility="groups", group_ids=[G30])
    body = {"report_key": key}
    assert (await _run_as(env, PEER, body))["report_key"] == key  # member of the shared group
    env.as_user(VIEWER)  # not a member of the group
    assert (await env.client.post("/api/v1/reports/run", json=body)).status_code == 404
    env.as_user(OWNER)
    upd = await env.client.put(
        f"/api/v1/reports/{key}",
        json={"name": "rep48 run", "version": 1, "definition": DEF_BODY, "visibility": "private"},
    )
    assert upd.status_code == 200, upd.text
    env.as_user(PEER)  # the share is gone
    assert (await env.client.post("/api/v1/reports/run", json=body)).status_code == 404
    env.as_user(OWNER)
    assert (await env.client.delete(f"/api/v1/reports/{key}")).status_code == 204
    gone = await env.client.post("/api/v1/reports/run", json=body)
    assert gone.status_code == 410 and "rep48 run" in gone.json()["detail"]["message"]
    export = await env.client.post("/api/v1/reports/export", json=body)
    assert export.status_code == 410


async def test_broken_definition_runs_422_by_key(env: Env) -> None:
    key = await _saved(env, OWNER)
    _run_sql(
        env.sync_url,
        (f"UPDATE tiqora_report SET definition = '{{\"schema_version\": 2}}' WHERE id = {key}",),
    )
    for path in ("/api/v1/reports/run", "/api/v1/reports/export"):
        resp = await env.client.post(path, json={"report_key": key})
        assert resp.status_code == 422, (path, resp.text)
        assert resp.json()["detail"]["path"] == "definition"
        assert "nicht mehr gelesen" in resp.json()["detail"]["message"]


# ---------------------------------------------------------------------------
# the service seam
# ---------------------------------------------------------------------------


async def test_run_report_ref_common_filters_and_extra_filter(env: Env) -> None:
    key = await _saved(env, OWNER)
    async with env.factory() as s:
        reporting = await load_reporting_settings(s, Settings(environment="test"))
        out = await run_report_ref(
            s,
            user_id=OWNER,
            report_key=key,
            definition=None,
            overrides=RunOverrides(common={"customer": ["rep48-cust-b"], "sla": []}),
            reporting=reporting,
            cache=None,
            budget=QueryBudget.inline(reporting),
        )
    assert out.deferred is None and out.ignored_common == ()
    assert out.result is not None and [r[1] for r in out.result.rows] == [1]  # only ticket 48142
    assert out.extra_filter is not None


async def test_ignored_common_keys_are_reported(env: Env) -> None:
    async with env.factory() as s:
        reporting = await load_reporting_settings(s, Settings(environment="test"))
        out = await run_report_ref(
            s,
            user_id=OWNER,
            report_key="builtin.backlog_trend",
            definition=None,
            overrides=RunOverrides(common={"customer": ["x"], "queue": [G30]}),
            reporting=reporting,
            cache=None,
            budget=QueryBudget.inline(reporting),
        )
    assert out.ignored_common == ("customer",)


async def test_extra_filter_never_widens_scope(env: Env) -> None:
    # an "any" extra filter naming the foreign queue still yields only the viewer's queue
    widen = Filter.model_validate(
        {
            "where": {
                "any": [
                    {"field": "queue", "op": "in", "value": [G30]},
                    {"field": "queue", "op": "in", "value": [G31]},
                ]
            }
        }
    )
    async with env.factory() as s:
        reporting = await load_reporting_settings(s, Settings(environment="test"))
        out = await run_report_ref(
            s,
            user_id=VIEWER,
            report_key=None,
            definition=ReportDefinition.model_validate(DEF_BODY),
            overrides=RunOverrides(extra_filter=widen),
            reporting=reporting,
            cache=None,
            budget=QueryBudget.inline(reporting),
        )
    assert out.result is not None and [r[0] for r in out.result.rows] == [G31]


async def test_deferred_and_enqueue_carry_extra_filter(env: Env) -> None:
    key = await _saved(env, OWNER)
    async with env.factory() as s:
        reporting = await load_reporting_settings(s, Settings(environment="test"))
        out = await run_report_ref(
            s,
            user_id=OWNER,
            report_key=key,
            definition=None,
            overrides=RunOverrides(common={"queue": [G30]}),
            reporting=reporting,
            cache=None,
            budget=QueryBudget.inline(reporting),
            force_job=True,
        )
        assert out.result is None and out.deferred == "forced"
        accepted = await enqueue_report_ref(
            s, user_id=OWNER, run=ReportRunIn(report_key=key), extra_filter=out.extra_filter
        )
    await env.run_jobs()
    env.as_user(OWNER)
    job = await env.job(accepted.job_id)
    assert job["status"] == "succeeded"
    payload = _job_payload(env, accepted.job_id)
    assert payload["extra_filter"]["where"]["field"] == "queue"
    assert payload["extra_filter"]["where"]["value"] == [G30]
    assert [r[0] for r in job["result"]["rows"]] == [G30]
    # R-PRIVATE: the scoped result stays with its creator
    env.as_user(VIEWER)
    other = await env.client.get(f"/api/v1/jobs/{accepted.job_id}")
    assert other.status_code == 404, other.text


async def test_job_extra_filter_narrows_the_job_result(env: Env) -> None:
    # the job applies the carried filter: customer b only -> one ticket instead of two
    narrow = Filter.model_validate(
        {"where": {"field": "customer", "op": "in", "value": ["rep48-cust-b"]}}
    )
    key = await _saved(env, OWNER)
    async with env.factory() as s:
        accepted = await enqueue_report_ref(
            s, user_id=OWNER, run=ReportRunIn(report_key=key), extra_filter=narrow
        )
    await env.run_jobs()
    env.as_user(OWNER)
    job = await env.job(accepted.job_id)
    assert job["status"] == "succeeded", job
    assert [r[1] for r in job["result"]["rows"]] == [1]


async def test_enqueue_cap_raises(env: Env) -> None:
    run = ReportRunIn(report_key="builtin.ticket_kpis")
    async with env.factory() as s:
        for _ in range(MAX_ACTIVE_REPORT_JOBS_PER_USER):
            await enqueue_report_ref(s, user_id=OWNER, run=run, extra_filter=None)
        with pytest.raises(TooManyReportJobs):
            await enqueue_report_ref(s, user_id=OWNER, run=run, extra_filter=None)


async def test_old_payload_without_extra_filter_still_runs(env: Env) -> None:
    # a job row written by wave 1 (no extra_filter key) is accepted by the new payload model
    old = {"user_id": OWNER, "run": {"report_key": "builtin.ticket_kpis"}}
    assert ReportRunPayload.model_validate(old).extra_filter is None
    engine = create_engine(env.sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tiqora_job (kind, status, payload, progress_done,"
                    " cancel_requested, attempts, created_by, created_at)"
                    " VALUES ('core.report_run', 'queued', :p, 0, :f, 0, :u, :t)"
                ),
                {"p": json.dumps(old), "f": False, "u": OWNER, "t": datetime(2026, 10, 1)},
            )
            job_id = int(
                conn.execute(
                    text(
                        "SELECT MAX(id) FROM tiqora_job"
                        " WHERE kind = 'core.report_run' AND created_by = :u"
                    ),
                    {"u": OWNER},
                ).scalar_one()
            )
    finally:
        engine.dispose()
    assert "extra_filter" not in _job_payload(env, job_id)  # the row really is a wave-1 row
    await env.run_jobs()
    env.as_user(OWNER)
    assert (await env.job(job_id))["status"] == "succeeded"
