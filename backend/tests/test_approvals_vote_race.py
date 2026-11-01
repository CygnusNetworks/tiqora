"""Votes at the same moment, and a vote that loses to a deadlock (both dialects).

Two votes on different requests must not deadlock on MariaDB; a vote forced into a deadlock is run
again as a whole, with no double effects, on every path that decides.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import pytest
from fastmcp import Client
from sqlalchemy import delete, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests import test_approvals_links_api as links_kit
from tests import test_approvals_mcp as mcp_kit
from tests import test_approvals_portal_api as portal_kit
from tests._approvals_helpers import (
    AG1,
    AG3,
    AG4,
    AG5,
    AG7,
    T1,
    T2,
    approvals_session,
    as_client,
    installed_registry,
    make_fake_subject_type,
)
from tests._fake_redis import FakeRedis
from tests.test_approvals_sweep import SETTINGS, _committed_world, _defn, _request, _step
from tiqora.approvals import _engine, delegation, service
from tiqora.approvals.contract import JOB_HOOK, JOB_MAIL, Actor, ApprovalError
from tiqora.approvals.models import (
    TiqoraApproval,
    TiqoraApprovalApprover,
    TiqoraApprovalDelegation,
    TiqoraApprovalVote,
)
from tiqora.approvals.retry import commit_with_retry
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.jobs.service import utc_now
from tiqora.mcp_server.server import mcp

pytestmark = pytest.mark.db

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
ROUNDS = 15
BASE = "/api/v1/approvals"


async def _plain_vote(
    factory: async_sessionmaker[AsyncSession], approval_id: int, user_id: int
) -> str:
    """One vote without the retry, the way a route's transaction runs: 'ok' or what went wrong."""
    async with factory() as session:
        await session.rollback()
        try:
            await service.decide(
                session,
                approval_id,
                actor=Actor.agent(user_id),
                decision="approved",
                channel="api",
                settings=SETTINGS,
            )
            await session.commit()
        except ApprovalError as err:
            await session.rollback()
            return err.code
        except OperationalError as exc:
            await session.rollback()
            return f"db_error:{exc.orig.args[0] if exc.orig else None}"
    return "ok"


async def _status(factory: async_sessionmaker[AsyncSession], approval_id: int) -> str:
    async with factory() as session:
        return str(
            (
                await session.execute(
                    select(TiqoraApproval.status).where(TiqoraApproval.id == approval_id)
                )
            ).scalar_one()
        )


async def _votes(factory: async_sessionmaker[AsyncSession], *approval_ids: int) -> int:
    async with factory() as session:
        rows = await session.execute(
            select(TiqoraApprovalVote.id).where(TiqoraApprovalVote.approval_id.in_(approval_ids))
        )
        return len(rows.all())


@pytest.mark.parametrize("url_fixture", URLS)
@pytest.mark.parametrize("tickets", [(T1, T2), (T1, T1)], ids=["two_tickets", "one_ticket"])
@pytest.mark.parametrize("rule", ["all", "any"])
async def test_votes_on_different_requests_do_not_deadlock(
    url_fixture: str, request: pytest.FixtureRequest, tickets: tuple[int, int], rule: str
) -> None:
    """``all`` stays open after the vote, ``any`` closes the request (more rows, more jobs)."""
    async with _committed_world(request.getfixturevalue(url_fixture)) as factory:
        with installed_registry(make_fake_subject_type()):
            for n in range(ROUNDS):
                ids: list[int] = []
                async with factory() as setup:
                    for k, ticket in enumerate(tickets):
                        ids.append(
                            await _request(
                                setup,
                                _defn(_step("CAB", AG3, AG4, rule=rule)),
                                ticket_id=ticket,
                                approval_key=f"race-{n}-{k}",
                            )
                        )
                got = await asyncio.gather(
                    _plain_vote(factory, ids[0], AG3), _plain_vote(factory, ids[1], AG4)
                )
                assert got == ["ok", "ok"], (n, got)
                assert await _votes(factory, *ids) == 2


