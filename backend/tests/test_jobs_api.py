"""Jobs polling API: visibility, progress, cancel and file download.

Seed band: agent id 8601 (no users row needed: jobs.created_by is a soft reference),
job kind prefix ``test.api``; agent 8603 owns real-kind jobs (ticket export, report run)
for the API-key kind gate. Root (id 1) is the admin.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraJob, TiqoraJobFile
from tiqora.domain.auth import AuthenticatedUser
from tiqora.jobs.service import utc_now

pytestmark = pytest.mark.db

AGENT = 8601
OTHER_AGENT = 8602
KEY_AGENT = 8603  # owns core.* jobs: cleaned up by created_by
ROOT = 1

_URLS: list[str] = []


def _async_url(url: str) -> str:
    return (
        url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _clean(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        ids = [
            r[0]
            for r in conn.execute(
                text("SELECT id FROM tiqora_job WHERE kind LIKE 'test.api%' OR created_by = :k"),
                {"k": KEY_AGENT},
            ).fetchall()
        ]
        if ids:
            for table, col in (("tiqora_job_file", "job_id"), ("tiqora_job", "id")):
                conn.execute(
                    text(f"DELETE FROM {table} WHERE {col} IN :ids").bindparams(
                        bindparam("ids", expanding=True)
                    ),
                    {"ids": ids},
                )
    engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in set(_URLS):
        _clean(url)
    _URLS.clear()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest,
) -> AsyncIterator[tuple[str, async_sessionmaker[AsyncSession]]]:
    url: str = request.getfixturevalue(request.param)
    _URLS.append(url)
    _clean(url)
    engine = create_async_engine(_async_url(url))
    yield url, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def _client(
    factory: async_sessionmaker[AsyncSession],
    user_id: int,
    scopes: frozenset[str] | None = None,
) -> AsyncClient:
    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=user_id,
        login=f"jobs.api.{user_id}",
        first_name="J",
        last_name="A",
        auth_method="session" if scopes is None else "api_key",
        api_key_scopes=scopes,
    )
    app.dependency_overrides[get_db] = _db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_job(
    factory: async_sessionmaker[AsyncSession],
    *,
    created_by: int | None = AGENT,
    status: str = "queued",
    files: tuple[tuple[str, str, bytes], ...] = (),
    **extra: Any,
) -> tuple[int, list[int]]:
    async with factory() as s:
        job = TiqoraJob(
            kind=extra.pop("kind", "test.api"),
            status=status,
            payload="{}",
            progress_done=extra.pop("progress_done", 0),
            cancel_requested=False,
            created_by=created_by,
            created_at=utc_now(),
            **extra,
        )
        s.add(job)
        await s.flush()
        rows = [
            TiqoraJobFile(
                job_id=job.id,
                name=n,
                content_type=ct,
                size_bytes=len(d),
                data=d,
                created_at=utc_now(),
            )
            for n, ct, d in files
        ]
        s.add_all(rows)
        await s.flush()
        ids = [r.id for r in rows]
        await s.commit()
        return job.id, ids


async def test_owner_polls_job_and_sees_progress_and_files(
    env: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = env
    job_id, _ = await _make_job(
        factory,
        status="running",
        progress_done=3,
        progress_total=10,
        started_at=utc_now(),
        result='{"rows": 3}',
        files=(("a.csv", "text/csv", b"x,y\n"),),
    )
    async with _client(factory, AGENT) as c:
        resp = await c.get(f"/api/v1/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["progress_done"] == 3
    assert body["progress_total"] == 10
    assert body["result"] == {"rows": 3}
    assert body["title_key"] is None  # kind not registered
    assert body["files"][0]["name"] == "a.csv"
    assert body["files"][0]["size_bytes"] == 4
    assert body["created_at"].endswith("Z") or body["created_at"].endswith("+00:00")


async def test_invalid_result_json_is_none(
    env: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = env
    job_id, _ = await _make_job(factory, status="succeeded", result="{not json")
    async with _client(factory, AGENT) as c:
        resp = await c.get(f"/api/v1/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["result"] is None


async def test_other_agent_gets_404_admin_gets_200(
    env: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = env
    job_id, _ = await _make_job(factory)
    async with _client(factory, OTHER_AGENT) as c:
        foreign = await c.get(f"/api/v1/jobs/{job_id}")
    async with _client(factory, ROOT) as c:
        admin = await c.get(f"/api/v1/jobs/{job_id}")
        missing = await c.get("/api/v1/jobs/999999999")
    assert foreign.status_code == 404
    assert admin.status_code == 200
    assert missing.status_code == 404


async def test_cancel_queued_then_terminal_409(
    env: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = env
    job_id, _ = await _make_job(factory)
    async with _client(factory, AGENT) as c:
        first = await c.post(f"/api/v1/jobs/{job_id}/cancel")
        second = await c.post(f"/api/v1/jobs/{job_id}/cancel")
    assert first.status_code == 200
    assert first.json()["status"] == "cancelled"
    assert second.status_code == 409
    assert second.json()["detail"]["message"] == "Die Aufgabe ist bereits beendet."
    async with factory() as s:
        row = await s.get(TiqoraJob, job_id)
        assert row is not None and row.status == "cancelled"  # committed


async def test_foreign_agent_cannot_cancel(
    env: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = env
    job_id, _ = await _make_job(factory)
    async with _client(factory, OTHER_AGENT) as c:
        resp = await c.post(f"/api/v1/jobs/{job_id}/cancel")
    assert resp.status_code == 404
    async with factory() as s:
        row = await s.get(TiqoraJob, job_id)
        assert row is not None and row.status == "queued"


async def test_download_file(env: tuple[str, async_sessionmaker[AsyncSession]]) -> None:
    _, factory = env
    job_id, fids = await _make_job(
        factory, files=(("Tickets übersicht.csv", "text/csv", b"a;b\n1;2\n"),)
    )
    async with _client(factory, AGENT) as c:
        resp = await c.get(f"/api/v1/jobs/{job_id}/files/{fids[0]}")
    assert resp.status_code == 200
    assert resp.content == b"a;b\n1;2\n"
    assert resp.headers["content-type"] == "text/csv; charset=utf-8"
    assert "filename*=UTF-8''Tickets%20%C3%BCbersicht.csv" in resp.headers["content-disposition"]


async def test_foreign_agent_cannot_download_file(
    env: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = env
    job_id, fids = await _make_job(factory, files=(("a.csv", "text/csv", b"secret"),))
    async with _client(factory, OTHER_AGENT) as c:
        resp = await c.get(f"/api/v1/jobs/{job_id}/files/{fids[0]}")
    assert resp.status_code == 404
    assert b"secret" not in resp.content


async def test_admin_sees_job_but_cannot_download_foreign_file(
    env: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    """Files are built from the creator's queue scope: creator-only, even for admins."""
    _, factory = env
    job_id, fids = await _make_job(factory, files=(("a.csv", "text/csv", b"secret"),))
    async with _client(factory, ROOT) as c:
        meta = await c.get(f"/api/v1/jobs/{job_id}")
        resp = await c.get(f"/api/v1/jobs/{job_id}/files/{fids[0]}")
    assert meta.status_code == 200
    assert [f["id"] for f in meta.json()["files"]] == fids
    assert resp.status_code == 404
    assert b"secret" not in resp.content


