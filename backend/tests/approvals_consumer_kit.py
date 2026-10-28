"""Test kit for plans that consume the approval engine (itsm-change, catalogue, process).

Import these helpers instead of re-deriving how a consumer's approval round trip is driven in a
test::

    from tests.approvals_consumer_kit import approve_all, assert_subject_registered, run_hook_now

A typical consumer test registers its subject type (``installed_registry`` of
``tests._approvals_helpers``), calls ``service.request_approval`` inside its own transaction,
commits, lets the approvers decide (:func:`approve_all` / :func:`reject_first`, or
``service.decide`` for finer control), commits again and then runs the follow-up job the
deciding transaction queued with :func:`run_hook_now`. In production that job
(``core.approval_hook``) calls the subject type's ``on_decided`` after the deciding commit; the
tests call it directly so no worker is needed.

Everything here talks to the public service API only. The helpers never commit: ``approve_all``
and ``reject_first`` flush on the caller's session, :func:`run_hook_now` opens sessions through
the factory it is given and commits the way the job does. ``shared_session_factory`` adapts a
rolled-back test session (``approvals_session``) to the factory shape the job expects.

Lock order (contract): a consumer calls the approval service (``request_approval``,
``cancel_approval``, ``record_auto_decision``) **before** it writes the Znuny ``ticket`` row
(state, owner, lock, articles, history) in the same transaction. The engine itself locks
``tiqora_approval`` first and touches ``ticket`` afterwards (history lines insert child rows,
which InnoDB guards with a shared lock on the ticket), so a consumer that updates the ticket
first deadlocks (MariaDB 1213) with a concurrent vote. :func:`race_consumer_and_vote` checks a
consumer's transaction against a vote that arrives in between its two halves.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.approvals import service
from tiqora.approvals.contract import (
    APPROVAL_SUBJECT_TYPES,
    JOB_HOOK,
    Actor,
    ApprovalError,
    ApprovalInfo,
    ApprovalSubjectType,
    Principal,
)
from tiqora.approvals.hooks import MAX_ATTEMPTS, ApprovalHookPayload, run_approval_hook
from tiqora.approvals.models import TiqoraApprovalApprover, TiqoraApprovalStep
from tiqora.config import Settings
from tiqora.extensions.core import get_registry
from tiqora.jobs.context import JobContext
from tiqora.jobs.service import utc_now

_SETTINGS = Settings(environment="test")
_MAX_ROUNDS = 100  # more votes than any definition has approvers: guards a stuck loop


def assert_subject_registered(key: str) -> ApprovalSubjectType:
    """The registered subject type *key*; fails readably when the registry lacks it."""
    item = get_registry().get(APPROVAL_SUBJECT_TYPES, key)
    if item is None:
        known = sorted(t.key for t in get_registry().items(APPROVAL_SUBJECT_TYPES))
        raise AssertionError(f"approval subject type {key!r} is not registered (known: {known})")
    return item


def shared_session_factory(session: AsyncSession) -> Callable[[], Any]:
    """A session factory that always hands out *session* (and does not close it).

    For tests running inside ``approvals_session``, whose single connection transaction is
    rolled back at the end: the job's own commit/rollback then act on the session's savepoint.
    """

    @asynccontextmanager
    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    return _factory


async def run_hook_now(
    session_factory: async_sessionmaker[AsyncSession] | Callable[[], Any],
    approval_id: int,
    status: str,
    *,
    attempt: int = 1,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict[str, Any]:
    """Runs ``core.approval_hook`` for ``(approval_id, status)`` the way the job runner does.

    Returns the job result (``{"approval_id", "status", "note", "hook"}``). A failing
    ``on_decided`` raises ``JobRetry`` (``attempt < max_attempts``) or ``JobFailed`` (last
    attempt, ``hook_failed`` audited), exactly as in production; pass *attempt* to test retries.
    """
    ctx = JobContext(
        job_id=0,
        kind=JOB_HOOK,
        created_by=None,
        settings=_SETTINGS,
        session_factory=session_factory,  # type: ignore[arg-type]
        attempt=attempt,
        max_attempts=max_attempts,
    )
    payload = ApprovalHookPayload(approval_id=approval_id, status=status)  # type: ignore[arg-type]
    return await run_approval_hook(ctx, payload) or {}


async def _pending_rows(session: AsyncSession, approval_id: int) -> list[TiqoraApprovalApprover]:
    rows = await session.execute(
        select(TiqoraApprovalApprover)
        .join(TiqoraApprovalStep, TiqoraApprovalStep.id == TiqoraApprovalApprover.step_id)
        .where(
            TiqoraApprovalApprover.approval_id == approval_id,
            TiqoraApprovalApprover.status == "pending",
            TiqoraApprovalStep.status == "active",
        )
        .order_by(TiqoraApprovalApprover.id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


def _actor_of(row: TiqoraApprovalApprover) -> Actor:
    principal = Principal.parse(row.principal)
    if principal.kind == "agent":
        assert principal.user_id is not None
        return Actor.agent(principal.user_id)
    assert principal.login is not None
    return Actor.customer(principal.login)


async def _vote_all(
    session: AsyncSession, approval_id: int, decision: str, *, stop_after_first: bool
) -> ApprovalInfo:
    info = await service.get_info(session, approval_id)
    for _ in range(_MAX_ROUNDS):
        if info.status != "pending":
            return info
        rows = await _pending_rows(session, approval_id)
        if not rows:
            raise AssertionError(
                f"approval {approval_id} is pending but has no pending approver row to vote"
            )
        for row in rows:
            try:
                result = await service.decide(
                    session,
                    approval_id,
                    actor=_actor_of(row),
                    decision=decision,  # type: ignore[arg-type]
                    channel="api",
                    approver_id=row.id,
                )
            except ApprovalError as exc:
                if exc.code in ("already_decided", "approval_closed", "step_not_active"):
                    # the rule closed the step / request under us: re-read and go on
                    info = await service.get_info(session, approval_id)
                    break
                raise
            info = result.info
            if stop_after_first or info.status != "pending":
                return info
    raise AssertionError(f"approval {approval_id} did not reach a final status")


async def approve_all(session: AsyncSession, approval_id: int) -> ApprovalInfo:
    """Votes ``approved`` for every pending approver row of every stage, in order.

    Each vote is cast as the row's own principal over channel ``api``; stops once the request
    is no longer pending (a rule may close a step before every row voted). Flushes, never commits.
    """
    return await _vote_all(session, approval_id, "approved", stop_after_first=False)


async def reject_first(session: AsyncSession, approval_id: int) -> ApprovalInfo:
    """The first pending approver of the active step rejects; returns the resulting info.

    With a rule that tolerates one rejection (``all`` rejects at once, ``any`` waits for the
    others) the request may still be pending afterwards.
    """
    return await _vote_all(session, approval_id, "rejected", stop_after_first=True)


# --- lock order -------------------------------------------------------------------------------

_MARIADB_DEADLOCK = 1213
#: how long the vote is given to reach its lock wait (no PROCESS privilege to ask InnoDB)
_BLOCK_WAIT_SECONDS = 1.0


async def touch_ticket(session: AsyncSession, ticket_id: int) -> None:
    """A consumer's write of the Znuny ``ticket`` row (an X lock), e.g. its state change."""
    await session.execute(
        text("UPDATE ticket SET change_time = :now WHERE id = :t"),
        {"now": utc_now().replace(microsecond=0), "t": ticket_id},
    )


