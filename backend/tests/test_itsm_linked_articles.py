"""Linked-articles job and ``POST /tickets/{id}/linked-articles`` (Task 18), both dialects.

One rolled-back session carries the world (the routes' ``commit`` becomes ``flush``, the job's
sessions are the same session). Seed band 941300-941399. ``FULL`` (rw on queue A) works on
``T_A`` (source); linked: ``T_C`` and ``T_D`` (queue A, ``T_D`` has a customer) and ``T_B``
(queue B, nobody here reads it). The outgoing e-mail path is wrapped by a spy; no SMTP relay is
configured, so the reply is stored, not sent.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tests._itsm_adapter_helpers import itsm_session, seed_customer
from tests._itsm_mode_helpers import itsm_url, profile_packages, seed_packages, store
from tests.test_itsm_ci_api import (
    FULL,
    NOW,
    QUEUE_A,
    T_A,
    T_B,
    _seed_access,
    _user,
)
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraJob, TiqoraSettings
from tiqora.domain.api_key_scopes import path_to_area
from tiqora.domain.ticket_write_service import link_tickets
from tiqora.itsm.linked_articles import (
    LINKED_ARTICLES_JOB,
    LinkedArticlesPayload,
    enqueue_linked_articles,
    result_from_table,
    run_linked_articles,
)
from tiqora.itsm.mode import SETTING_MODE
from tiqora.itsm.models import TiqoraItsmBulkTarget
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache
from tiqora.jobs.context import JobContext
from tiqora.jobs.runner import JobRunner

pytestmark = pytest.mark.db

T_C, T_D = 941301, 941302  # queue A; T_D has a customer
CUSTOMER = "linked941.customer@example.test"
DIALECTS = ["mysql", "postgresql"]
SOURCE = f"/api/v1/tickets/{T_A}/linked-articles"


class _Factory:
    """``session_factory`` that hands out the test's one session and never closes it."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def __call__(self) -> _Factory:
        return self

    async def __aenter__(self) -> AsyncSession:
        return self.session

    async def __aexit__(self, *exc: object) -> None:
        return None


@dataclass
class World:
    client: AsyncClient
    session: AsyncSession
    factory: _Factory
    sent: list[int] = field(default_factory=list)

    def ctx(self, job_id: int, *, attempt: int = 1) -> JobContext:
        return JobContext(
            job_id=job_id,
            kind=LINKED_ARTICLES_JOB.kind,
            created_by=FULL,
            settings=Settings(environment="test"),
            session_factory=self.factory,  # type: ignore[arg-type]
            attempt=attempt,
            max_attempts=2,
        )

    async def start_job(self, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        """Queue the job, then mark it running; returns (job id, validated payload).

        A note goes through the API. The API offers no reply mode (only the major-incident update
        sends customer e-mail), so a reply job is queued the way that producer queues it.
        """
        body = body or self.body()
        if body.get("mode") == "reply":
            job = await enqueue_linked_articles(
                self.session,
                LinkedArticlesPayload(
                    source_ticket_id=T_A,
                    origin="major_incident_update",
                    acting_user_id=FULL,
                    **body,
                ),
            )
            job_id = int(job.id)
        else:
            got = await self.client.post(SOURCE, json=body)
            assert got.status_code == 202, got.text
            job_id = int(got.json()["job_id"])
        await self.session.execute(
            update(TiqoraJob).where(TiqoraJob.id == job_id).values(status="running", attempts=1)
        )
        payload = LinkedArticlesPayload.model_validate_json(
            (
                await self.session.execute(select(TiqoraJob.payload).where(TiqoraJob.id == job_id))
            ).scalar_one()
        )
        return job_id, payload

    @staticmethod
    def body(**over: Any) -> dict[str, Any]:
        return {
            "target_ticket_ids": [T_C, T_D],
            "mode": "note",
            "subject": "Update zur Störung",
            "body_html": "<p>Wir arbeiten daran.</p><script>alert(1)</script>",
            **over,
        }

    async def job_count(self) -> int:
        return int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(TiqoraJob)
                    .where(TiqoraJob.kind == LINKED_ARTICLES_JOB.kind)
                )
            ).scalar_one()
        )

    async def articles(self, ticket_id: int) -> list[Any]:
        return list(
            (
                await self.session.execute(
                    text(
                        "SELECT a.id, m.a_subject, m.a_body, a.is_visible_for_customer"
                        " FROM article a JOIN article_data_mime m ON m.article_id = a.id"
                        " WHERE a.ticket_id = :t ORDER BY a.id"
                    ),
                    {"t": ticket_id},
                )
            ).all()
        )

    async def history(self, ticket_id: int) -> int:
        return int(
            (
                await self.session.execute(
                    text("SELECT count(*) FROM ticket_history WHERE ticket_id = :t"),
                    {"t": ticket_id},
                )
            ).scalar_one()
        )

    async def state_of(self, ticket_id: int) -> str:
        return str(
            (
                await self.session.execute(
                    text(
                        "SELECT s.name FROM ticket t JOIN ticket_state s"
                        " ON s.id = t.ticket_state_id WHERE t.id = :t"
                    ),
                    {"t": ticket_id},
                )
            ).scalar_one()
        )