async def test_file_of_foreign_job_is_404(
    env: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = env
    job_a, files_a = await _make_job(factory, files=(("a.csv", "text/csv", b"A"),))
    job_b, _ = await _make_job(factory)
    async with _client(factory, AGENT) as c:
        wrong = await c.get(f"/api/v1/jobs/{job_b}/files/{files_a[0]}")
        right = await c.get(f"/api/v1/jobs/{job_a}/files/{files_a[0]}")
    assert wrong.status_code == 404
    assert right.status_code == 200


async def test_list_all_requires_admin(env: tuple[str, async_sessionmaker[AsyncSession]]) -> None:
    _, factory = env
    mine, _ = await _make_job(factory)
    theirs, _ = await _make_job(factory, created_by=OTHER_AGENT)
    async with _client(factory, AGENT) as c:
        own = await c.get("/api/v1/jobs")
        denied = await c.get("/api/v1/jobs", params={"all": "true"})
    async with _client(factory, ROOT) as c:
        everything = await c.get("/api/v1/jobs", params={"all": "true"})
    assert denied.status_code == 403
    own_ids = {j["id"] for j in own.json()}
    assert mine in own_ids and theirs not in own_ids
    assert everything.status_code == 200
    all_ids = {j["id"] for j in everything.json()}
    assert {mine, theirs} <= all_ids


def test_stats_job_kinds_are_the_report_handlers() -> None:
    from tiqora.api.v1.jobs import STATS_JOB_KINDS
    from tiqora.reporting.backfill import REPORT_BACKFILL_JOB
    from tiqora.reporting.jobs import REPORT_EXPORT_JOB, REPORT_RUN_JOB

    assert {REPORT_RUN_JOB.kind, REPORT_EXPORT_JOB.kind, REPORT_BACKFILL_JOB.kind} == (
        STATS_JOB_KINDS
    )


async def test_stats_only_key_reaches_report_jobs_only(
    env: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    """F1: a stats key polls/downloads its report jobs, never its ticket exports."""
    _, factory = env
    csv = (("tickets.csv", "text/csv", b"tn;title"),)
    export, (export_file,) = await _make_job(
        factory, created_by=KEY_AGENT, kind="core.ticket_export", status="running", files=csv
    )
    xlsx = (("bericht.csv", "text/csv", b"a;b"),)
    report, (report_file,) = await _make_job(
        factory, created_by=KEY_AGENT, kind="core.report_export", status="running", files=xlsx
    )
    stats_rw = frozenset({"stats:rw"})
    async with _client(factory, KEY_AGENT, stats_rw) as c:
        denied = [
            await c.get(f"/api/v1/jobs/{export}"),
            await c.get(f"/api/v1/jobs/{export}/files/{export_file}"),
            await c.post(f"/api/v1/jobs/{export}/cancel"),
        ]
        listed = await c.get("/api/v1/jobs")
        allowed = [
            await c.get(f"/api/v1/jobs/{report}"),
            await c.get(f"/api/v1/jobs/{report}/files/{report_file}"),
        ]
        cancel = await c.post(f"/api/v1/jobs/{report}/cancel")
    assert [r.status_code for r in denied] == [403, 403, 403]
    assert "stats" in denied[0].json()["detail"]
    assert [r.status_code for r in allowed] == [200, 200] and allowed[1].content == b"a;b"
    assert cancel.status_code == 200
    assert {j["id"] for j in listed.json()} == {report}  # the ticket export is not listed
    # a tickets key (alone or next to stats) keeps reaching the ticket export
    for scopes in ({"tickets:ro"}, {"tickets:ro", "stats:ro"}):
        async with _client(factory, KEY_AGENT, frozenset(scopes)) as c:
            assert (await c.get(f"/api/v1/jobs/{export}")).status_code == 200
            got = await c.get(f"/api/v1/jobs/{export}/files/{export_file}")
            assert got.status_code == 200 and got.content == b"tn;title"
            ids = {j["id"] for j in (await c.get("/api/v1/jobs")).json()}
            assert {export, report} <= ids
    # stats:ro + tickets:ro may read the ticket export but not cancel it (POST needs rw)
    async with _client(factory, KEY_AGENT, frozenset({"tickets:ro", "stats:rw"})) as c:
        assert (await c.post(f"/api/v1/jobs/{export}/cancel")).status_code == 403


async def test_stats_only_key_of_an_admin_cannot_list_all_jobs(
    env: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = env
    async with _client(factory, ROOT, frozenset({"stats:rw"})) as c:
        refused = await c.get("/api/v1/jobs", params={"all": "true"})
    async with _client(factory, ROOT, frozenset({"tickets:ro"})) as c:
        tickets_key = await c.get("/api/v1/jobs", params={"all": "true"})
    assert refused.status_code == 403
    assert tickets_key.status_code == 200
