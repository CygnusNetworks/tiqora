"""Hook job, decision note, SSE ``approval_changed`` and the consumer kit (Task 9).

Seed band 971300-971399 is reserved; the tests only need the helper world (970100-970599) and
autoincrement rows (approvals, articles, jobs) that live in the rolled-back ``approvals_session``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests._approvals_helpers import (
    AG1,
    AG3,
    T1,
    approvals_session,
    installed_registry,
    make_fake_subject_type,
)
from tests._fake_redis import FakeRedis
from tests.approvals_consumer_kit import (
    approve_all,
    assert_subject_registered,
    reject_first,
    run_hook_now,
    shared_session_factory,
)
from tiqora.approvals import service
from tiqora.approvals.contract import JOB_HOOK, Actor, ApprovalDefinition
from tiqora.approvals.hooks import APPROVAL_HOOK_JOB, MAX_ATTEMPTS
from tiqora.approvals.models import TiqoraApprovalEvent
from tiqora.approvals.settings import APPROVALS_SECTION
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.events.pubsub import TIQORA_EVENTS_CHANNEL, publish_approval_changed
from tiqora.jobs.context import JobFailed, JobRetry
from tiqora.jobs.registry import JOB_HANDLERS
from tiqora.jobs.service import enqueue_job
from tiqora.plugins import sync_hooks
from tiqora.settings_sections.store import save_section_values

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
SETTINGS = Settings(environment="test")
SUBJECT = "test.subject"
ACTOR = Actor.agent(AG1)
ONE_STEP = ApprovalDefinition.model_validate(
    {"steps": [{"name": "Prüfung", "approvers": [{"kind": "user", "ref": str(AG3)}]}]}
)


async def _set(session: AsyncSession, **values: Any) -> None:
    await save_section_values(session, APPROVALS_SECTION, values, secret_key=SETTINGS.secret_key)


async def _requested(session: AsyncSession) -> int:
    info = await service.request_approval(
        session,
        ticket_id=T1,
        subject_type=SUBJECT,
        title="Freigabe appr97",
        actor=ACTOR,
        definition=ONE_STEP,
        settings=SETTINGS,
    )
    await session.commit()
    return info.approval_id


async def _decided(session: AsyncSession, *, approve: bool = True) -> tuple[int, str]:
    approval_id = await _requested(session)
    info = await (approve_all if approve else reject_first)(session, approval_id)
    await session.commit()
    return approval_id, info.status


async def _actions(session: AsyncSession, approval_id: int) -> list[str]:
    rows = await session.execute(
        select(TiqoraApprovalEvent.action)
        .where(TiqoraApprovalEvent.approval_id == approval_id)
        .order_by(TiqoraApprovalEvent.id)
    )
    return [str(r[0]) for r in rows]


async def _notes(session: AsyncSession) -> list[tuple[str, int, str, str]]:
    rows = await session.execute(
        text(
            "SELECT st.name, a.is_visible_for_customer, adm.a_subject, adm.a_body"
            " FROM article a"
            " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
            " JOIN article_data_mime adm ON adm.article_id = a.id"
            " WHERE a.ticket_id = :t ORDER BY a.id"
        ),
        {"t": T1},
    )
    return [(str(r[0]), int(r[1]), str(r[2]), str(r[3])) for r in rows]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_hook_calls_on_decided_once(url_fixture: str, request: pytest.FixtureRequest) -> None:
    fake = make_fake_subject_type()
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(fake):
            approval_id, status = await _decided(session)
            assert status == "approved"
            factory = shared_session_factory(session)

            result = await run_hook_now(factory, approval_id, "approved")
            assert result == {
                "approval_id": approval_id,
                "status": "approved",
                "note": "off",
                "hook": "called",
            }
            (outcome,) = fake.decided
            assert (outcome.approval_id, outcome.ticket_id, outcome.subject_type) == (
                approval_id,
                T1,
                SUBJECT,
            )
            assert (outcome.status, outcome.reason, outcome.comment) == ("approved", "votes", None)
            assert outcome.decided_at is not None
            assert (await _actions(session, approval_id))[-1] == "hook_done"

            again = await run_hook_now(factory, approval_id, "approved")  # job re-run
            assert again["hook"] == "already_done"
            assert len(fake.decided) == 1
            assert (await _actions(session, approval_id)).count("hook_done") == 1


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_hook_runs_for_rejected_and_cancelled(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    fake = make_fake_subject_type()
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(fake):
            factory = shared_session_factory(session)
            rejected_id, status = await _decided(session, approve=False)
            assert status == "rejected"
            await run_hook_now(factory, rejected_id, "rejected")

            cancelled_id = await _requested(session)
            await service.cancel_approval(session, cancelled_id, actor=ACTOR, settings=SETTINGS)
            await session.commit()
            await run_hook_now(factory, cancelled_id, "cancelled")

            assert [(o.approval_id, o.status) for o in fake.decided] == [
                (rejected_id, "rejected"),
                (cancelled_id, "cancelled"),
            ]
            assert fake.decided[1].reason == "cancelled"
            assert fake.decided[1].comment is None


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_hook_retry_then_failed_audit(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    fake = make_fake_subject_type()

    async def boom(session: AsyncSession, outcome: Any) -> None:
        raise RuntimeError("consumer bug with a secret name")

    failing = replace(fake.item, on_decided=boom)
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(failing):
            await _set(session, decision_note=True)
            await session.commit()
            approval_id, _ = await _decided(session)
            factory = shared_session_factory(session)

            for attempt in range(1, MAX_ATTEMPTS):
                with pytest.raises(JobRetry) as retry:
                    await run_hook_now(factory, approval_id, "approved", attempt=attempt)
                assert retry.value.delay_seconds == 60 * attempt
            assert "hook_failed" not in await _actions(session, approval_id)
            assert await _notes(session) == []  # the note rolled back with the failed hook

            with pytest.raises(JobFailed) as failed:
                await run_hook_now(factory, approval_id, "approved", attempt=MAX_ATTEMPTS)
            assert "secret" not in failed.value.message
            actions = await _actions(session, approval_id)
            assert actions[-1] == "hook_failed"
            row = (
                await session.execute(
                    select(TiqoraApprovalEvent).where(
                        TiqoraApprovalEvent.approval_id == approval_id,
                        TiqoraApprovalEvent.action == "hook_failed",
                    )
                )
            ).scalar_one()
            assert json.loads(row.detail or "{}") == {
                "attempt": MAX_ATTEMPTS,
                "error": "RuntimeError",
            }
            assert "hook_done" not in actions


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_hook_succeeds_on_retry(url_fixture: str, request: pytest.FixtureRequest) -> None:
    fake = make_fake_subject_type()
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(fake):
            approval_id, _ = await _decided(session)
            factory = shared_session_factory(session)
            fake.raise_next = RuntimeError("transient")
            with pytest.raises(JobRetry):
                await run_hook_now(factory, approval_id, "approved", attempt=1)
            assert fake.decided == []
            result = await run_hook_now(factory, approval_id, "approved", attempt=2)
            assert result["hook"] == "called"
            assert len(fake.decided) == 1
            assert "hook_failed" not in await _actions(session, approval_id)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_hook_idempotent_dedupe(url_fixture: str, request: pytest.FixtureRequest) -> None:
    fake = make_fake_subject_type()
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(fake) as registry:
            handler = registry.get(JOB_HANDLERS, JOB_HOOK)
            assert handler is APPROVAL_HOOK_JOB
            assert (handler.max_attempts, handler.requeue_if_interrupted) == (5, True)
            approval_id, _ = await _decided(session)  # the decision queued one hook job
            payload = {"approval_id": approval_id, "status": "approved"}
            again = await enqueue_job(
                session,
                kind=JOB_HOOK,
                payload=payload,
                created_by=None,
                dedupe_key=f"approval:{approval_id}:approved",
                registry=registry,
            )
            jobs = [
                j
                for j in (
                    await session.execute(select(TiqoraJob).where(TiqoraJob.kind == JOB_HOOK))
                ).scalars()
                if json.loads(j.payload).get("approval_id") == approval_id
            ]
            assert [j.id for j in jobs] == [again.id]
            assert (jobs[0].created_by, jobs[0].status) == (None, "queued")


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_decision_note_written_when_enabled(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    fake = make_fake_subject_type()
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(fake):
            await _set(session, decision_note=True)
            await session.commit()
            approval_id, _ = await _decided(session)
            before = await _notes(session)
            result = await run_hook_now(shared_session_factory(session), approval_id, "approved")
            assert result["note"] == "written"
            notes = (await _notes(session))[len(before) :]
            assert len(notes) == 1
            sender, visible, subject, body = notes[0]
            assert (sender, visible) == ("system", 0)
            assert subject == f"Freigabe #{approval_id} (default): freigegeben"
            assert f"Die Freigabe #{approval_id} (default) wurde freigegeben." in body
            assert "- Stufe 1: freigegeben" in body
            assert "Prüfung" not in body  # step names are free text: ids/statuses only
            assert "approved" not in body  # German note, no raw enum
            assert "Freigabe appr97" not in body  # the request title (free text) stays out
            assert len(fake.decided) == 1
            # F31: nothing in the note can wake the AI reply worker or notify the customer
            from tiqora.ai.outbox import article_sender_type

            article_id = (
                await session.execute(
                    text("SELECT MAX(id) FROM article WHERE ticket_id = :t"), {"t": T1}
                )
            ).scalar_one()
            assert await article_sender_type(session, int(article_id)) == "system"
            assert (
                await session.execute(select(TiqoraJob.id).where(TiqoraJob.kind.like("ai%")))
            ).first() is None


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_decision_note_veto_skipped(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = make_fake_subject_type()
    seen: list[Any] = []

    async def veto(operation: str, op: Any, **kwargs: Any) -> Any:
        seen.append((operation, op))
        raise sync_hooks.OperationVetoed("Nicht erlaubt")

    monkeypatch.setattr(sync_hooks, "has_sync_hooks", lambda operation, registry=None: True)
    monkeypatch.setattr(sync_hooks, "run_sync_hooks", veto)
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(fake):
            await _set(session, decision_note=True)
            await session.commit()
            approval_id, _ = await _decided(session)
            before = await _notes(session)
            result = await run_hook_now(shared_session_factory(session), approval_id, "approved")
            assert result["note"] == "skipped"
            assert await _notes(session) == before
            ((operation, op),) = seen
            assert (operation, op.ticket_id, op.sender_type, op.visible_for_customer) == (
                "article.create",
                T1,
                "system",
                False,
            )
            actions = await _actions(session, approval_id)
            assert actions[-2:] == ["note_skipped", "hook_done"]
            assert len(fake.decided) == 1  # the veto never blocks the consumer hook


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_no_note_by_default(url_fixture: str, request: pytest.FixtureRequest) -> None:
    fake = make_fake_subject_type()
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(fake):
            approval_id, _ = await _decided(session)
            before = await _notes(session)
            result = await run_hook_now(shared_session_factory(session), approval_id, "approved")
            assert result["note"] == "off"
            assert await _notes(session) == before


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_hook_skips_note_when_ticket_gone(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    fake = make_fake_subject_type()
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(fake):
            await _set(session, decision_note=True)
            await session.commit()
            approval_id, _ = await _decided(session)
            for table in ("ticket_history", "ticket"):
                column = "id" if table == "ticket" else "ticket_id"
                await session.execute(text(f"DELETE FROM {table} WHERE {column} = :t"), {"t": T1})
            await session.commit()
            result = await run_hook_now(shared_session_factory(session), approval_id, "approved")
            assert (result["note"], result["hook"]) == ("skipped", "called")
            assert len(fake.decided) == 1


async def test_publish_approval_changed_shape() -> None:
    redis = FakeRedis()
    await publish_approval_changed(redis, [7, 5, 7])  # type: ignore[arg-type]
    ((channel, raw),) = redis.published
    assert channel == TIQORA_EVENTS_CHANNEL
    assert json.loads(raw) == {"type": "approval_changed", "user_ids": [5, 7]}


async def test_publish_approval_changed_empty_and_failing() -> None:
    redis = FakeRedis()
    await publish_approval_changed(redis, [])  # type: ignore[arg-type]
    assert redis.published == []
    redis.fail = True
    await publish_approval_changed(redis, [1])  # type: ignore[arg-type]  # never raises


def test_sse_forwards_only_to_listed_users() -> None:
    from tiqora.api.v1.events import _should_forward

    raw = json.dumps({"type": "approval_changed", "user_ids": [5]})
    assert _should_forward(raw, set(), user_id=5) is True
    assert _should_forward(raw, set(), user_id=6) is False
    assert _should_forward(raw, set(), user_id=None) is False
    assert _should_forward(json.dumps({"type": "approval_changed"}), set(), user_id=5) is False
    assert (
        _should_forward(json.dumps({"type": "approval_changed", "user_ids": "5"}), set(), user_id=5)
        is False
    )


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_consumer_kit_round_trip(url_fixture: str, request: pytest.FixtureRequest) -> None:
    fake = make_fake_subject_type()
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(fake):
            assert assert_subject_registered(SUBJECT) is fake.item
            with pytest.raises(AssertionError, match="nope.type.*not registered.*test.subject"):
                assert_subject_registered("nope.type")
            approval_id = await _requested(session)
            info = await approve_all(session, approval_id)
            assert (info.status, info.reason) == ("approved", "votes")
            await session.commit()
            await run_hook_now(shared_session_factory(session), approval_id, "approved")
            assert [o.status for o in fake.decided] == ["approved"]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_consumer_kit_multi_stage_and_reject(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    from tests._approvals_helpers import AG4, AG5

    two_stages = ApprovalDefinition.model_validate(
        {
            "steps": [
                {"name": "Prüfung", "approvers": [{"kind": "user", "ref": str(AG3)}]},
                {
                    "name": "Freigabe",
                    "stage": 2,
                    "rule": "all",
                    "approvers": [
                        {"kind": "user", "ref": str(AG4)},
                        {"kind": "user", "ref": str(AG5)},
                    ],
                },
            ]
        }
    )
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            first = await service.request_approval(
                session,
                ticket_id=T1,
                subject_type=SUBJECT,
                title="Zwei Stufen",
                actor=ACTOR,
                definition=two_stages,
                approval_key="a",
                settings=SETTINGS,
            )
            info = await approve_all(session, first.approval_id)
            assert info.status == "approved"
            votes = (
                await session.execute(
                    text("SELECT COUNT(*) FROM tiqora_approval_vote WHERE approval_id = :a"),
                    {"a": first.approval_id},
                )
            ).scalar_one()
            assert votes == 3

            second = await service.request_approval(
                session,
                ticket_id=T1,
                subject_type=SUBJECT,
                title="Abgelehnt",
                actor=ACTOR,
                definition=two_stages,
                approval_key="b",
                settings=SETTINGS,
            )
            rejected = await reject_first(session, second.approval_id)
            assert rejected.status == "rejected"