async def _seed(session: AsyncSession) -> None:
    await _seed_access(session)
    await seed_customer(session, CUSTOMER, "Linked", "Customer", "linked941")
    for tid, customer in ((T_C, None), (T_D, CUSTOMER)):
        await session.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
                " responsible_user_id, ticket_priority_id, ticket_state_id, customer_id,"
                " customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :tn, 'linked941 ticket', :q, 1, 1, 1, 1, 3, 1, :cid, :cu, 0, 0,"
                " 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {
                "id": tid,
                "tn": f"20261013{tid}",
                "q": QUEUE_A,
                "cid": "linked941" if customer else None,
                "cu": customer,
                "t": NOW,
            },
        )
    for other in (T_C, T_D, T_B):
        await link_tickets(
            session, source_ticket_id=T_A, target_ticket_id=other, link_type="Normal", user_id=1
        )


@asynccontextmanager
async def world_for(request: pytest.FixtureRequest, dialect: str) -> AsyncIterator[World]:
    url = itsm_url(request, dialect, "itsm-6")
    seed_packages(url, profile_packages("itsm-6"))
    store(url, SETTING_MODE, "tiqora")
    try:
        async with itsm_session(request, dialect, "itsm-6") as (session, _reader, _engine):
            await _seed(session)

            async def _db() -> Any:
                yield session

            session.commit = session.flush  # type: ignore[method-assign]
            app = create_app(Settings(environment="test"))
            app.dependency_overrides[get_db] = _db
            app.dependency_overrides[get_current_user] = lambda: _user(FULL)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                yield World(c, session, _Factory(session))
    finally:
        clear_detect_cache()
        # the mail log is written best-effort on its own connection, outside the world
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM tiqora_mail_log WHERE ticket_id IN (:c, :d)"),
                    {"c": T_C, "d": T_D},
                )
        finally:
            engine.dispose()


