"""Jobs polling API: visibility, progress, cancel and file download.

Seed band: agent id 8601 (no users row needed: jobs.created_by is a soft reference),
job kind prefix ``test.api``. Root (id 1) is the admin.
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
                text("SELECT id FROM tiqora_job WHERE kind LIKE 'test.api%'")
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


def _client(factory: async_sessionmaker[AsyncSession], user_id: int) -> AsyncClient:
    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=user_id,
        login=f"jobs.api.{user_id}",
        first_name="J",
        last_name="A",
        auth_method="session",
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
            kind="test.api",
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
