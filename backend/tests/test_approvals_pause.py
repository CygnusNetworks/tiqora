"""Pauses of the approval timers, and requests frozen with their module (both dialects).

A deadline that passes while approvals are off, or while a subject type's module is off, does not
fire on switching on: the first running tick moves the timers by the pause.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests import test_approvals_admin_api as admin_kit
from tests import test_approvals_links_api as links_kit
from tests._approvals_helpers import (
    AG3,
    T1,
    CapturingMail,
    FakeSubjectType,
    approvals_session,
    as_client,
    installed_registry,
    make_fake_subject_type,
)
from tests._fake_redis import FakeRedis
from tests.approvals_consumer_kit import run_hook_now, shared_session_factory
from tests.test_approvals_switch import SETTINGS, SUBJECT, _defn, _request, _status, _sweep, _switch
from tiqora.approvals import inbox, service, sweep
from tiqora.approvals.contract import JOB_MAIL, Actor, ApprovalError
from tiqora.approvals.notify import ApprovalMailPayload, run_approval_mail
from tiqora.domain.settings_store import get_setting, set_setting
from tiqora.jobs.context import JobContext
from tiqora.jobs.service import utc_now
from tiqora.security.ratelimit import ApprovalLinkRateLimiter
from tiqora.worker import approvals_sweep as worker_module

pytestmark = pytest.mark.db

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
BASE = "/api/v1/approvals"
UNAVAILABLE = {"detail": {"code": "subject_unavailable", "detail": None}}
MINUTE = timedelta(minutes=1)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


async def _step(session: AsyncSession, approval_id: int) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "SELECT due_at, last_reminder_at, reminder_count, activated_at"
                " FROM tiqora_approval_step WHERE approval_id = :a"
            ),
            {"a": approval_id},
        )
    ).one()
    return dict(row._mapping)


async def _set_due(session: AsyncSession, approval_id: int, due: datetime) -> None:
    await session.execute(
        text("UPDATE tiqora_approval_step SET due_at = :d WHERE approval_id = :a"),
        {"d": due, "a": approval_id},
    )
    await session.commit()


def _whole(moment: datetime) -> datetime:
    return moment.replace(microsecond=0)


async def _pause(session: AsyncSession, start: datetime, length: timedelta) -> None:
    """A running tick at *start*, a paused one half way through, then on again."""
    await _sweep(session, start)
    await _switch(session, False)
    await _sweep(session, start + length / 2)
    await _switch(session, True)


@pytest.mark.parametrize("url_fixture", URLS)
@pytest.mark.parametrize("on_timeout", ["reject", "approve"])
async def test_a_deadline_passing_in_the_pause_does_not_fire(
    url_fixture: str, request: pytest.FixtureRequest, on_timeout: str
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            start = _whole(utc_now())
            approval_id = await _request(session, due_hours=1, on_timeout=on_timeout)
            await _set_due(session, approval_id, start + HOUR)
            await _pause(session, start, 2 * DAY)

            back = start + 2 * DAY
            counts = await _sweep(session, back)
            assert counts["timeout_decided"] == 0
            assert await _status(session, approval_id) == "pending"
            assert (await _step(session, approval_id))["due_at"] == start + HOUR + 2 * DAY

            # the rest of its time runs from there, and then it does fire
            assert (await _sweep(session, back + HOUR - MINUTE))["timeout_decided"] == 0
            assert (await _sweep(session, back + HOUR + MINUTE))["timeout_decided"] == 1
            assert await _status(session, approval_id) != "pending"


@pytest.mark.parametrize("url_fixture", URLS)
async def test_a_deadline_due_before_the_pause_still_fires(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """It was due while people could decide, and only a tick stood between it and its firing."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            start = _whole(utc_now())
            approval_id = await _request(session, due_hours=1, on_timeout="reject")
            await _set_due(session, approval_id, start - HOUR)
            await _switch(session, False)
            assert (await _sweep(session, start))["paused"] == 1
            await _switch(session, True)
            assert (await _sweep(session, start + MINUTE))["timeout_decided"] == 1