@pytest.fixture(params=DIALECTS)
def dialect(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
async def w(request: pytest.FixtureRequest, dialect: str) -> AsyncIterator[World]:
    async with world_for(request, dialect) as world:
        yield world


@pytest.fixture
def mail_spy(w: World, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Wrap the outgoing agent e-mail path; records the ticket of each call."""
    from tiqora.channels.email import outbound_reply

    original: Callable[..., Awaitable[int]] = outbound_reply.deliver_agent_email_reply

    async def spy(*args: Any, **kwargs: Any) -> int:
        w.sent.append(int(kwargs["ticket_id"]))
        return await original(*args, **kwargs)

    monkeypatch.setattr(outbound_reply, "deliver_agent_email_reply", spy)
    return w.sent


async def test_api_enqueues_job_and_returns_202(w: World) -> None:
    got = await w.client.post(SOURCE, json=w.body())
    assert got.status_code == 202, got.text
    job = (
        await w.session.execute(select(TiqoraJob).where(TiqoraJob.id == got.json()["job_id"]))
    ).scalar_one()
    assert job.kind == "core.itsm_linked_articles" and job.created_by == FULL
    payload = json.loads(job.payload)
    assert payload["target_ticket_ids"] == [T_C, T_D] and payload["origin"] == "linked_note"
    assert "<script" not in payload["body_html"]  # sanitised on write
    assert path_to_area(SOURCE) == "tickets"


async def test_api_refuses_unlinked_and_unknown_targets_without_echo(w: World) -> None:
    got = await w.client.post(SOURCE, json=w.body(target_ticket_ids=[T_C, 941399]))
    assert got.status_code == 422 and got.json()["detail"] == {"code": "targets_not_linked"}
    assert "941399" not in got.text


async def test_linked_articles_404_without_ro(w: World) -> None:
    got = await w.client.post(f"/api/v1/tickets/{T_B}/linked-articles", json=w.body())
    assert got.status_code == 404


async def test_api_reply_mode_refused(w: World, mail_spy: list[int]) -> None:
    """No bulk customer e-mail through this route: the admin gate for that lives on the
    major-incident update only (I1 of the final review)."""
    jobs_before = await w.job_count()
    got = await w.client.post(SOURCE, json=w.body(mode="reply"))
    assert got.status_code == 422, got.text
    assert await w.job_count() == jobs_before and mail_spy == []


async def test_api_works_with_itsm_off(w: World) -> None:
    """A general ticket feature: it stays in mode ``off`` (plan open question 6)."""
    await w.session.execute(
        update(TiqoraSettings).where(TiqoraSettings.key == SETTING_MODE).values(value="off")
    )
    await w.session.flush()
    got = await w.client.post(SOURCE, json=w.body())
    assert got.status_code == 202, got.text


async def test_max_targets_enforced(w: World) -> None:
    w.session.add(TiqoraSettings(key="itsm.linked_articles_max_tickets", value="1"))
    await w.session.flush()
    got = await w.client.post(SOURCE, json=w.body())
    assert got.status_code == 422 and got.json()["detail"] == {"code": "too_many_targets"}


async def test_note_on_each_linked_ticket(w: World) -> None:
    job_id, payload = await w.start_job()
    before = {t: await w.history(t) for t in (T_C, T_D)}
    out = await run_linked_articles(w.ctx(job_id), payload)
    assert out["done"] == [T_C, T_D] and out["skipped"] == []
    for ticket_id in (T_C, T_D):
        (article,) = await w.articles(ticket_id)
        assert article[1] == "Update zur Störung" and "Wir arbeiten daran" in article[2]
        assert "<script" not in article[2] and not article[3]  # internal note
        assert await w.history(ticket_id) > before[ticket_id]  # from add_article
    rows = (
        await w.session.execute(
            select(TiqoraItsmBulkTarget).where(TiqoraItsmBulkTarget.job_id == job_id)
        )
    ).scalars()
    assert {r.ticket_id: r.status for r in rows} == {T_C: "done", T_D: "done"}


async def test_job_skips_ticket_without_rw(w: World) -> None:
    job_id, payload = await w.start_job()
    payload = payload.model_copy(update={"target_ticket_ids": [T_C, T_B]})
    out = await run_linked_articles(w.ctx(job_id), payload)
    assert out["done"] == [T_C]
    assert out["skipped"] == [{"ticket_id": T_B, "reason": "no_rw", "detail": None}]
    assert await w.articles(T_B) == []


async def test_job_skips_unlinked_target(w: World) -> None:
    job_id, payload = await w.start_job()
    await w.session.execute(
        text("DELETE FROM link_relation WHERE source_key = :s AND target_key = :t"),
        {"s": str(T_A), "t": str(T_C)},
    )
    out = await run_linked_articles(w.ctx(job_id), payload)
    assert out["done"] == [T_D]
    assert out["skipped"] == [{"ticket_id": T_C, "reason": "not_linked", "detail": None}]
    assert await w.articles(T_C) == []


async def test_set_state_after_note(w: World) -> None:
    job_id, payload = await w.start_job(w.body(target_ticket_ids=[T_C]))
    payload = payload.model_copy(update={"set_state_name": "closed successful"})
    out = await run_linked_articles(w.ctx(job_id), payload)
    assert out["done"] == [T_C]
    assert len(await w.articles(T_C)) == 1
    assert await w.state_of(T_C) == "closed successful"


async def test_unknown_state_is_reported_per_ticket(w: World) -> None:
    job_id, payload = await w.start_job(w.body(target_ticket_ids=[T_C]))
    payload = payload.model_copy(update={"set_state_name": "no such state"})
    out = await run_linked_articles(w.ctx(job_id), payload)
    assert out["done"] == []
    assert out["skipped"][0]["reason"] == "error" and await w.articles(T_C) == []


async def test_reply_mode_uses_deliver_agent_email_reply(w: World, mail_spy: list[int]) -> None:
    job_id, payload = await w.start_job(w.body(mode="reply"))
    out = await run_linked_articles(w.ctx(job_id), payload)
    assert mail_spy == [T_D]  # one e-mail, to the ticket that has a customer
    assert out["done"] == [T_D]
    assert out["skipped"] == [{"ticket_id": T_C, "reason": "no_customer_address", "detail": None}]
    (article,) = await w.articles(T_D)
    assert article[3]  # visible for the customer


async def test_interrupted_after_checkpoint_not_resent(w: World, mail_spy: list[int]) -> None:
    job_id, payload = await w.start_job(w.body(mode="reply", target_ticket_ids=[T_D, T_C]))
    # the job got as far as the first target (article + checkpoint + progress row), then died
    single = payload.model_copy(update={"target_ticket_ids": [T_D]})
    await run_linked_articles(w.ctx(job_id), single)
    assert mail_spy == [T_D]
    await w.session.execute(
        update(TiqoraJob).where(TiqoraJob.id == job_id).values(checkpoint="first_write")
    )

    runner = JobRunner(
        session_factory=w.factory,  # type: ignore[arg-type]
        settings=Settings(environment="test"),
        registry=__import__("tiqora.extensions", fromlist=["get_registry"]).get_registry(),
    )
    row = (job_id, LINKED_ARTICLES_JOB.kind, payload.model_dump_json(), FULL, 1, "first_write")
    assert await runner._settle_interrupted(row)  # fails the job, calls the hook
    job = (await w.session.execute(select(TiqoraJob).where(TiqoraJob.id == job_id))).scalar_one()
    assert job.status == "failed"
    assert json.loads(job.result or "{}")["done"] == [T_D]

    # a (manual) re-run with the full target list skips the target that was already done
    out = await run_linked_articles(w.ctx(job_id, attempt=2), payload)
    assert mail_spy == [T_D]  # still exactly one customer mail
    assert len(await w.articles(T_D)) == 1
    assert out["done"] == [T_D] and out["skipped"][0]["ticket_id"] == T_C


async def test_result_lists_done_and_skipped(w: World) -> None:
    job_id, payload = await w.start_job()
    payload = payload.model_copy(update={"target_ticket_ids": [T_C, T_B, T_D]})
    await run_linked_articles(w.ctx(job_id), payload)
    result = await result_from_table(w.session, job_id)
    assert sorted(result.done) == [T_C, T_D]
    assert [s["ticket_id"] for s in result.skipped] == [T_B]
