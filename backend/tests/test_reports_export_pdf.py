"""PDF format of the interactive report export job (``core.report_export``).

Both dialects. Seed band 52100-52119 (names contain ``rep52``): agent 52100 (``ro`` on group
52110), queue 52110, open tickets 52111/52112 in that queue. Everything the module writes is
deleted again (incl. its jobs, the report state row and ``reporting.*`` settings).
"""

from __future__ import annotations

import builtins
import io
from collections.abc import AsyncIterator, Generator
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pypdf import PdfReader
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import tiqora.reporting.jobs as report_jobs
from tests.test_report_pdf import needs_pdf
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.reports import get_report_cache
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions import get_registry
from tiqora.jobs.runner import JobRunner
from tiqora.reporting.facts_store import rebuild_ticket_facts

pytestmark = pytest.mark.db

LO, HI = 52100, 52119
AGENT, QUEUE = 52100, 52110
TICKETS = (52111, 52112)
BUILT = datetime(2026, 9, 20, 11, 0)
USER = AuthenticatedUser(
    id=AGENT, login="rep52.pdf", first_name="Pia", last_name="Pdf", auth_method="session"
)
_BAND = {"lo": LO, "hi": HI}
_JOBS = "kind IN ('core.report_run', 'core.report_export')"
_RESET = (
    f"DELETE FROM tiqora_job_file WHERE job_id IN (SELECT id FROM tiqora_job WHERE {_JOBS})",
    f"DELETE FROM tiqora_job WHERE {_JOBS}",
    "DELETE FROM tiqora_report_state",
    "DELETE FROM tiqora_settings WHERE {key} LIKE 'reporting.%'",
)
_CLEANUP = (
    *_RESET,
    "DELETE FROM tiqora_report_ticket_event WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_fact WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_dirty_ticket WHERE ticket_id BETWEEN :lo AND :hi",
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


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        st_new = conn.execute(text("SELECT id FROM ticket_state WHERE name = 'new'")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep52.pdf', 'x', 'Pia', 'Pdf', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT, "t": BUILT},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep52-group', 1, :t, 1, :t, 1)"
            ),
            {"id": QUEUE, "t": BUILT},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep52-queue', :id, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": QUEUE, "t": BUILT},
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:u, :g, 'ro', :t, 1, :t, 1)"
            ),
            {"u": AGENT, "g": QUEUE, "t": BUILT},
        )
        for tid in TICKETS:
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, :title, :q, 1, 1, :u, 1, 3, :s, 'rep52-cust',"
                    " 'rep52@example.org', 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"20260900{tid}", "title": f"rep52 ticket {tid}", "q": QUEUE,
                 "u": AGENT, "s": st_new, "t": BUILT},
            )  # fmt: skip
    engine.dispose()


class Env:
    def __init__(
        self, sync_url: str, factory: async_sessionmaker[AsyncSession], client: AsyncClient
    ) -> None:
        self.sync_url = sync_url
        self.factory = factory
        self.client = client

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


class _MemoryCache:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int) -> None:
        self.store[key] = value


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
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_report_cache] = lambda: cache
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield Env(sync_url, factory, c)
    finally:
        await engine.dispose()
        _run_sql(sync_url, _RESET)


BODY: dict[str, Any] = {
    "report_key": "builtin.open_now",
    "format": "pdf",
    "title": "rep52 Offen",
    "locale": "de",
    "column_labels": {"queue": "Queue", "tickets": "Tickets"},
    "info_lines": ["Stand: heute"],
}


async def _export(env: Env, body: dict[str, Any]) -> dict[str, Any]:
    resp = await env.client.post("/api/v1/reports/export", json=body)
    assert resp.status_code == 202, resp.text
    await env.run_jobs()
    return await env.job(resp.json()["job_id"])


@needs_pdf
async def test_export_pdf_job_file(env: Env) -> None:
    job = await _export(env, BODY)
    assert job["status"] == "succeeded", job
    assert job["result"] == {"rows": 1}
    (file,) = job["files"]
    assert file["name"] == "rep52-offen.pdf" and file["content_type"] == "application/pdf"
    dl = await env.client.get(f"/api/v1/jobs/{job['id']}/files/{file['id']}")
    assert dl.status_code == 200 and dl.content.startswith(b"%PDF")
    page_text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(dl.content)).pages)
    assert "rep52 Offen" in page_text and "rep52-queue" in page_text


async def test_export_pdf_unavailable_fails_with_german_message(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_import = builtins.__import__

    def fail_weasyprint(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "weasyprint" or name.startswith("weasyprint."):
            raise OSError("cannot load library 'libgobject-2.0-0'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_weasyprint)
    job = await _export(env, BODY)
    assert job["status"] == "failed"
    assert "PDF-Erstellung ist auf diesem Server nicht verfügbar" in job["error"]


async def test_export_unknown_format_is_422(env: Env) -> None:
    resp = await env.client.post("/api/v1/reports/export", json={**BODY, "format": "docx"})
    assert resp.status_code == 422
