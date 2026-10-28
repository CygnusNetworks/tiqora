"""Approval sweeper (A12): deadlines, reminders, escalation, invalid approvers, orphans, links.

Helper world ids 970101+; seeded ids of this module (band 971600-971699) are the token hashes
``971600 + n``. Requests are autoincrement rows inside the rolled-back ``approvals_session``
(``shared_session_factory`` hands the sweeper that one session, whose commits and rollbacks act
on a savepoint); the two tests that race real sessions seed a committed world and clean up.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests._approvals_helpers import (
    AG1,
    AG2,
    AG3,
    AG4,
    AG5,
    ANDERE,
    T1,
    CapturingMail,
    approvals_session,
    delete_world,
    ensure_tiqora_tables,
    installed_registry,
    make_fake_subject_type,
    seed_world,
    to_async_url,
)
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests.approvals_consumer_kit import run_hook_now, shared_session_factory
from tiqora.approvals import service, sweep
from tiqora.approvals.contract import (
    EVENT_DECIDED,
    EVENT_ESCALATED,
    JOB_HOOK,
    JOB_MAIL,
    Actor,
    ApprovalDefinition,
    ApprovalError,
)
from tiqora.approvals.models import (
    TiqoraApproval,
    TiqoraApprovalApprover,
    TiqoraApprovalEvent,
    TiqoraApprovalStep,
    TiqoraApprovalToken,
)
from tiqora.approvals.notify import ApprovalMailPayload, run_approval_mail
from tiqora.approvals.settings import APPROVALS_SECTION
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraJob, TiqoraSettings
from tiqora.domain.history_render import render_history_entry
from tiqora.domain.settings_store import KEY_NOTIFICATION_SENDER_EMAIL, set_setting
from tiqora.jobs.context import JobContext
from tiqora.jobs.service import utc_now
from tiqora.settings_sections.store import save_section_values
from tiqora.worker import approvals_sweep as worker_module
from tiqora.worker.services import DAEMON_SERVICES_BY_SLUG

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
BASE = "https://tiqora.example.org"
SETTINGS = Settings(environment="test", public_base_url=BASE)
SUBJECT = "test.subject"
REQUESTER = Actor.agent(AG2)  # AG1 is an escalation target in these tests
ZERO = {
    "overdue": 0,
    "escalated": 0,
    "timeout_decided": 0,
    "reminded": 0,
    "removed": 0,
    "cancelled": 0,
    "links_purged": 0,
}


def _step(name: str, *users: int, **extra: Any) -> dict[str, Any]:
    return {"name": name, "approvers": [{"kind": "user", "ref": str(u)} for u in users], **extra}


def _defn(*steps: dict[str, Any]) -> ApprovalDefinition:
    return ApprovalDefinition.model_validate({"steps": list(steps)})


def _now() -> datetime:
    return utc_now().replace(microsecond=0)


async def _set(session: AsyncSession, **values: Any) -> None:
    await save_section_values(session, APPROVALS_SECTION, values, secret_key=SETTINGS.secret_key)


async def _request(session: AsyncSession, definition: ApprovalDefinition, **extra: Any) -> int:
    args: dict[str, Any] = {
        "ticket_id": T1,
        "subject_type": SUBJECT,
        "title": "Freigabe appr97",
        "actor": REQUESTER,
        "definition": definition,
        "settings": SETTINGS,
    }
    args.update(extra)
    info = await service.request_approval(session, **args)
    await session.commit()
    return info.approval_id


async def _tick(session: AsyncSession, now: datetime, redis_client: Any = None) -> dict[str, int]:
    await session.commit()  # the sweeper rolls back items that have nothing to do
    return await sweep.run_approvals_sweep(
        settings=SETTINGS,
        session_factory=shared_session_factory(session),  # type: ignore[arg-type]
        now=now,
        redis_client=redis_client,
    )


async def _overdue_by(session: AsyncSession, approval_id: int, now: datetime, **kw: int) -> None:
    """Sets every step's deadline ``hours`` (default 1) before *now*."""
    await session.execute(
        text("UPDATE tiqora_approval_step SET due_at = :d WHERE approval_id = :a"),
        {"d": now - timedelta(hours=kw.get("hours", 1)), "a": approval_id},
    )
    await session.commit()


