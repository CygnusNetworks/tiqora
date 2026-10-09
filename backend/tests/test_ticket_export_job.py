"""Ticket CSV export as a background job (first job consumer).

Seed band 918xx (agent 91800, groups 91810/91811, queues 91820/91821/91822,
tickets 91830-91832). The module deletes every row it created.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import tiqora.api.v1.tickets as tickets_api
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.tickets import MAX_ACTIVE_EXPORTS_PER_USER
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions import get_registry
from tiqora.jobs.handlers.ticket_export import TICKET_EXPORT_JOB
from tiqora.jobs.registry import JOB_HANDLERS
from tiqora.jobs.runner import JobRunner

pytestmark = pytest.mark.db

NOW = datetime(2026, 3, 1, 12, 0, 0)
AGENT = 91800
G1, G2 = 91810, 91811
Q1, Q2, Q3 = 91820, 91821, 91822  # Q1+Q2 visible (G1), Q3 foreign (G2)
T1, T2, T3 = 91830, 91831, 91832  # T1 in Q1, T2 in Q2, T3 in Q3
TICKETS = (T1, T2, T3)
URLS: list[str] = []


def _async_url(url: str) -> str:
    return (
        url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _cleanup(conn: Any) -> None:
    job_ids = [
        r[0]
        for r in conn.execute(
            text("SELECT id FROM tiqora_job WHERE kind = 'core.ticket_export'")
        ).fetchall()
    ]
    if job_ids:
        for table, col in (("tiqora_job_file", "job_id"), ("tiqora_job", "id")):
            conn.execute(
                text(f"DELETE FROM {table} WHERE {col} IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": job_ids},
            )
    for sql, ids in (
        ("DELETE FROM ticket WHERE id IN :ids", TICKETS),
        ("DELETE FROM queue WHERE id IN :ids", (Q1, Q2, Q3)),
        ("DELETE FROM group_user WHERE group_id IN :ids", (G1, G2)),
        ("DELETE FROM permission_groups WHERE id IN :ids", (G1, G2)),
        ("DELETE FROM users WHERE id IN :ids", (AGENT,)),
    ):
        conn.execute(text(sql).bindparams(bindparam("ids", expanding=True)), {"ids": ids})


def _seed(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _cleanup(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'export.agent', 'x', 'Export', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT, "t": NOW},
        )
        for gid, name in ((G1, "export-g1"), (G2, "export-g2")):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id, create_time,"
                    " create_by, change_time, change_by)"
                    " VALUES (:id, :name, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "name": name, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by) VALUES (:u, :g, 'ro', :t, 1, :t, 1)"
            ),
            {"u": AGENT, "g": G1, "t": NOW},
        )
        for qid, gid in ((Q1, G1), (Q2, G1), (Q3, G2)):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id, create_time,"
                    " create_by, change_time, change_by)"
                    " VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": qid, "n": f"ExportQueue{qid}", "g": gid, "t": NOW},
            )
        for tid, qid in ((T1, Q1), (T2, Q2), (T3, Q3)):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag, create_time, create_by,"
                    " change_time, change_by)"
                    " VALUES (:id, :tn, :title, :q, 1, 1, :u, 1, 3, 4, 'EXPORT-CUST',"
                    " 'alice@example.org', 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"EXP{tid}", "title": f"Export; ticket {tid}",
                 "q": qid, "u": AGENT, "t": NOW},
            )  # fmt: skip
    engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in set(URLS):
        engine = create_engine(url)
        with engine.begin() as conn:
            _cleanup(conn)
        engine.dispose()
    URLS.clear()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def factory(
    request: pytest.FixtureRequest,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url: str = request.getfixturevalue(request.param)
    URLS.append(url)
    _seed(url)
    engine = create_async_engine(_async_url(url))
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


def _client(factory: async_sessionmaker[AsyncSession]) -> AsyncClient:
    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=AGENT, login="export.agent", first_name="Export", last_name="Agent",
        auth_method="session",
    )  # fmt: skip
    app.dependency_overrides[get_db] = _db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _run_export(
    factory: async_sessionmaker[AsyncSession], client: AsyncClient, params: dict[str, Any]
) -> dict[str, Any]:
    resp = await client.post("/api/v1/tickets/export-jobs", json={"params": params})
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "queued"
    job_id = resp.json()["job_id"]
    runner = JobRunner(
        session_factory=factory, settings=Settings(environment="test"), registry=get_registry()
    )
    await runner.run_once()
    await runner.wait_idle()
    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert job["status"] == "succeeded", job
    job["_job_id"] = job_id
    return job  # type: ignore[no-any-return]


async def test_export_job_end_to_end(factory: async_sessionmaker[AsyncSession]) -> None:
    async with _client(factory) as client:
        job = await _run_export(factory, client, {"queue_id": Q1})
        assert job["result"] == {"rows": 1}
        assert [f["name"] for f in job["files"]] == ["tickets.csv"]
        assert job["files"][0]["content_type"] == "text/csv; charset=utf-8"
        dl = await client.get(f"/api/v1/jobs/{job['_job_id']}/files/{job['files'][0]['id']}")
        assert dl.status_code == 200
        assert dl.content.startswith(b"\xef\xbb\xbf")
        direct = await client.get(f"/api/v1/tickets/export.csv?queue_id={Q1}")
        assert direct.status_code == 200
        assert dl.content == direct.content
        assert f"EXP{T1}".encode() in dl.content


async def test_export_job_never_contains_foreign_queue(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(factory) as client:
        job = await _run_export(factory, client, {})
        assert job["result"] == {"rows": 2}
        dl = await client.get(f"/api/v1/jobs/{job['_job_id']}/files/{job['files'][0]['id']}")
        assert f"EXP{T3}".encode() not in dl.content
        # Naming the foreign queue explicitly still yields header-only output.
        job = await _run_export(factory, client, {"queue_id": Q3})
        assert job["result"] == {"rows": 0}
        dl = await client.get(f"/api/v1/jobs/{job['_job_id']}/files/{job['files'][0]['id']}")
        assert dl.content.startswith(b"\xef\xbb\xbfNumber;Title;")
        assert dl.content.count(b"\n") == 1


async def test_export_job_reports_progress(factory: async_sessionmaker[AsyncSession]) -> None:
    async with _client(factory) as client:
        job = await _run_export(factory, client, {})
        assert job["progress_total"] == 2
        assert job["progress_done"] == 2


def test_worker_registry_has_ticket_export_handler() -> None:
    """The worker builds its JobRunner on a fresh ``get_registry()`` (see
    ``tiqora.worker.__main__``); core handlers must be registered there or
    jobs end with the unknown-kind message."""
    import inspect

    from tiqora.extensions import set_registry_for_tests
    from tiqora.worker import __main__ as worker_main

    src = inspect.getsource(worker_main)
    assert "registry=get_registry()" in src
    set_registry_for_tests(None)  # worker process starts with no cached registry
    try:
        handler = get_registry().get(JOB_HANDLERS, "core.ticket_export")
        assert handler is TICKET_EXPORT_JOB
    finally:
        set_registry_for_tests(None)


async def test_invalid_filter_is_rejected_at_enqueue_with_422(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.api.v1 import tickets as tickets_api
    from tiqora.filters import FilterError

    async def boom(*_a: Any, **_k: Any) -> None:
        raise FilterError("Unbekanntes Feld „x“.", path="where.field")

    monkeypatch.setattr(tickets_api, "compile_ticket_filter", boom)
    async with _client(factory) as client:
        resp = await client.post("/api/v1/tickets/export-jobs", json={"params": {}})
    assert resp.status_code == 422
    assert resp.json()["detail"] == {"message": "Unbekanntes Feld „x“.", "path": "where.field"}
    async with factory() as s:
        n = (
            await s.execute(text("SELECT COUNT(*) FROM tiqora_job WHERE kind='core.ticket_export'"))
        ).scalar_one()
    assert n == 0


async def test_same_export_twice_joins_the_pending_job(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(factory) as client:
        a = await client.post("/api/v1/tickets/export-jobs", json={"params": {"queue_id": Q1}})
        b = await client.post("/api/v1/tickets/export-jobs", json={"params": {"queue_id": Q1}})
    assert a.status_code == b.status_code == 202
    assert a.json()["job_id"] == b.json()["job_id"]


async def test_restart_after_cancel_request_does_not_join_the_dying_job(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(factory) as client:
        a = await client.post("/api/v1/tickets/export-jobs", json={"params": {"queue_id": Q1}})
        # A running job whose cancellation was requested is still "active" but dying.
        async with factory() as s:
            await s.execute(
                text(
                    "UPDATE tiqora_job SET status='running', cancel_requested=:c WHERE id=:i"
                ).bindparams(bindparam("c", True), bindparam("i", a.json()["job_id"]))
            )
            await s.commit()
        b = await client.post("/api/v1/tickets/export-jobs", json={"params": {"queue_id": Q1}})
    assert a.status_code == b.status_code == 202
    assert b.json()["job_id"] != a.json()["job_id"]


async def test_third_active_export_is_rejected_with_429(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(factory) as client:
        first = await client.post("/api/v1/tickets/export-jobs", json={"params": {"queue_id": Q1}})
        second = await client.post("/api/v1/tickets/export-jobs", json={"params": {"queue_id": Q2}})
        third = await client.post("/api/v1/tickets/export-jobs", json={"params": {}})
        again = await client.post("/api/v1/tickets/export-jobs", json={"params": {"queue_id": Q1}})
        # Cancelling one frees a slot.
        await client.post(f"/api/v1/jobs/{first.json()['job_id']}/cancel")
        after_cancel = await client.post("/api/v1/tickets/export-jobs", json={"params": {}})
    assert first.status_code == second.status_code == 202
    assert third.status_code == 429
    assert third.json()["detail"]["message"].startswith(
        f"Es laufen bereits {MAX_ACTIVE_EXPORTS_PER_USER} Exporte"
    )
    assert again.status_code == 202  # joins the pending identical export, not a new slot
    assert after_cancel.status_code == 202


async def test_concurrent_exports_cannot_overshoot_the_cap(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five different exports at once: the user-row lock lets exactly the cap through."""
    real_count = tickets_api.count_active_jobs

    async def slow_count(*args: Any, **kwargs: Any) -> int:
        n = await real_count(*args, **kwargs)
        await asyncio.sleep(0.2)  # widen the check-then-insert window
        return n

    monkeypatch.setattr(tickets_api, "count_active_jobs", slow_count)
    bodies = [
        {"params": {"queue_id": Q1}},
        {"params": {"queue_id": Q2}},
        {"params": {}},
        {"params": {"queue_id": Q1}, "order": "asc"},
        {"params": {"queue_id": Q2}, "order": "asc"},
    ]
    async with _client(factory) as client:
        responses = await asyncio.gather(
            *(client.post("/api/v1/tickets/export-jobs", json=b) for b in bodies)
        )
    codes = sorted(r.status_code for r in responses)
    assert codes == [202] * MAX_ACTIVE_EXPORTS_PER_USER + [429] * (5 - MAX_ACTIVE_EXPORTS_PER_USER)
    async with factory() as s:
        active = (
            await s.execute(
                text(
                    "SELECT COUNT(*) FROM tiqora_job WHERE kind = 'core.ticket_export'"
                    " AND created_by = :u AND status IN ('queued', 'running')"
                ),
                {"u": AGENT},
            )
        ).scalar_one()
    assert active == MAX_ACTIVE_EXPORTS_PER_USER


async def test_filter_error_at_run_time_fails_job_with_the_real_message(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    from tiqora.jobs.service import enqueue_job

    async with factory() as s:
        job = await enqueue_job(
            s,
            kind="core.ticket_export",
            payload={
                "user_id": AGENT,
                "filter": {"where": {"field": "no_such_field", "op": "eq", "value": 1}},
            },
            created_by=AGENT,
        )
        await s.commit()
        job_id = job.id
    runner = JobRunner(
        session_factory=factory, settings=Settings(environment="test"), registry=get_registry()
    )
    await runner.run_once()
    await runner.wait_idle()
    async with _client(factory) as client:
        body = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert body["status"] == "failed"
    assert body["error"].startswith("Der Filter ist nicht mehr gültig: ")
    assert "no_such_field" in body["error"]
