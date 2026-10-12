"""Job handlers ``core.gi_invoke`` / ``core.gi_invoke_sync`` (G11) through the real
:class:`~tiqora.jobs.runner.JobRunner` claim / execute / finalize path (both dialects); the
remote is a ``httpx.MockTransport`` and the resolver returns documentation addresses.

Seed band 51270-51309: webservice 51270, ticket 51271. Jobs and debugger rows the module
creates are removed with ``snapshot_marks`` / ``delete_above_marks``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests._gi_fixtures import (
    EGRESS_TEST_POLICY,
    GI_MARK_TABLES,
    cleanup_webservices,
    insert_webservice,
    requester_config,
)
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.extensions import get_registry
from tiqora.gi import jobs as gi_jobs
from tiqora.gi.error_handling import PastExecutionData, RetryDecision
from tiqora.gi.jobs import (
    GI_INVOKE_KIND,
    GI_INVOKE_SYNC_KIND,
    INTERRUPTED_AFTER_SEND_MESSAGE,
    WEBSERVICE_GONE_MESSAGE,
    GiInvokePayload,
    enqueue_invocation,
)
from tiqora.gi.requester import RequesterOutcome
from tiqora.jobs.registry import JOB_HANDLERS
from tiqora.jobs.runner import JobRunner

pytestmark = pytest.mark.db

WS = 51270
TICKET = 51271
HOSTS = {"crm.example.org": ["198.51.100.10"]}
PASSWORD = "example-password-three"
_SEEDED: dict[str, dict[str, int]] = {}
_JOB_KINDS = (GI_INVOKE_KIND, GI_INVOKE_SYNC_KIND)

RETRY = {
    "Retry": {
        "Type": "RequestRetry",
        "ScheduleRetry": "1",
        "RetryIntervalStart": "60",
        "RetryIntervalFactor": "1",
        "RetryIntervalMax": "",
        "RetryCountMax": "",
        "RetryPeriodMax": "",
    }
}


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _resolver(host: str, port: int) -> list[str]:
    return HOSTS[host]


def _cleanup(conn: Any) -> None:
    cleanup_webservices(conn, [WS])
    conn.execute(text("DELETE FROM tiqora_job WHERE kind LIKE 'core.gi_invoke%'"))
    conn.execute(text("DELETE FROM ticket WHERE id = :t"), {"t": TICKET})


def _seed(sync_url: str) -> None:
    if sync_url in _SEEDED:
        return
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    _SEEDED[sync_url] = snapshot_marks(sync_url, GI_MARK_TABLES)
    with engine.begin() as conn:
        _cleanup(conn)
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url, marks in _SEEDED.items():
        delete_above_marks(sync_url, marks)
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            _cleanup(conn)
        engine.dispose()
    _SEEDED.clear()


@dataclass
class Mock:
    seen: list[httpx.Request] = field(default_factory=list)
    handler: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(
        200, json={"result": "ok"}
    )

    def transport(self) -> httpx.MockTransport:
        def record(request: httpx.Request) -> httpx.Response:
            self.seen.append(request)
            return self.handler(request)

        return httpx.MockTransport(record)


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def url_fixture(request: pytest.FixtureRequest) -> str:
    url: str = request.getfixturevalue(request.param)
    _seed(url)
    engine = create_engine(url)
    with engine.begin() as conn:
        _cleanup(conn)
    engine.dispose()
    return url


@pytest.fixture
def mock(monkeypatch: pytest.MonkeyPatch) -> Mock:
    remote = Mock()
    monkeypatch.setattr(
        gi_jobs,
        "_requester_extras",
        lambda: {
            "http_transport": remote.transport(),
            "resolver": _resolver,
            "policy": EGRESS_TEST_POLICY,
        },
    )
    return remote


def _install(sync_url: str, **kwargs: Any) -> None:
    config = requester_config(invokers={"SendTicket": {"Type": "Ticket::Generic"}}, **kwargs)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        cleanup_webservices(conn, [WS])
        insert_webservice(conn, ws_id=WS, name="GI12", config=config)
    engine.dispose()


def _payload(**kwargs: Any) -> GiInvokePayload:
    values: dict[str, Any] = {
        "webservice_id": WS,
        "invoker": "SendTicket",
        "data": {"TicketID": TICKET, "Event": "TicketCreate"},
        "asynchronous": True,
        "origin": "event",
        "history_id": 1,
    }
    values.update(kwargs)
    return GiInvokePayload(**values)


class Env:
    """An async engine plus a runner; ``drain`` runs every due GI job through the runner."""

    def __init__(self, sync_url: str) -> None:
        self.sync_url = sync_url
        self.engine = create_async_engine(_async_url(sync_url))
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.runner = JobRunner(
            session_factory=self.factory, settings=get_settings(), registry=get_registry()
        )

    async def enqueue(self, payload: GiInvokePayload, due_at: datetime | None = None) -> int:
        async with self.factory() as session:
            job = await enqueue_invocation(session, payload=payload, created_by=7, due_at=due_at)
            await session.commit()
            return int(job.id)

    async def run(self, job_id: int) -> None:
        attempt = await self.runner._claim(job_id)
        assert attempt
        async with self.factory() as session:
            kind, payload = (
                await session.execute(
                    text("SELECT kind, payload FROM tiqora_job WHERE id = :i"), {"i": job_id}
                )
            ).one()
        await self.runner._execute(job_id, kind, payload, 7, attempt=attempt)

    def jobs(self) -> list[dict[str, Any]]:
        engine = create_engine(self.sync_url)
        try:
            with engine.begin() as conn:
                rows = conn.execute(
                    text(
                        "SELECT id, kind, status, payload, result, error, dedupe_key, due_at,"
                        " checkpoint, attempts FROM tiqora_job WHERE kind LIKE 'core.gi_invoke%'"
                        " ORDER BY id"
                    )
                ).all()
        finally:
            engine.dispose()
        return [
            {
                "id": int(r[0]),
                "kind": str(r[1]),
                "status": str(r[2]),
                "payload": json.loads(r[3]),
                "result": json.loads(r[4]) if r[4] else None,
                "error": r[5],
                "dedupe_key": r[6],
                "due_at": r[7],
                "checkpoint": r[8],
                "attempts": int(r[9]),
            }
            for r in rows
        ]

    def make_due(self, job_id: int) -> None:
        engine = create_engine(self.sync_url)
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE tiqora_job SET due_at = :t WHERE id = :i"),
                {"t": datetime(2026, 1, 1), "i": job_id},
            )
        engine.dispose()

    async def dispose(self) -> None:
        await self.engine.dispose()


@pytest.fixture
async def env(url_fixture: str) -> Any:
    environment = Env(url_fixture)
    try:
        yield environment
    finally:
        await environment.dispose()


def _naive_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def test_handlers_registered_with_lanes() -> None:
    registry = get_registry()
    async_handler = registry.get(JOB_HANDLERS, GI_INVOKE_KIND)
    sync_handler = registry.get(JOB_HANDLERS, GI_INVOKE_SYNC_KIND)
    assert async_handler is not None and sync_handler is not None
    assert (async_handler.lane, sync_handler.lane) == ("gi", "gi_sync")
    for handler in (async_handler, sync_handler):
        assert handler.requeue_if_interrupted and handler.result_private
        assert handler.max_attempts == 3
        assert handler.timeout_seconds == get_settings().gi_max_timeout_seconds + 120


async def test_success_result_and_lane(env: Env, mock: Mock) -> None:
    _install(env.sync_url)
    job_id = await env.enqueue(_payload(asynchronous=False))
    (job,) = env.jobs()
    assert job["kind"] == GI_INVOKE_SYNC_KIND and job["dedupe_key"] == f"gi:1:{WS}:SendTicket"
    await env.run(job_id)
    (job,) = env.jobs()
    assert job["status"] == "succeeded" and job["checkpoint"] == "sending"
    assert job["result"]["status"] == "ok" and len(job["result"]["communication_id"]) == 32
    assert len(mock.seen) == 1 and mock.seen[0].url.host == "198.51.100.10"

    async_id = await env.enqueue(_payload(history_id=2))
    assert next(j for j in env.jobs() if j["id"] == async_id)["kind"] == GI_INVOKE_KIND


async def test_dedupe_only_for_first_event_runs(env: Env, mock: Mock) -> None:
    first = await env.enqueue(_payload(history_id=5))
    assert await env.enqueue(_payload(history_id=5)) == first
    other = [
        _payload(history_id=5, origin="resend"),
        _payload(history_id=5, origin="response_action"),
        _payload(history_id=5, past_execution={"InitialRequestDateTime": "2026-01-01 10:00:00"}),
    ]
    ids = {await env.enqueue(p) for p in other}
    assert len(ids) == 3 and first not in ids
    assert all(j["dedupe_key"] is None for j in env.jobs() if j["id"] != first)


async def test_dry_run_returns_masked_request_and_sends_nothing(env: Env, mock: Mock) -> None:
    _install(
        env.sync_url,
        transport_extra={
            "Authentication": {
                "AuthType": "BasicAuth",
                "BasicAuthUser": "apiuser",
                "BasicAuthPassword": PASSWORD,
            }
        },
    )
    job_id = await env.enqueue(_payload(origin="dry_run", dry_run=True, history_id=None))
    await env.run(job_id)
    (job,) = env.jobs()
    assert job["status"] == "succeeded" and job["checkpoint"] is None
    assert job["result"]["request"]["method"] == "POST"
    assert PASSWORD not in json.dumps(job["result"]) and mock.seen == []


async def test_reschedule_enqueues_new_job_with_past_execution_data(env: Env, mock: Mock) -> None:
    mock.handler = lambda request: httpx.Response(503, json={"error": "down"})
    _install(env.sync_url, error_handling=RETRY)
    first_id = await env.enqueue(_payload())
    before = _naive_now()
    await env.run(first_id)
    jobs = env.jobs()
    assert len(jobs) == 2
    first, second = jobs
    assert first["status"] == "succeeded" and first["result"]["status"] == "rescheduled"
    assert first["dedupe_key"] is not None
    assert second["id"] != first_id and second["status"] == "queued"
    assert second["dedupe_key"] is None
    assert second["payload"]["past_execution"]["RetryCount"] == 1
    assert second["payload"]["history_id"] == 1 and second["payload"]["origin"] == "event"
    wait = (second["due_at"] - before).total_seconds()
    assert 55 <= wait <= 65

    # forced due, it runs again and schedules attempt 2
    env.make_due(second["id"])
    await env.run(second["id"])
    third = env.jobs()[-1]
    assert third["id"] > second["id"] and third["status"] == "queued"
    assert third["payload"]["past_execution"]["RetryCount"] == 2
    assert len(mock.seen) == 2


async def test_final_failure_fails_job_with_redacted_message(env: Env, mock: Mock) -> None:
    mock.handler = lambda request: httpx.Response(500, text=f"boom {PASSWORD}")
    _install(
        env.sync_url,
        transport_extra={
            "Authentication": {
                "AuthType": "BasicAuth",
                "BasicAuthUser": "apiuser",
                "BasicAuthPassword": PASSWORD,
            }
        },
    )
    job_id = await env.enqueue(_payload())
    await env.run(job_id)
    (job,) = env.jobs()
    assert job["status"] == "failed" and job["error"]
    assert PASSWORD not in job["error"] and len(job["error"]) <= 500
    assert len(mock.seen) == 1


async def test_interrupted_before_checkpoint_requeued(env: Env, mock: Mock) -> None:
    _install(env.sync_url)
    job_id = await env.enqueue(_payload())
    attempt = await env.runner._claim(job_id)
    assert attempt == 1
    row = (job_id, GI_INVOKE_KIND, "{}", 7, attempt, None)
    assert await env.runner._settle_interrupted(row)
    (job,) = env.jobs()
    assert job["status"] == "queued" and mock.seen == []
    await env.run(job_id)  # the requeued job is sent normally
    (job,) = env.jobs()
    assert job["status"] == "succeeded" and len(mock.seen) == 1


async def test_interrupted_after_checkpoint_not_resent(env: Env, mock: Mock) -> None:
    _install(env.sync_url)
    job_id = await env.enqueue(_payload())
    attempt = await env.runner._claim(job_id)
    async with env.factory() as session:
        await session.execute(
            text("UPDATE tiqora_job SET checkpoint = 'sending' WHERE id = :i"), {"i": job_id}
        )
        await session.commit()
        payload = (
            await session.execute(
                text("SELECT payload FROM tiqora_job WHERE id = :i"), {"i": job_id}
            )
        ).scalar_one()
    row = (job_id, GI_INVOKE_KIND, payload, 7, attempt, "sending")
    assert await env.runner._settle_interrupted(row)
    jobs = env.jobs()
    assert len(jobs) == 1  # no new job
    assert jobs[0]["status"] == "failed" and jobs[0]["error"] == INTERRUPTED_AFTER_SEND_MESSAGE
    assert len(mock.seen) <= 1 and await env.runner._claim(job_id) == 0


async def test_checkpoint_precedes_the_request(env: Env, mock: Mock) -> None:
    _install(env.sync_url)
    marks: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        engine = create_engine(env.sync_url)
        with engine.begin() as conn:
            marks.append(
                conn.execute(
                    text("SELECT checkpoint FROM tiqora_job WHERE kind LIKE 'core.gi_invoke%'")
                ).scalar_one()
            )
        engine.dispose()
        return httpx.Response(200, json={"result": "ok"})

    mock.handler = handler
    await env.run(await env.enqueue(_payload()))
    assert marks == ["sending"]


async def test_invalid_webservice_fails_cleanly(env: Env, mock: Mock) -> None:
    job_id = await env.enqueue(_payload(webservice_id=WS + 20))
    await env.run(job_id)
    (job,) = env.jobs()
    assert job["status"] == "failed" and job["error"] == WEBSERVICE_GONE_MESSAGE
    assert mock.seen == []


async def test_future_task_time_diff_fallback(
    env: Env, mock: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(env.sync_url)
    past = PastExecutionData(_naive_now(), 1, None, None)

    async def fake_requester(**kwargs: Any) -> RequesterOutcome:
        return RequesterOutcome(False, "down", None, RetryDecision(True, None, past, {}), "c" * 32)

    monkeypatch.setattr(gi_jobs, "run_requester", fake_requester)
    job_id = await env.enqueue(_payload())
    before = _naive_now()
    await env.run(job_id)
    first, second = env.jobs()
    assert first["result"]["status"] == "rescheduled"
    assert 295 <= (second["due_at"] - before).total_seconds() <= 305
    assert second["payload"]["past_execution"]["RetryCount"] == 1
    assert second["due_at"] - before < timedelta(seconds=310)


async def test_response_action_enqueue_creates_follow_up_job(env: Env, mock: Mock) -> None:
    _install(env.sync_url)
    mock.handler = lambda request: httpx.Response(
        200,
        json={
            "OTRS_AsynchronousInvokerExecution": [
                {"WebserviceID": WS, "Invoker": "SendTicket", "Data": {"TicketID": TICKET}}
            ]
        },
    )
    job_id = await env.enqueue(_payload())
    await env.run(job_id)
    first, second = env.jobs()
    assert first["status"] == "succeeded"
    assert second["status"] == "queued" and second["kind"] == GI_INVOKE_KIND
    assert second["payload"]["origin"] == "response_action" and second["dedupe_key"] is None