async def _row(session: AsyncSession, approval_id: int) -> TiqoraApproval:
    return (
        await session.execute(
            select(TiqoraApproval)
            .where(TiqoraApproval.id == approval_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _steps(session: AsyncSession, approval_id: int) -> list[TiqoraApprovalStep]:
    rows = await session.execute(
        select(TiqoraApprovalStep)
        .where(TiqoraApprovalStep.approval_id == approval_id)
        .order_by(TiqoraApprovalStep.position)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


async def _approvers(session: AsyncSession, approval_id: int) -> list[TiqoraApprovalApprover]:
    rows = await session.execute(
        select(TiqoraApprovalApprover)
        .where(TiqoraApprovalApprover.approval_id == approval_id)
        .order_by(TiqoraApprovalApprover.id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


async def _by_user(session: AsyncSession, approval_id: int) -> dict[int, str]:
    return {
        int(a.user_id): a.status
        for a in await _approvers(session, approval_id)
        if a.user_id is not None
    }


async def _events(
    session: AsyncSession, approval_id: int, action: str | None = None
) -> list[TiqoraApprovalEvent]:
    stmt = select(TiqoraApprovalEvent).where(TiqoraApprovalEvent.approval_id == approval_id)
    if action is not None:
        stmt = stmt.where(TiqoraApprovalEvent.action == action)
    rows = await session.execute(stmt.order_by(TiqoraApprovalEvent.id))
    return list(rows.scalars())


async def _jobs(session: AsyncSession, kind: str, approval_id: int) -> list[dict[str, Any]]:
    rows = await session.execute(
        select(TiqoraJob).where(TiqoraJob.kind == kind).order_by(TiqoraJob.id)
    )
    payloads = [json.loads(j.payload) for j in rows.scalars()]
    return [p for p in payloads if p.get("approval_id") == approval_id]


async def _mails(session: AsyncSession, approval_id: int, kind: str) -> list[dict[str, Any]]:
    return [p for p in await _jobs(session, JOB_MAIL, approval_id) if p["kind"] == kind]


async def _outbox(session: AsyncSession) -> list[tuple[str, dict[str, Any]]]:
    rows = await session.execute(
        text(
            "SELECT event_type, payload FROM tiqora_event_outbox WHERE ticket_id = :t ORDER BY id"
        ),
        {"t": T1},
    )
    return [(str(r[0]), json.loads(r[1])) for r in rows]


# --- 1. deadlines -------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_overdue_notify(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(
                session, _defn(_step("CAB", AG3, AG4, rule="all", due_hours=1, on_timeout="notify"))
            )
            now = _now()
            await _overdue_by(session, aid, now)

            assert await _tick(session, now) == {**ZERO, "overdue": 1}

            (step,) = await _steps(session, aid)
            assert (step.status, step.overdue_at) == ("active", now)
            assert await _by_user(session, aid) == {AG3: "pending", AG4: "pending"}
            assert [e.actor for e in await _events(session, aid, "overdue")] == ["system"]
            (mail,) = await _mails(session, aid, "overdue")
            assert sorted(mail["approver_ids"]) == sorted(
                a.id for a in await _approvers(session, aid)
            )
            assert mail["include_requester"] is True

            assert await _tick(session, now + timedelta(minutes=5)) == ZERO  # reported once
            assert len(await _events(session, aid, "overdue")) == 1
            assert len(await _mails(session, aid, "overdue")) == 1


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_overdue_escalate_adds_decisive(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    definition = _defn(
        _step(
            "CAB",
            AG3,
            AG4,
            rule="all",
            due_hours=1,
            on_timeout="escalate",
            escalation=[{"kind": "user", "ref": str(AG1)}],
        )
    )
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, definition)
            now = _now()
            await _overdue_by(session, aid, now)

            assert await _tick(session, now) == {**ZERO, "escalated": 1}

            (step,) = await _steps(session, aid)
            rows = {a.user_id: a for a in await _approvers(session, aid)}
            assert (step.status, step.overdue_at) == ("active", now)
            assert (rows[AG1].status, rows[AG1].decisive, rows[AG1].source_kind) == (
                "pending",
                True,
                "escalation",
            )
            assert not rows[AG3].decisive and not rows[AG4].decisive
            (esc,) = await _events(session, aid, "escalated")
            assert esc.step_id == step.id and json.loads(esc.detail or "{}") == {"approvers": 1}
            assert [
                p["step_id"] for name, p in await _outbox(session) if name == EVENT_ESCALATED
            ] == [step.id]
            (mail,) = await _mails(session, aid, "escalation")
            assert mail["approver_ids"] == [rows[AG1].id]
            assert await _mails(session, aid, "overdue") == []

            assert await _tick(session, now + timedelta(hours=1)) == ZERO

            # the escalation approver decides alone although the rule is "all" with two pending
            await session.rollback()
            result = await service.decide(
                session,
                aid,
                actor=Actor.agent(AG1),
                decision="approved",
                channel="ui",
                settings=SETTINGS,
            )
            await session.commit()
            assert result.step_status == "approved"
            assert (await _row(session, aid)).status == "approved"
            assert await _by_user(session, aid) == {
                AG1: "approved",
                AG3: "not_needed",
                AG4: "not_needed",
            }


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_escalation_skips_prior_voter_and_upgrades_pending_row(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """F8 for escalation: AG3 voted, so AG3 is never made decisive; AG4's pending row is."""
    definition = _defn(
        _step(
            "CAB",
            AG3,
            AG4,
            AG5,
            rule="all",
            due_hours=1,
            on_timeout="escalate",
            escalation=[{"kind": "user", "ref": str(AG3)}, {"kind": "user", "ref": str(AG4)}],
        )
    )
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, definition)
            await session.rollback()
            await service.decide(
                session,
                aid,
                actor=Actor.agent(AG3),
                decision="approved",
                channel="ui",
                settings=SETTINGS,
            )
            await session.commit()
            now = _now()
            await _overdue_by(session, aid, now)

            assert await _tick(session, now) == {**ZERO, "escalated": 1}

            rows = {a.user_id: a for a in await _approvers(session, aid)}
            assert len(rows) == 3  # nobody added: AG4 already had a row
            assert (rows[AG3].status, rows[AG3].decisive) == ("approved", False)
            assert (rows[AG4].status, rows[AG4].decisive) == ("pending", True)
            (mail,) = await _mails(session, aid, "escalation")
            assert mail["approver_ids"] == [rows[AG4].id]


@pytest.mark.db
@pytest.mark.parametrize("rule", ["all", "quorum"])
@pytest.mark.parametrize("url_fixture", URLS)
async def test_upgraded_approver_stays_in_the_rule(
    url_fixture: str, request: pytest.FixtureRequest, rule: str
) -> None:
    """Escalating to a regular approver must not let the others decide without them."""
    extra: dict[str, Any] = {"quorum": 2} if rule == "quorum" else {}
    definition = _defn(
        _step(
            "CAB",
            AG3,
            AG4,
            rule=rule,
            due_hours=1,
            on_timeout="escalate",
            escalation=[{"kind": "user", "ref": str(AG4)}],
            **extra,
        )
    )
    fake = _Redis()
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, definition)
            now = _now()
            await _overdue_by(session, aid, now)
            assert await _tick(session, now, fake) == {**ZERO, "escalated": 1}
            rows = {a.user_id: a for a in await _approvers(session, aid)}
            assert len(rows) == 2 and rows[AG4].decisive and not rows[AG3].decisive
            # F24: the upgrade changes no status but the inbox row of AG4 now reads "decisive"
            assert fake.published == [{"type": "approval_changed", "user_ids": [AG4]}]

            await session.rollback()
            await service.decide(
                session,
                aid,
                actor=Actor.agent(AG3),
                decision="approved",
                channel="ui",
                settings=SETTINGS,
            )
            await session.commit()
            (step,) = await _steps(session, aid)
            assert step.status == "active"  # AG3 alone neither approves nor (quorum) rejects
            assert (await _row(session, aid)).status == "pending"

            await session.rollback()
            result = await service.decide(
                session,
                aid,
                actor=Actor.agent(AG4),
                decision="approved",
                channel="ui",
                settings=SETTINGS,
            )
            await session.commit()
            assert result.step_status == "approved"
            assert (await _row(session, aid)).status == "approved"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_upgraded_approver_decides_alone(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    definition = _defn(
        _step(
            "CAB",
            AG3,
            AG4,
            rule="all",
            due_hours=1,
            on_timeout="escalate",
            escalation=[{"kind": "user", "ref": str(AG4)}],
        )
    )
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, definition)
            now = _now()
            await _overdue_by(session, aid, now)
            await _tick(session, now)
            await session.rollback()
            result = await service.decide(
                session,
                aid,
                actor=Actor.agent(AG4),
                decision="approved",
                channel="ui",
                settings=SETTINGS,
            )
            await session.commit()
            assert result.step_status == "approved"
            assert await _by_user(session, aid) == {AG3: "not_needed", AG4: "approved"}


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_escalation_without_target_reports_overdue(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """The escalation list only names the requester: nobody to escalate to -> plain notice."""
    definition = _defn(
        _step(
            "CAB",
            AG3,
            due_hours=1,
            on_timeout="escalate",
            escalation=[{"kind": "user", "ref": str(AG2)}],  # AG2 is the requester
        )
    )
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, definition)
            now = _now()
            await _overdue_by(session, aid, now)

            assert await _tick(session, now) == {**ZERO, "overdue": 1}

            assert len(await _approvers(session, aid)) == 1
            assert await _events(session, aid, "escalated") == []
            assert [
                (e.action, json.loads(e.detail or "{}").get("reason"))
                for e in await _events(session, aid, "approver_excluded")
            ] == [("approver_excluded", "self")]
            assert len(await _mails(session, aid, "overdue")) == 1


@pytest.mark.db
@pytest.mark.parametrize(
    ("on_timeout", "two_steps", "request_status", "request_reason", "next_stage"),
    [
        ("reject", True, "rejected", "timeout", None),
        ("approve", True, "pending", None, 2),
        ("approve", False, "approved", "timeout", None),
    ],
)
@pytest.mark.parametrize("url_fixture", URLS)
async def test_overdue_reject_and_approve(
    url_fixture: str,
    request: pytest.FixtureRequest,
    on_timeout: str,
    two_steps: bool,
    request_status: str,
    request_reason: str | None,
    next_stage: int | None,
) -> None:
    steps = [_step("Prüfung", AG3, AG4, due_hours=1, on_timeout=on_timeout)]
    if two_steps:
        steps.append(_step("Freigabe", AG5, stage=2))
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, _defn(*steps))
            now = _now()
            await session.execute(
                text(
                    "UPDATE tiqora_approval_step SET due_at = :d"
                    " WHERE approval_id = :a AND position = 1"
                ),
                {"d": now - timedelta(minutes=5), "a": aid},
            )

            assert await _tick(session, now) == {**ZERO, "timeout_decided": 1}

            outcome = "rejected" if on_timeout == "reject" else "approved"
            first, *rest = await _steps(session, aid)
            assert (first.status, first.reason) == (outcome, "timeout")
            row = await _row(session, aid)
            assert (row.status, row.reason, row.current_stage) == (
                request_status,
                request_reason,
                next_stage,
            )
            by_user = await _by_user(session, aid)
            assert by_user[AG3] == by_user[AG4] == "not_needed"
            (event,) = await _events(session, aid, "timeout_decided")
            assert json.loads(event.detail or "{}") == {"decision": outcome}
            if request_status == "pending":
                assert [s.status for s in rest] == ["active"]
                assert by_user[AG5] == "pending"
                assert len(await _mails(session, aid, "request")) == 2  # stage 1, then stage 2
                assert await _jobs(session, JOB_HOOK, aid) == []
            else:
                assert [s.status for s in rest] == (["cancelled"] if two_steps else [])
                assert await _jobs(session, JOB_HOOK, aid) == [
                    {"approval_id": aid, "status": request_status}
                ]
                assert [p["reason"] for n, p in await _outbox(session) if n == EVENT_DECIDED] == [
                    "timeout"
                ]
            assert await _tick(session, now + timedelta(minutes=1)) == ZERO


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_timeout_approval_behind_a_voted_stage_reads_timeout(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """A-I2: stage 1 voted ("passt"), stage 2 approved by its deadline: reason ``timeout``.

    The hook hands the consumer no comment (stage 1's vote comment is not the decision).
    """
    fake = make_fake_subject_type()
    definition = _defn(
        _step("Fachbereich", AG3),
        _step("CAB", AG4, stage=2, due_hours=1, on_timeout="approve"),
    )
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(fake):
            aid = await _request(session, definition)
            await service.decide(
                session,
                aid,
                actor=Actor.agent(AG3),
                decision="approved",
                channel="ui",
                comment="passt",
                settings=SETTINGS,
            )
            await session.commit()
            now = _now()
            await session.execute(
                text(
                    "UPDATE tiqora_approval_step SET due_at = :d"
                    " WHERE approval_id = :a AND position = 2"
                ),
                {"d": now - timedelta(minutes=5), "a": aid},
            )

            assert await _tick(session, now) == {**ZERO, "timeout_decided": 1}

            assert [(s.status, s.reason) for s in await _steps(session, aid)] == [
                ("approved", "votes"),
                ("approved", "timeout"),
            ]
            row = await _row(session, aid)
            assert (row.status, row.reason) == ("approved", "timeout")
            assert [p["reason"] for n, p in await _outbox(session) if n == EVENT_DECIDED] == [
                "timeout"
            ]
            await run_hook_now(shared_session_factory(session), aid, "approved")
            (outcome,) = fake.decided
            assert (outcome.status, outcome.reason, outcome.comment) == (
                "approved",
                "timeout",
                None,
            )


# --- race: sweeper vs. vote ---------------------------------------------------------------------

_MARKS = (
    "tiqora_approval_event",
    "tiqora_approval_token",
    "tiqora_approval_vote",
    "tiqora_approval_approver",
    "tiqora_approval_step",
    "tiqora_approval",
    "tiqora_job",
    "tiqora_event_outbox",
    "ticket_history",
)


@asynccontextmanager
async def _committed_world(url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    ensure_tiqora_tables(url)
    marks = snapshot_marks(url, _MARKS)
    engine = create_async_engine(to_async_url(url), poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as seed:
            await seed_world(seed)
            await seed.commit()
        yield factory
    finally:
        delete_above_marks(url, marks)
        async with factory() as cleanup:
            await delete_world(cleanup)
            await cleanup.commit()
        await engine.dispose()


async def _vote(factory: async_sessionmaker[AsyncSession], aid: int, user_id: int) -> str:
    async with factory() as session:
        await session.rollback()  # callers roll back before deciding (F1(a))
        try:
            await service.decide(
                session,
                aid,
                actor=Actor.agent(user_id),
                decision="approved",
                channel="ui",
                settings=SETTINGS,
            )
        except ApprovalError as exc:
            await session.rollback()
            return exc.code
        except DBAPIError as exc:  # deadlock, lock wait timeout
            await session.rollback()
            return f"db_error:{type(exc.orig).__name__}"
        await session.commit()
        return "ok"


async def _sweep(factory: async_sessionmaker[AsyncSession], now: datetime) -> dict[str, int]:
    return await sweep.run_approvals_sweep(settings=SETTINGS, session_factory=factory, now=now)


async def _final(factory: async_sessionmaker[AsyncSession], aid: int) -> tuple[str, str | None]:
    """(status, reason) after checking exactly one Decided event / audit row / hook job."""
    async with factory() as session:
        row = await _row(session, aid)
        decided = [e for e in await _events(session, aid) if e.action == "decided"]
        outbox = [
            p for n, p in await _outbox(session) if n == EVENT_DECIDED and p["approval_id"] == aid
        ]
        hooks = await _jobs(session, JOB_HOOK, aid)
        assert len(decided) == len(outbox) == len(hooks) == 1
        return row.status, row.reason


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_timeout_races_vote(url_fixture: str, request: pytest.FixtureRequest) -> None:
    definition = _defn(_step("Prüfung", AG3, AG4, due_hours=1, on_timeout="reject"))
    async with _committed_world(request.getfixturevalue(url_fixture)) as factory:
        with installed_registry(make_fake_subject_type()):
            now = _now()

            async def new_request() -> int:
                async with factory() as setup:
                    aid = await _request(setup, definition)
                    await _overdue_by(setup, aid, now)
                    return aid

            # the vote holds the lock first: the sweeper waits, then finds the step closed
            first = await new_request()
            async with factory() as voter:
                await voter.rollback()
                await service.decide(
                    voter,
                    first,
                    actor=Actor.agent(AG3),
                    decision="approved",
                    channel="ui",
                    settings=SETTINGS,
                )
                swept = asyncio.create_task(_sweep(factory, now))
                await asyncio.sleep(1.5)
                assert not swept.done(), "the sweeper must wait for the voter's row lock"
                await voter.commit()
            try:
                counts = await asyncio.wait_for(swept, timeout=30)
            except TimeoutError:
                pytest.fail("sweeper did not finish after the vote committed (lock regression?)")
            assert counts["timeout_decided"] == 0
            assert await _final(factory, first) == ("approved", "votes")

            # both at once: whoever locks first wins, the other sees a closed request
            second = await new_request()
            try:
                outcome, vote = await asyncio.wait_for(
                    asyncio.gather(_sweep(factory, now), _vote(factory, second, AG4)), timeout=45
                )
            except TimeoutError:
                pytest.fail("racing sweep and vote did not finish within 45 s")
            status, reason = await _final(factory, second)
            if vote == "ok":
                assert (status, reason) == ("approved", "votes")
                assert outcome["timeout_decided"] == 0
            else:
                assert vote == "approval_closed"
                assert (status, reason) == ("rejected", "timeout")
                assert outcome["timeout_decided"] == 1
            async with factory() as check:
                assert len([e for e in await _events(check, second) if e.action == "voted"]) == (
                    1 if vote == "ok" else 0
                )


# --- 2. reminders -------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_reminders_interval_and_max(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _set(session, reminder_interval_hours=24, reminder_max=2)
            aid = await _request(session, _defn(_step("CAB", AG3, AG4, rule="all")))
            (step,) = await _steps(session, aid)
            assert step.activated_at is not None and step.due_at is None
            t0 = step.activated_at

            got = {}
            for hours in (25, 26, 49, 80):
                got[hours] = (await _tick(session, t0 + timedelta(hours=hours)))["reminded"]
            assert got == {25: 1, 26: 0, 49: 1, 80: 0}

            (step,) = await _steps(session, aid)
            assert (step.reminder_count, step.last_reminder_at) == (
                2,
                (t0 + timedelta(hours=49)).replace(microsecond=0),
            )
            mails = await _mails(session, aid, "reminder")
            assert len(mails) == 2
            pending = sorted(a.id for a in await _approvers(session, aid))
            assert all(sorted(m["approver_ids"]) == pending for m in mails)
            assert [
                json.loads(e.detail or "{}") for e in await _events(session, aid, "reminded")
            ] == [
                {"count": 1, "recipients": 2},
                {"count": 2, "recipients": 2},
            ]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_reminders_off(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _set(session, reminder_interval_hours=0)
            aid = await _request(session, _defn(_step("CAB", AG3)))
            (step,) = await _steps(session, aid)
            assert step.activated_at is not None
            assert await _tick(session, step.activated_at + timedelta(days=30)) == ZERO
            await _set(session, reminder_interval_hours=24, reminder_max=0)
            assert await _tick(session, step.activated_at + timedelta(days=30)) == ZERO
            assert await _mails(session, aid, "reminder") == []
            assert await _events(session, aid, "reminded") == []


# --- 3. invalid approvers -----------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_invalid_approver_removed_and_reevaluated(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Rule all over AG3 and AG5: AG3 approved, AG5 became invalid -> removed -> approved."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, _defn(_step("CAB", AG3, AG5, rule="all")))
            await session.rollback()
            await service.decide(
                session,
                aid,
                actor=Actor.agent(AG3),
                decision="approved",
                channel="ui",
                settings=SETTINGS,
            )
            await session.execute(text("UPDATE users SET valid_id = 2 WHERE id = :u"), {"u": AG5})
            await session.commit()

            assert await _tick(session, _now()) == {**ZERO, "removed": 1}

            assert await _by_user(session, aid) == {AG3: "approved", AG5: "removed"}
            (step,) = await _steps(session, aid)
            assert (step.status, step.reason) == ("approved", "votes")
            row = await _row(session, aid)
            assert (row.status, row.reason) == ("approved", "votes")
            (event,) = await _events(session, aid, "approver_removed")
            assert json.loads(event.detail or "{}") == {"reason": "invalid"}
            assert event.approver_id == next(
                a.id for a in await _approvers(session, aid) if a.user_id == AG5
            )
            assert await _jobs(session, JOB_HOOK, aid) == [
                {"approval_id": aid, "status": "approved"}
            ]


@pytest.mark.db
@pytest.mark.parametrize(
    ("on_no_approver", "status"), [("reject", "rejected"), ("approve", "approved")]
)
@pytest.mark.parametrize("url_fixture", URLS)
async def test_last_approver_invalid_applies_on_no_approver(
    url_fixture: str, request: pytest.FixtureRequest, on_no_approver: str, status: str
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(
                session, _defn(_step("Prüfung", AG3, on_no_approver=on_no_approver))
            )
            await session.execute(text("UPDATE users SET valid_id = 2 WHERE id = :u"), {"u": AG3})
            await session.commit()

            assert await _tick(session, _now()) == {**ZERO, "removed": 1}

            assert await _by_user(session, aid) == {AG3: "removed"}
            (step,) = await _steps(session, aid)
            assert (step.status, step.reason) == (status, "no_approvers")
            row = await _row(session, aid)
            assert (row.status, row.reason) == (status, "no_approvers")
            assert await _jobs(session, JOB_HOOK, aid) == [{"approval_id": aid, "status": status}]
            assert await _tick(session, _now()) == ZERO


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_invalid_customer_approver_removed(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """A customer approver whose customer user was switched off drops out like an agent."""
    async with approvals_session(request, url_fixture) as session:
        await _set(session, customer_approvers_enabled=True)
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            definition = _defn(
                {
                    "name": "Kunde",
                    "approvers": [
                        {"kind": "customer_user", "ref": "appr97-k1"},
                        {"kind": "user", "ref": str(AG3)},
                    ],
                    "rule": "all",
                }
            )
            aid = await _request(session, definition)
            customers = [a for a in await _approvers(session, aid) if a.customer_login]
            assert [a.principal for a in customers] == ["customer:appr97-k1"]
            await session.execute(
                text("UPDATE customer_user SET valid_id = 2 WHERE login = 'appr97-k1'")
            )
            await session.commit()

            assert await _tick(session, _now()) == {**ZERO, "removed": 1}

            (gone,) = [a for a in await _approvers(session, aid) if a.customer_login]
            assert gone.status == "removed"
            assert (await _row(session, aid)).status == "pending"  # AG3 still has to decide


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_customer_outside_company_drops_out_and_the_all_step_completes(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """B-I1: the ticket moved to another company; the customer's row becomes ``not_needed``
    (audit ``outside_company``) and the ``all`` step no longer waits for a refused vote."""
    async with approvals_session(request, url_fixture) as session:
        await _set(session, customer_approvers_enabled=True)
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            definition = _defn(
                {
                    "name": "Kunde",
                    "approvers": [
                        {"kind": "customer_user", "ref": "appr97-k1"},
                        {"kind": "user", "ref": str(AG3)},
                    ],
                    "rule": "all",
                }
            )
            aid = await _request(session, definition)
            await service.decide(
                session,
                aid,
                actor=Actor.agent(AG3),
                decision="approved",
                channel="ui",
                settings=SETTINGS,
            )
            await session.commit()
            assert await _tick(session, _now()) == ZERO  # still in the company: nothing to do
            await session.execute(
                text("UPDATE ticket SET customer_id = :c WHERE id = :t"), {"c": ANDERE, "t": T1}
            )
            await session.commit()

            assert await _tick(session, _now()) == {**ZERO, "removed": 1}

            (gone,) = [a for a in await _approvers(session, aid) if a.customer_login]
            assert gone.status == "not_needed"
            (event,) = [
                e
                for e in await _events(session, aid, "approver_removed")
                if e.approver_id == gone.id
            ]
            assert json.loads(event.detail or "{}") == {"reason": "outside_company"}
            row = await _row(session, aid)
            assert (row.status, row.reason) == ("approved", "votes")
            assert await _tick(session, _now()) == ZERO


# --- 4. deleted tickets -------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_deleted_ticket_cancels(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, _defn(_step("CAB", AG3, AG4)))
            outbox_before = await _outbox(session)
            history_before = (
                await session.execute(
                    text("SELECT COUNT(*) FROM ticket_history WHERE ticket_id = :t"), {"t": T1}
                )
            ).scalar_one()
            assert history_before >= 1
            # Znuny deleted the ticket: its history goes with it
            await session.execute(
                text("DELETE FROM ticket_history WHERE ticket_id = :t"), {"t": T1}
            )
            await session.execute(text("DELETE FROM ticket WHERE id = :t"), {"t": T1})
            await session.commit()

            assert await _tick(session, _now()) == {**ZERO, "cancelled": 1}

            row = await _row(session, aid)
            assert (row.status, row.reason) == ("cancelled", "ticket_deleted")
            assert await _by_user(session, aid) == {AG3: "not_needed", AG4: "not_needed"}
            assert await _outbox(session) == outbox_before  # no fan-out for a ticket that is gone
            assert [e.actor for e in await _events(session, aid, "cancelled")] == ["system"]
            assert await _jobs(session, JOB_HOOK, aid) == [
                {"approval_id": aid, "status": "cancelled"}
            ]
            left = (
                await session.execute(
                    text("SELECT COUNT(*) FROM ticket_history WHERE ticket_id = :t"), {"t": T1}
                )
            ).scalar_one()
            assert left == 0  # no history line for a ticket that is gone
            assert await _tick(session, _now()) == ZERO


# --- 5. link purge ------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_links_purged(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, _defn(_step("CAB", AG3)))
            (approver,) = await _approvers(session, aid)
            now = _now()
            old, fresh = now - timedelta(days=31), now - timedelta(days=2)

            def token(n: int, **cols: datetime | None) -> TiqoraApprovalToken:
                return TiqoraApprovalToken(
                    approval_id=aid,
                    approver_id=approver.id,
                    ticket_id=T1,
                    token_hash=f"{971600 + n:064x}",
                    created_at=old,
                    expires_at=cols.pop("expires_at", now + timedelta(days=1)),  # type: ignore[arg-type]
                    **cols,
                )

            session.add_all(
                [
                    token(1, expires_at=old),  # expired long ago
                    token(2, used_at=old),  # redeemed long ago
                    token(3, revoked_at=old),  # revoked long ago
                    token(4, revoked_at=fresh),  # dead, but not for 30 days yet
                    token(5),  # live
                ]
            )
            await session.commit()

            assert await _tick(session, now) == {**ZERO, "links_purged": 3}

            left = await session.execute(
                select(TiqoraApprovalToken.token_hash)
                .where(TiqoraApprovalToken.approval_id == aid)
                .order_by(TiqoraApprovalToken.id)
            )
            assert [str(h) for h in left.scalars()] == [f"{971600 + n:064x}" for n in (4, 5)]


# --- (c) a link that would be born expired ------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_escalation_long_after_deadline_mails_the_inbox_url(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deadline more than 24 h past: the escalation mail carries no (dead) link, only the inbox."""
    mail = CapturingMail(monkeypatch)
    definition = _defn(
        _step(
            "CAB",
            AG3,
            due_hours=1,
            on_timeout="escalate",
            escalation=[{"kind": "user", "ref": str(AG1)}],
        )
    )
    async with approvals_session(request, url_fixture) as session:
        await set_setting(session, KEY_NOTIFICATION_SENDER_EMAIL, "tiqora@example.org")
        await session.execute(
            text(
                "INSERT INTO user_preferences (user_id, preferences_key, preferences_value)"
                " VALUES (:u, 'UserEmail', 'appr97-agent1@example.org')"
            ),
            {"u": AG1},
        )
        await session.commit()
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, definition)
            now = _now()
            await _overdue_by(session, aid, now, hours=30)
            assert (await _tick(session, now))["escalated"] == 1
            (queued,) = await _mails(session, aid, "escalation")

            ctx = JobContext(
                job_id=1,
                kind=JOB_MAIL,
                created_by=None,
                settings=SETTINGS,
                session_factory=shared_session_factory(session),  # type: ignore[arg-type]
                attempt=1,
                max_attempts=3,
            )
            await run_approval_mail(ctx, ApprovalMailPayload.model_validate(queued))

            assert len(mail.sent) == 1
            body = str(mail.sent[0].get_content())
            assert "/approve#t=" not in body
            assert f"{BASE}/agent/approvals" in body
            tokens = await session.execute(
                select(TiqoraApprovalToken).where(TiqoraApprovalToken.approval_id == aid)
            )
            assert list(tokens.scalars()) == []


# --- publishing ---------------------------------------------------------------------------------


class _Redis:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish(self, channel: str, message: str) -> None:
        self.published.append(json.loads(message))


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_changes_publish_approval_changed(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """F24: after the item commit, the agents whose inbox changed get one notice."""
    fake = _Redis()
    definition = _defn(_step("Prüfung", AG3, AG4, due_hours=1, on_timeout="reject"))
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, definition)
            now = _now()
            await _overdue_by(session, aid, now)
            assert (await _tick(session, now, fake))["timeout_decided"] == 1
            assert fake.published == [{"type": "approval_changed", "user_ids": [AG3, AG4]}]
            assert (await _row(session, aid)).status == "rejected"
            fake.published.clear()
            assert await _tick(session, now, fake) == ZERO
            assert fake.published == []


# --- daemon wiring ------------------------------------------------------------------------------


def test_daemon_catalogue_has_approvals() -> None:
    service_row = DAEMON_SERVICES_BY_SLUG["approvals"]
    assert service_row.enabled_key == "daemon.approvals.enabled"
    assert service_row.interval_key == "daemon.approvals.interval_seconds"
    assert (service_row.default_enabled, service_row.toggleable) == (True, True)
    assert service_row.schedule_kind == "interval"
    assert service_row.interval_settings_attr == "approvals_sweep_interval_seconds"
    assert Settings(environment="test").approvals_sweep_interval_seconds == 60


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_worker_tick_switch(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _set(session, reminder_interval_hours=1, reminder_max=1)
            aid = await _request(session, _defn(_step("CAB", AG3)))
            await session.execute(
                text("UPDATE tiqora_approval_step SET activated_at = :t WHERE approval_id = :a"),
                {"t": utc_now() - timedelta(hours=3), "a": aid},
            )
            await session.commit()
            monkeypatch.setattr(
                worker_module, "get_session_factory", lambda: shared_session_factory(session)
            )
            fake = _Redis()
            monkeypatch.setattr(worker_module, "get_pubsub_redis", lambda settings=None: fake)

            await set_setting(session, "daemon.approvals.enabled", "0")
            assert await worker_module.run_approvals_sweep_worker_tick(SETTINGS) == {"skipped": 1}
            assert await _mails(session, aid, "reminder") == []

            await set_setting(session, "daemon.approvals.enabled", "1")
            assert await worker_module.run_approvals_sweep_worker_tick(SETTINGS) == {
                **ZERO,
                "reminded": 1,
            }
            assert len(await _mails(session, aid, "reminder")) == 1
            row = (
                await session.execute(
                    select(TiqoraSettings).where(TiqoraSettings.key == "daemon.approvals.enabled")
                )
            ).scalar_one()
            assert row.value == "1"


# --- robustness ---------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_failing_item_does_not_starve_the_rest(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A poison item is rolled back and skipped; the item behind the id-ordered LIMIT still runs."""
    definition = _defn(_step("Prüfung", AG3, due_hours=1, on_timeout="reject"))
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            now = _now()
            poison = await _request(session, definition, approval_key="poison")
            healthy = await _request(session, definition, approval_key="healthy")
            for aid in (poison, healthy):
                await _overdue_by(session, aid, now)
            real = sweep._overdue  # noqa: SLF001

            async def flaky(
                db: AsyncSession, approval: TiqoraApproval, *args: Any, **kwargs: Any
            ) -> str | None:
                if approval.id == poison:
                    await db.execute(text("UPDATE tiqora_approval SET title = 'half done'"))
                    raise RuntimeError("resolver plugin exploded")
                return await real(db, approval, *args, **kwargs)

            monkeypatch.setattr(sweep, "_overdue", flaky)
            monkeypatch.setattr(sweep, "_BATCH", 1)  # one candidate per page

            assert await _tick(session, now) == {**ZERO, "timeout_decided": 1}

            assert (await _row(session, poison)).status == "pending"  # rolled back, untouched
            assert (await _row(session, poison)).title == "Freigabe appr97"
            assert (await _row(session, healthy)).status == "rejected"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_invalid_scan_pages_through_all_requests(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            ids = [
                await _request(session, _defn(_step("Prüfung", AG3)), approval_key=f"k{n}")
                for n in range(3)
            ]
            await session.execute(text("UPDATE users SET valid_id = 2 WHERE id = :u"), {"u": AG3})
            await session.commit()
            monkeypatch.setattr(sweep, "_SCAN", 2)

            assert await _tick(session, _now()) == {**ZERO, "removed": 3}

            for aid in ids:
                assert (await _row(session, aid)).status == "rejected"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_invalid_scan_resumes_across_ticks(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With one tiny scan page per tick the cursor carries the scan to rows beyond it."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            ids = [
                await _request(session, _defn(_step("Prüfung", AG3)), approval_key=f"c{n}")
                for n in range(5)
            ]
            await session.execute(text("UPDATE users SET valid_id = 2 WHERE id = :u"), {"u": AG3})
            await session.commit()
            monkeypatch.setattr(sweep, "_SCAN", 2)
            monkeypatch.setattr(sweep, "_SCAN_PAGES", 1)
            monkeypatch.setattr(sweep, "_MAX_PAGES", 1)

            removed = [(await _tick(session, _now()))["removed"] for _ in range(8)]

            assert removed[0] < 5  # the first tick stopped at its page limit
            assert sum(removed) == 5
            for aid in ids:
                assert (await _row(session, aid)).status == "rejected"
            cursor = (
                await session.execute(
                    select(TiqoraSettings.value).where(
                        TiqoraSettings.key == sweep.KEY_INVALID_CURSOR
                    )
                )
            ).scalar_one()
            assert cursor == "0"  # wrapped at the end of the table


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_timeout_history_line(url_fixture: str, request: pytest.FixtureRequest) -> None:
    """F12: the timeout decision shows up as plain text in the Znuny ticket history."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(
                session, _defn(_step("Prüfung", AG3, due_hours=1, on_timeout="reject"))
            )
            now = _now()
            await _overdue_by(session, aid, now)
            await _tick(session, now)
            rows = await session.execute(
                text(
                    "SELECT name FROM ticket_history WHERE ticket_id = :t"
                    " AND name LIKE 'Approval #%' ORDER BY id"
                ),
                {"t": T1},
            )
            lines = [str(r[0]) for r in rows]
            assert lines == [
                f"Approval #{aid} (default): requested",
                f"Approval #{aid}: decided rejected (timeout)",
            ]
            assert render_history_entry(history_type="Misc", name=lines[-1]) == lines[-1]