@pytest.mark.parametrize("url_fixture", URLS)
async def test_a_reminder_keeps_its_rhythm_across_the_pause(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _switch(session, True, reminder_interval_hours=24, reminder_max=3)
            start = _whole(utc_now())
            approval_id = await _request(session)
            await _pause(session, start, 3 * DAY)

            back = start + 3 * DAY
            assert (await _sweep(session, back))["reminded"] == 0
            assert (await _sweep(session, back + 23 * HOUR))["reminded"] == 0
            assert (await _sweep(session, back + 25 * HOUR))["reminded"] == 1
            assert (await _step(session, approval_id))["reminder_count"] == 1


@pytest.mark.parametrize("url_fixture", URLS)
async def test_a_link_expiring_in_the_pause_lives_on(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            start = _whole(utc_now())
            approval_id, _ = await links_kit._link(session)
            params = {"a": approval_id, "e": start + HOUR}
            await session.execute(
                text("UPDATE tiqora_approval_token SET expires_at = :e WHERE approval_id = :a"),
                params,
            )
            await session.commit()
            await _pause(session, start, 2 * DAY)
            await _sweep(session, start + 2 * DAY)
            expires = (
                await session.execute(
                    text("SELECT expires_at FROM tiqora_approval_token WHERE approval_id = :a"),
                    {"a": approval_id},
                )
            ).scalar_one()
            assert expires == start + HOUR + 2 * DAY


@pytest.mark.parametrize("url_fixture", URLS)
async def test_off_and_on_between_two_ticks_moves_nothing(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            start = _whole(utc_now())
            approval_id = await _request(session, due_hours=1, on_timeout="reject")
            await _set_due(session, approval_id, start + HOUR)
            await _sweep(session, start)
            before = await _step(session, approval_id)
            await _switch(session, False)
            await _switch(session, True)
            counts = await _sweep(session, start + MINUTE)
            assert await _step(session, approval_id) == before
            assert (counts["timeout_decided"], counts["reminded"], counts["overdue"]) == (0, 0, 0)


@pytest.mark.parametrize("url_fixture", URLS)
async def test_off_and_on_seen_by_one_tick_is_not_noticeable(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """A tick caught it: the timers move by two tick intervals at most, and nothing goes out."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _switch(session, True, reminder_interval_hours=1, reminder_max=3)
            start = _whole(utc_now())
            approval_id = await _request(session, due_hours=1, on_timeout="reject")
            due = start + 30 * MINUTE
            await _set_due(session, approval_id, due)
            await _pause(session, start, 2 * MINUTE)
            counts = await _sweep(session, start + 2 * MINUTE)
            step = await _step(session, approval_id)
            assert step["due_at"] == due + 2 * MINUTE
            assert (counts["timeout_decided"], counts["reminded"], counts["overdue"]) == (0, 0, 0)
            assert step["reminder_count"] == 0


@pytest.mark.parametrize("url_fixture", URLS)
async def test_a_crash_before_the_commit_shifts_once(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            start = _whole(utc_now())
            approval_id = await _request(session, due_hours=1, on_timeout="reject")
            due = start + HOUR
            await _set_due(session, approval_id, due)
            await _pause(session, start, 2 * DAY)
            saved = await get_setting(session, sweep.KEY_TIMERS)
            await session.commit()

            real = sweep._shift_timers

            async def crash(*args: Any, **kwargs: Any) -> int:
                await real(*args, **kwargs)  # the update is out, the commit is not
                raise RuntimeError("the process dies here")

            monkeypatch.setattr(sweep, "_shift_timers", crash)
            with pytest.raises(RuntimeError):
                await _sweep(session, start + 2 * DAY)
            await session.rollback()
            monkeypatch.setattr(sweep, "_shift_timers", real)
            # nothing happened: not moved, not fired, and the pause is still on record
            assert (await _step(session, approval_id))["due_at"] == due
            assert await _status(session, approval_id) == "pending"
            assert await get_setting(session, sweep.KEY_TIMERS) == saved
            await session.commit()

            # the next tick is later, so it moves by the whole pause up to then, once
            later = start + 2 * DAY + MINUTE
            assert (await _sweep(session, later))["timeout_decided"] == 0
            moved = due + 2 * DAY + MINUTE
            assert (await _step(session, approval_id))["due_at"] == moved
            await _sweep(session, later + MINUTE)
            assert (await _step(session, approval_id))["due_at"] == moved


@pytest.mark.parametrize("url_fixture", URLS)
async def test_a_step_activated_after_switching_on_keeps_its_deadline(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Asked between switching on and the first tick: it was never paused."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            start = _whole(utc_now())
            old = await _request(session, due_hours=1)
            await _pause(session, start, 2 * DAY)
            info = await service.request_approval(
                session,
                ticket_id=T1,
                subject_type=SUBJECT,
                title="Freigabe appr97",
                actor=Actor.system(),
                definition=_defn(due_hours=48),
                approval_key="second",
                settings=SETTINGS,
            )
            new = info.approval_id
            await session.execute(
                text("UPDATE tiqora_approval_step SET activated_at = :t WHERE approval_id = :a"),
                {"t": start + 2 * DAY - MINUTE, "a": new},
            )
            await session.commit()
            old_due = (await _step(session, old))["due_at"]
            new_due = (await _step(session, new))["due_at"]
            await _sweep(session, start + 2 * DAY)
            assert (await _step(session, old))["due_at"] == old_due + 2 * DAY
            assert (await _step(session, new))["due_at"] == new_due


# --- the daemon flag ---------------------------------------------------------------------------


async def _flag(session: AsyncSession, on: bool) -> None:
    await set_setting(session, "daemon.approvals.enabled", "1" if on else "0")


@pytest.fixture
def worker_tick(monkeypatch: pytest.MonkeyPatch) -> Any:
    """``tick(session, now)``: the worker's tick on *session*, the daemon flag deciding."""

    async def tick(session: AsyncSession, now: datetime) -> dict[str, int]:
        await session.commit()
        monkeypatch.setattr(
            worker_module, "get_session_factory", lambda: shared_session_factory(session)
        )
        monkeypatch.setattr(worker_module, "get_pubsub_redis", lambda settings=None: None)
        return await worker_module.run_approvals_sweep_worker_tick(SETTINGS, now=now)

    return tick


@pytest.mark.parametrize("url_fixture", URLS)
@pytest.mark.parametrize("on_timeout", ["reject", "approve"])
async def test_a_pause_only_ticks_with_the_daemon_off_saw_does_not_fire(
    url_fixture: str, request: pytest.FixtureRequest, worker_tick: Any, on_timeout: str
) -> None:
    """Admin turns off service and module, later on again: the first real sweep decides nothing."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            start = _whole(utc_now())
            approval_id = await _request(session, due_hours=1, on_timeout=on_timeout)
            await _set_due(session, approval_id, start + HOUR)
            await _flag(session, False)
            assert await worker_tick(session, start) == {"skipped": 1}
            await _switch(session, False)
            assert await worker_tick(session, start + DAY) == {"skipped": 1}
            await _switch(session, True)
            await _flag(session, True)
            counts = await worker_tick(session, start + 2 * DAY)
            assert counts["timeout_decided"] == 0
            assert await _status(session, approval_id) == "pending"
            assert (await _step(session, approval_id))["due_at"] == start + HOUR + 2 * DAY


@pytest.mark.parametrize("url_fixture", URLS)
async def test_a_month_with_the_daemon_off_does_not_count_into_a_short_pause(
    url_fixture: str, request: pytest.FixtureRequest, worker_tick: Any
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            start = _whole(utc_now())
            approval_id = await _request(session, due_hours=1)
            due = start + 40 * DAY
            await _set_due(session, approval_id, due)
            await worker_tick(session, start)
            await _flag(session, False)
            month = start + 30 * DAY
            await worker_tick(session, month)
            await _switch(session, False)
            await _flag(session, True)
            assert (await worker_tick(session, month + 30 * MINUTE))["paused"] == 1
            await _switch(session, True)
            await worker_tick(session, month + HOUR)
            assert (await _step(session, approval_id))["due_at"] == due + HOUR


@pytest.mark.parametrize("url_fixture", URLS)
async def test_a_pause_ending_with_the_daemon_off_shifts_once(
    url_fixture: str, request: pytest.FixtureRequest, worker_tick: Any
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            start = _whole(utc_now())
            approval_id = await _request(session, due_hours=1, on_timeout="reject")
            due = start + HOUR
            await _set_due(session, approval_id, due)
            await _flag(session, False)
            await worker_tick(session, start)
            await _switch(session, False)
            await worker_tick(session, start + DAY)
            await _switch(session, True)
            await worker_tick(session, start + 2 * DAY)
            assert (await _step(session, approval_id))["due_at"] == due + 2 * DAY
            await _flag(session, True)
            assert (await worker_tick(session, start + 2 * DAY + MINUTE))["timeout_decided"] == 0
            assert (await _step(session, approval_id))["due_at"] == due + 2 * DAY


@pytest.mark.parametrize("url_fixture", URLS)
async def test_a_deadline_passing_with_the_daemon_off_and_the_module_on_still_fires(
    url_fixture: str, request: pytest.FixtureRequest, worker_tick: Any
) -> None:
    """People could decide the whole time: the service being off moves nothing, as before."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            start = _whole(utc_now())
            approval_id = await _request(session, due_hours=1, on_timeout="reject")
            await _set_due(session, approval_id, start + HOUR)
            await _flag(session, False)
            await worker_tick(session, start + 2 * HOUR)
            await _flag(session, True)
            assert (await worker_tick(session, start + 3 * HOUR))["timeout_decided"] == 1
            assert await _status(session, approval_id) == "rejected"


@pytest.mark.parametrize("url_fixture", URLS)
async def test_a_link_issued_after_switching_on_keeps_its_time(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            start = _whole(utc_now())
            approval_id, _ = await links_kit._link(session)
            await _pause(session, start, 2 * DAY)
            fresh = start + 2 * DAY - MINUTE
            expires = fresh + 7 * DAY
            await session.execute(
                text(
                    "UPDATE tiqora_approval_token SET created_at = :c, expires_at = :e"
                    " WHERE approval_id = :a"
                ),
                {"c": fresh, "e": expires, "a": approval_id},
            )
            await session.commit()
            await _sweep(session, start + 2 * DAY)
            got = (
                await session.execute(
                    text("SELECT expires_at FROM tiqora_approval_token WHERE approval_id = :a"),
                    {"a": approval_id},
                )
            ).scalar_one()
            assert got == expires


# --- frozen with the module of the subject type -----------------------------------------------


def _switchable() -> tuple[FakeSubjectType, Callable[[bool], None]]:
    """A subject type whose module can be switched, the way ``itsm.change`` follows its switch."""
    fake = make_fake_subject_type()
    state = {"on": True}

    async def available(session: AsyncSession) -> bool:
        return state["on"]

    fake.item = replace(fake.item, available=available)

    def flip(on: bool) -> None:
        state["on"] = on

    return fake, flip


@pytest.mark.parametrize("url_fixture", URLS)
async def test_a_frozen_request_is_out_of_sight_and_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        fake, flip = _switchable()
        with installed_registry(fake):
            await admin_kit._seed_admin(session)
            approval_id = await _request(session)
            actor = Actor.agent(AG3)
            assert await inbox.pending_count(session, actor) == 1
            flip(False)
            await session.commit()
            assert await inbox.pending_count(session, actor) == 0
            page = await inbox.inbox(session, actor, state="pending", limit=10, offset=0)
            assert page.total == 0
            await session.rollback()

            with pytest.raises(ApprovalError) as refused:
                await service.decide(
                    session, approval_id, actor=actor, decision="approved", channel="ui"
                )
            assert refused.value.code == "subject_unavailable"
            await session.rollback()

            async with await as_client(session, agent=AG3) as client:
                got = await client.get(f"{BASE}/{approval_id}")
                assert (got.status_code, got.json()) == (409, UNAVAILABLE)
                got = await client.post(
                    f"{BASE}/{approval_id}/decision", json={"decision": "approved"}
                )
                assert got.status_code == 409
                assert got.json()["detail"]["code"] == "subject_unavailable"
            async with await as_client(session, agent=admin_kit.ADMIN) as admin:
                listed = await admin.get(f"/api/v1/tickets/{T1}/approvals")
                assert listed.status_code == 200, listed.text
                assert listed.json() == []
            assert await _status(session, approval_id) == "pending"

            flip(True)
            await session.commit()
            assert await inbox.pending_count(session, actor) == 1


@pytest.mark.parametrize("url_fixture", URLS)
async def test_a_frozen_requests_link_is_refused_and_counts_nothing(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        fake, flip = _switchable()
        with installed_registry(fake):
            approval_id, token = await links_kit._link(session)
            flip(False)
            redis = FakeRedis()
            async with links_kit._client(session, redis) as client:
                for path, extra in (("inspect", {}), ("decide", {"decision": "approved"})):
                    got = await client.post(
                        f"{links_kit.BASE}/{path}", json={"token": token, **extra}
                    )
                    assert got.status_code == 409, got.text
                    assert got.json()["detail"]["code"] == "subject_unavailable"
            limiter = ApprovalLinkRateLimiter(redis, links_kit.SETTINGS)
            assert (await limiter.check_ip(links_kit.IP_A)).allowed
            assert await _status(session, approval_id) == "pending"

            flip(True)  # not spent: the refused vote left the link alive
            async with links_kit._client(session, redis) as client:
                got = await client.post(f"{links_kit.BASE}/inspect", json={"token": token})
                assert (got.status_code, got.json()["valid"]) == (200, True)


@pytest.mark.parametrize("url_fixture", URLS)
async def test_a_frozen_request_pauses_while_the_others_run(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        fake, flip = _switchable()
        plain = make_fake_subject_type("test.plain")
        with installed_registry(fake, plain):
            start = _whole(utc_now())
            frozen = await _request(session, due_hours=1, on_timeout="reject")
            info = await service.request_approval(
                session,
                ticket_id=T1,
                subject_type="test.plain",
                title="Freigabe appr97",
                actor=Actor.system(),
                definition=_defn(due_hours=1, on_timeout="reject"),
                approval_key="other",
                settings=SETTINGS,
            )
            await session.commit()
            other = info.approval_id
            await _set_due(session, frozen, start + HOUR)
            await _set_due(session, other, start + HOUR)
            await _sweep(session, start)
            flip(False)
            counts = await _sweep(session, start + 2 * HOUR)
            assert counts["timeout_decided"] == 1 and "paused" not in counts
            assert await _status(session, other) == "rejected"
            assert await _status(session, frozen) == "pending"

            flip(True)
            back = start + DAY
            assert (await _sweep(session, back))["timeout_decided"] == 0
            assert await _status(session, frozen) == "pending"
            assert (await _step(session, frozen))["due_at"] == start + HOUR + DAY


@pytest.mark.parametrize("url_fixture", URLS)
async def test_pauses_of_both_switches_count_once(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Module off, then approvals off, approvals on, module on: one pause for the frozen type."""
    async with approvals_session(request, url_fixture) as session:
        fake, flip = _switchable()
        with installed_registry(fake):
            start = _whole(utc_now())
            approval_id = await _request(session, due_hours=1)
            due = (await _step(session, approval_id))["due_at"]
            await _sweep(session, start)
            flip(False)
            await _sweep(session, start + HOUR)
            await _switch(session, False)
            await _sweep(session, start + 2 * HOUR)
            await _switch(session, True)
            await _sweep(session, start + 3 * HOUR)
            assert (await _step(session, approval_id))["due_at"] == due
            flip(True)
            await _sweep(session, start + 4 * HOUR)
            assert (await _step(session, approval_id))["due_at"] == due + 4 * HOUR


@pytest.mark.parametrize("url_fixture", URLS)
async def test_no_mail_for_a_frozen_request_and_its_hook_still_runs(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        fake, flip = _switchable()
        with installed_registry(fake):
            approval_id = await _request(session)
            await service.decide(
                session, approval_id, actor=Actor.agent(AG3), decision="approved", channel="ui"
            )
            await session.commit()
            flip(False)
            assert await _status(session, approval_id) == "approved"
            result = await run_hook_now(shared_session_factory(session), approval_id, "approved")
            assert result["hook"] == "called"
            assert [o.approval_id for o in fake.decided] == [approval_id]

            flip(True)
            pending = await _request(session)
            flip(False)
            ctx = JobContext(
                job_id=0,
                kind=JOB_MAIL,
                created_by=None,
                settings=SETTINGS,
                session_factory=shared_session_factory(session),  # type: ignore[arg-type]
                attempt=1,
                max_attempts=3,
            )
            got = await run_approval_mail(
                ctx,
                ApprovalMailPayload(
                    approval_id=pending,
                    kind="request",
                    approver_ids=[],
                    include_requester=False,
                    include_substitutes=False,
                ),
            )
            assert got is not None
            assert got["note"] == "subject_unavailable"
            assert mail.sender.sent == []


@pytest.mark.parametrize("url_fixture", URLS)
async def test_admin_can_read_pickers_and_detail_but_not_cancel(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        fake, flip = _switchable()
        with installed_registry(fake):
            await admin_kit._seed_admin(session)
            approval_id = await _request(session)
            await session.commit()

            async def row(client: Any) -> dict[str, Any]:
                got = await client.get(admin_kit.BASE)
                assert got.status_code == 200, got.text
                return next(r for r in got.json()["items"] if r["id"] == approval_id)

            async with await as_client(session, agent=admin_kit.ADMIN) as admin:
                assert (await row(admin))["can_cancel"] is True
                flip(False)  # frozen with its module
                assert (await row(admin))["can_cancel"] is False
                assert (await admin.get(f"{BASE}/{approval_id}")).status_code == 200
                flip(True)
                await _switch(session, False)  # approvals off
                assert (await row(admin))["can_cancel"] is False
                for path in (f"{BASE}/lists", f"{BASE}/resolvers", f"{BASE}/{approval_id}"):
                    got = await admin.get(path)
                    assert got.status_code == 200, (path, got.text)
                assert (await admin.get(f"{BASE}/inbox")).status_code == 404
                got = await admin.post(f"{BASE}/{approval_id}/cancel", json={})
                assert got.status_code == 404
            async with await as_client(session, agent=AG3) as agent:
                for path in (f"{BASE}/lists", f"{BASE}/resolvers", f"{BASE}/{approval_id}"):
                    got = await agent.get(path)
                    assert got.status_code == 404, (path, got.text)
                    assert got.json()["detail"]["code"] == "approvals_disabled"
