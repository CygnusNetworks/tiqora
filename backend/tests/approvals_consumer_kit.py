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
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import select
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