def _db_outcome(exc: DBAPIError) -> str:
    code = exc.orig.args[0] if exc.orig is not None and exc.orig.args else None
    return "deadlock" if code == _MARIADB_DEADLOCK else f"db_error:{type(exc.orig).__name__}"


async def _vote_and_commit(
    factory: async_sessionmaker[AsyncSession],
    approval_id: int,
    voter: Actor,
    approver_id: int | None,
) -> str:
    async with factory() as session:
        await session.rollback()  # deciding callers roll back first (F1(a))
        try:
            await service.decide(
                session,
                approval_id,
                actor=voter,
                decision="approved",
                channel="api",
                approver_id=approver_id,
                settings=_SETTINGS,
            )
            await session.commit()
        except ApprovalError as exc:
            await session.rollback()
            return exc.code
        except DBAPIError as exc:
            await session.rollback()
            return _db_outcome(exc)
        return "ok"


async def _wait_until_blocked(task: asyncio.Task[str]) -> None:
    """Gives *task* time to run into a lock wait; returns early once it finished."""
    for _ in range(10):
        if task.done():
            return
        await asyncio.sleep(_BLOCK_WAIT_SECONDS / 10)


async def race_consumer_and_vote(
    factory: async_sessionmaker[AsyncSession],
    approval_id: int,
    *,
    first: Callable[[AsyncSession], Awaitable[object]],
    then: Callable[[AsyncSession], Awaitable[object]],
    voter: Actor,
    approver_id: int | None = None,
    within_seconds: float = 45,
) -> tuple[str, str]:
    """``(consumer outcome, vote outcome)`` of a consumer transaction raced by one vote.

    Session A (the consumer) runs *first* - with the contract's order, the approval service
    call - then a vote by *voter* starts in session B and is given time to block on A's locks,
    then A runs *then* (its ``ticket`` write, e.g. :func:`touch_ticket`) and commits. Outcomes
    are ``ok``, an ``ApprovalError`` code, ``deadlock`` (MariaDB 1213) or ``db_error:<class>``.
    Needs a factory on committed data (two real connections), never ``approvals_session``.
    """

    votes: list[asyncio.Task[str]] = []

    async def consumer() -> tuple[str, str]:
        async with factory() as a:
            await first(a)
            votes.append(
                asyncio.create_task(_vote_and_commit(factory, approval_id, voter, approver_id))
            )
            await _wait_until_blocked(votes[0])
            try:
                await then(a)
                await a.commit()
                outcome = "ok"
            except ApprovalError as exc:
                await a.rollback()
                outcome = exc.code
            except DBAPIError as exc:
                await a.rollback()
                outcome = _db_outcome(exc)
        return outcome, await votes[0]

    try:
        return await asyncio.wait_for(consumer(), timeout=within_seconds)
    except TimeoutError as exc:
        raise AssertionError(
            f"consumer and vote did not finish within {within_seconds} s (lock wait?)"
        ) from exc
    finally:
        for vote in votes:
            if not vote.done():
                vote.cancel()