@pytest.mark.parametrize("url_fixture", URLS)
async def test_votes_on_one_request_see_each_other(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Without the locking read: whoever gets the request lock second must see the first vote,
    or an ``all`` step stays open for ever."""
    async with _committed_world(request.getfixturevalue(url_fixture)) as factory:
        with installed_registry(make_fake_subject_type()):
            for n in range(ROUNDS):
                async with factory() as setup:
                    aid = await _request(
                        setup,
                        _defn(_step("CAB", AG3, AG4, rule="all")),
                        approval_key=f"same-{n}",
                    )
                got = await asyncio.gather(
                    _plain_vote(factory, aid, AG3), _plain_vote(factory, aid, AG4)
                )
                assert got == ["ok", "ok"]
                assert await _status(factory, aid) == "approved"
                assert await _votes(factory, aid) == 2


@asynccontextmanager
async def _substitutes(
    factory: async_sessionmaker[AsyncSession], by: int, *of: int
) -> AsyncIterator[None]:
    """Standing delegations to *by*, removed again: the world's cleanup does not know them."""
    async with factory() as setup:
        for user in of:
            await _substitute(setup, user, by)
    try:
        yield
    finally:
        async with factory() as cleanup:
            await cleanup.execute(
                delete(TiqoraApprovalDelegation).where(
                    TiqoraApprovalDelegation.substitute_user_id == by
                )
            )
            await cleanup.commit()


async def _substitute(session: AsyncSession, of: int, by: int) -> None:
    await delegation.create_delegation(
        session,
        user_id=of,
        substitute_user_id=by,
        valid_from=utc_now() - timedelta(hours=1),
        valid_until=None,
        created_by=of,
    )
    await session.commit()


@pytest.mark.parametrize("url_fixture", URLS)
async def test_one_substitute_voting_on_two_rows_votes_once(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """F8 reads the votes after the request lock: the second of two simultaneous votes of one
    person must see the first, whatever the rule counts. A stale vote read gives two votes."""
    async with _committed_world(request.getfixturevalue(url_fixture)) as factory:
        with installed_registry(make_fake_subject_type()):
            async with _substitutes(factory, AG5, AG3, AG4):
                for n in range(ROUNDS):
                    async with factory() as setup:
                        aid = await _request(
                            setup,
                            _defn(_step("CAB", AG3, AG4, AG7, rule="all")),
                            approval_key=f"sub-{n}",
                        )
                    got = await asyncio.gather(
                        _plain_vote(factory, aid, AG5), _plain_vote(factory, aid, AG5)
                    )
                    assert sorted(got) == ["already_decided", "ok"], (n, got)
                    assert await _votes(factory, aid) == 1


@pytest.mark.parametrize("url_fixture", URLS)
async def test_forward_waiting_for_a_vote_sees_it(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """The target votes as a substitute while someone forwards to them. The forward must see the
    vote it waited for, or the target holds a row F8 refuses and an ``all`` step can never end."""
    async with _committed_world(request.getfixturevalue(url_fixture)) as factory:
        with installed_registry(make_fake_subject_type()):
            async with _substitutes(factory, AG5, AG3):
                for n in range(5):
                    async with factory() as setup:
                        aid = await _request(
                            setup,
                            _defn(_step("CAB", AG3, AG4, rule="all")),
                            approval_key=f"fwd-{n}",
                        )
                        row = (
                            await setup.execute(
                                select(TiqoraApprovalApprover.id).where(
                                    TiqoraApprovalApprover.approval_id == aid,
                                    TiqoraApprovalApprover.user_id == AG4,
                                )
                            )
                        ).scalar_one()
                        await setup.rollback()

                    async def forward(approver_id: int = row, approval_id: int = aid) -> str:
                        async with factory() as session:
                            await session.rollback()  # the route's path
                            try:
                                await service.forward(
                                    session,
                                    approval_id,
                                    approver_id,
                                    actor=Actor.agent(AG4),
                                    to_user_id=AG5,
                                )
                                await session.commit()
                            except ApprovalError as err:
                                await session.rollback()
                                return err.code
                        return "ok"

                    async with factory() as voter:
                        await voter.rollback()
                        await service.decide(
                            voter,
                            aid,
                            actor=Actor.agent(AG5),
                            decision="approved",
                            channel="api",
                            settings=SETTINGS,
                        )
                        waiting = asyncio.create_task(forward())
                        await asyncio.sleep(0.5)
                        assert not waiting.done()
                        await voter.commit()
                    assert await asyncio.wait_for(waiting, 30) == "forward_target_invalid"
                    assert await _status(factory, aid) == "pending"
                    # AG4 still holds their row, so the step can end
                    got = await _plain_vote(factory, aid, AG4)
                    assert got == "ok"
                    assert await _status(factory, aid) == "approved"


# --- a forced deadlock ------------------------------------------------------------------------


class _Mysql1213(Exception):
    def __init__(self) -> None:
        super().__init__(1213, "Deadlock found when trying to get lock")


def _deadlock() -> OperationalError:
    return OperationalError("INSERT INTO tiqora_approval_vote", {}, _Mysql1213())


@pytest.fixture
def forced(monkeypatch: pytest.MonkeyPatch) -> Callable[[int | None], list[int]]:
    """``forced(n)``: the next *n* votes (None: all) die after they wrote everything but the
    commit; returns a list that grows by one per attempt."""

    def install(times: int | None) -> list[int]:
        attempts: list[int] = []
        real = _engine.close_step

        async def close_then_die(*args: Any, **kwargs: Any) -> None:
            await real(*args, **kwargs)
            attempts.append(1)
            if times is None or len(attempts) <= times:
                raise _deadlock()

        monkeypatch.setattr(_engine, "close_step", close_then_die)
        return attempts

    return install


async def _effects(session: AsyncSession, approval_id: int) -> dict[str, int]:
    one = {"a": approval_id}

    async def count(sql: str) -> int:
        return int((await session.execute(text(sql), one)).scalar_one())

    hooks = (
        await session.execute(
            select(TiqoraJob.id).where(
                TiqoraJob.kind == JOB_HOOK, TiqoraJob.dedupe_key.like(f"approval:{approval_id}:%")
            )
        )
    ).all()
    return {
        "votes": await count("SELECT COUNT(*) FROM tiqora_approval_vote WHERE approval_id = :a"),
        "voted": await count(
            "SELECT COUNT(*) FROM tiqora_approval_event WHERE approval_id = :a AND action = 'voted'"
        ),
        "decided": await count(
            "SELECT COUNT(*) FROM tiqora_approval_event WHERE approval_id = :a"
            " AND action = 'step_decided'"
        ),
        "hooks": len(hooks),
    }


ONCE = {"votes": 1, "voted": 1, "decided": 1, "hooks": 1}
NONE = {"votes": 0, "voted": 0, "decided": 0, "hooks": 0}


async def _ask(session: AsyncSession, *users: int) -> int:
    info = await service.request_approval(
        session,
        ticket_id=T1,
        subject_type="test.subject",
        title="Freigabe appr97",
        actor=Actor.agent(AG1),
        definition=_defn(_step("Prüfung", *(users or (AG3,)))),
        settings=SETTINGS,
    )
    await session.commit()
    return info.approval_id


@pytest.mark.parametrize("url_fixture", URLS)
async def test_agent_vote_retries_with_no_double_effects(
    url_fixture: str, request: pytest.FixtureRequest, forced: Any
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _ask(session)
            attempts = forced(2)
            async with await as_client(session, agent=AG3) as client:
                got = await client.post(f"{BASE}/{aid}/decision", json={"decision": "approved"})
            assert got.status_code == 200, got.text
            assert got.json()["status"] == "approved"
            assert len(attempts) == 3
            assert await _effects(session, aid) == ONCE


async def _mail_jobs(session: AsyncSession) -> int:
    rows = await session.execute(select(TiqoraJob.id).where(TiqoraJob.kind == JOB_MAIL))
    return len(rows.all())


@pytest.mark.parametrize("url_fixture", URLS)
async def test_a_retried_vote_queues_the_same_mails_as_a_plain_one(
    url_fixture: str, request: pytest.FixtureRequest, forced: Any
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            async with await as_client(session, agent=AG3) as client:
                plain = await _ask(session)
                before = await _mail_jobs(session)
                got = await client.post(f"{BASE}/{plain}/decision", json={"decision": "approved"})
                assert got.status_code == 200, got.text
                queued = await _mail_jobs(session) - before
                assert queued > 0

                other = await _ask_other(session)
                before = await _mail_jobs(session)
                forced(2)
                got = await client.post(f"{BASE}/{other}/decision", json={"decision": "approved"})
                assert got.status_code == 200, got.text
                assert await _mail_jobs(session) - before == queued


async def _ask_other(session: AsyncSession) -> int:
    info = await service.request_approval(
        session,
        ticket_id=T2,
        subject_type="test.subject",
        title="Freigabe appr97",
        actor=Actor.agent(AG1),
        definition=_defn(_step("Prüfung", AG3)),
        settings=SETTINGS,
    )
    await session.commit()
    return info.approval_id


@pytest.mark.parametrize("url_fixture", URLS)
async def test_agent_vote_out_of_attempts_is_a_clear_retryable_error(
    url_fixture: str, request: pytest.FixtureRequest, forced: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _ask(session)
            real = _engine.close_step
            attempts = forced(None)
            async with await as_client(session, agent=AG3) as client:
                got = await client.post(f"{BASE}/{aid}/decision", json={"decision": "approved"})
                assert got.status_code == 503, got.text
                assert got.json()["detail"]["code"] == "approval_busy"
                assert len(attempts) == 4
                assert await _effects(session, aid) == NONE
                await session.commit()
                monkeypatch.setattr(_engine, "close_step", real)
                again = await client.post(f"{BASE}/{aid}/decision", json={"decision": "approved"})
            assert again.status_code == 200, again.text
            assert await _effects(session, aid) == ONCE


@pytest.mark.parametrize("url_fixture", URLS)
async def test_link_vote_retries_and_is_not_spent_by_a_lost_attempt(
    url_fixture: str, request: pytest.FixtureRequest, forced: Any
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid, token = await links_kit._link(session, AG3)
            attempts = forced(1)
            async with links_kit._client(session, FakeRedis()) as client:
                got = await client.post(
                    f"{links_kit.BASE}/decide", json={"token": token, "decision": "approved"}
                )
            assert got.status_code == 200, got.text
            assert len(attempts) == 2
            assert await _effects(session, aid) == ONCE


@pytest.mark.parametrize("url_fixture", URLS)
async def test_link_vote_out_of_attempts_keeps_the_link(
    url_fixture: str, request: pytest.FixtureRequest, forced: Any
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid, token = await links_kit._link(session, AG3)
            forced(None)
            async with links_kit._client(session, FakeRedis()) as client:
                got = await client.post(
                    f"{links_kit.BASE}/decide", json={"token": token, "decision": "approved"}
                )
                assert got.status_code == 503, got.text
                assert got.json()["detail"]["code"] == "approval_busy"
                used = (
                    await session.execute(
                        text("SELECT used_at FROM tiqora_approval_token WHERE approval_id = :a"),
                        {"a": aid},
                    )
                ).scalar_one()
            assert used is None
            assert await _effects(session, aid) == NONE


@pytest.mark.parametrize("url_fixture", URLS)
async def test_portal_vote_retries_with_no_double_effects(
    url_fixture: str, request: pytest.FixtureRequest, forced: Any
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await portal_kit._enable(session)
            aid = await portal_kit._request(
                session,
                portal_kit._defn(portal_kit._step("Prüfung", portal_kit._cust(portal_kit.K2))),
            )
            await session.execute(
                text("UPDATE ticket SET customer_id = :c WHERE id = :t"),
                {"c": portal_kit.FIRMA, "t": T1},
            )
            await session.commit()
            attempts = forced(1)
            async with portal_kit._client(session) as client:
                got = await client.post(
                    f"{portal_kit.BASE}/{aid}/decision", json={"decision": "approved"}
                )
            assert got.status_code == 200, got.text
            assert len(attempts) == 2
            assert await _effects(session, aid) == ONCE


@pytest.mark.parametrize("url_fixture", URLS)
async def test_mcp_vote_retries_and_says_when_out_of_attempts(
    url_fixture: str, request: pytest.FixtureRequest, forced: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await mcp_kit._enable(session)
            real = _engine.close_step
            aid = await mcp_kit._request(session, (AG3,))
            await session.commit()
            forced(None)
            args = {"approval_id": aid, "decision": "approved"}
            async with mcp_kit._as(session, AG3, mcp_kit.RW_DECIDE), Client(mcp) as client:
                busy = await client.call_tool("approval_decide", args)
                assert busy.data == {"error": "approval_busy"}
                assert await _effects(session, aid) == NONE
                await session.commit()
                monkeypatch.setattr(_engine, "close_step", real)
                attempts = forced(1)
                done = await client.call_tool("approval_decide", args)
            assert done.data["ok"] is True
            assert len(attempts) == 2
            assert await _effects(session, aid) == ONCE


@pytest.mark.parametrize("url_fixture", URLS)
async def test_forward_retries_with_no_double_effects(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _ask(session, AG3, AG4)
            row = (
                await session.execute(
                    select(TiqoraApprovalApprover.id).where(
                        TiqoraApprovalApprover.approval_id == aid,
                        TiqoraApprovalApprover.user_id == AG3,
                    )
                )
            ).scalar_one()
            await session.commit()
            attempts: list[int] = []
            real = _engine.add_approver

            async def add_then_die(*args: Any, **kwargs: Any) -> int:
                got = await real(*args, **kwargs)
                attempts.append(1)
                if len(attempts) == 1:
                    raise _deadlock()
                return got

            monkeypatch.setattr(_engine, "add_approver", add_then_die)
            async with await as_client(session, agent=AG3) as client:
                got = await client.post(
                    f"{BASE}/{aid}/approvers/{row}/forward", json={"user_id": AG5}
                )
            assert got.status_code == 200, got.text
            assert len(attempts) == 2
            forwards = await session.execute(
                text(
                    "SELECT COUNT(*) FROM tiqora_approval_event WHERE approval_id = :a"
                    " AND action = 'forwarded'"
                ),
                {"a": aid},
            )
            assert forwards.scalar_one() == 1
            rows = await session.execute(
                select(TiqoraApprovalApprover.id).where(
                    TiqoraApprovalApprover.approval_id == aid,
                    TiqoraApprovalApprover.user_id == AG5,
                )
            )
            assert len(rows.all()) == 1


async def test_other_database_errors_are_not_retried() -> None:
    calls: list[int] = []

    class _Session:
        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    async def attempt() -> None:
        calls.append(1)
        raise OperationalError("SELECT 1", {}, Exception(2006, "gone away"))

    with pytest.raises(OperationalError):
        await commit_with_retry(_Session(), attempt)  # type: ignore[arg-type]
    assert len(calls) == 1


async def test_refusals_are_not_retried() -> None:
    calls: list[int] = []

    class _Session:
        async def rollback(self) -> None:  # pragma: no cover
            raise AssertionError("a refusal is the caller's to roll back")

    async def attempt() -> None:
        calls.append(1)
        raise ApprovalError("approval_closed", 409)

    with pytest.raises(ApprovalError):
        await commit_with_retry(_Session(), attempt)  # type: ignore[arg-type]
    assert len(calls) == 1
