"""E-mail decision links (A10): random single-use bearer tokens, stored only as sha256.

A link is a credential for exactly one approver row (and so one principal): it never reaches
another row of the same person, never a delegator's row, and it dies when that row is decided
through any channel, forwarded or made ``not_needed``, when the request closes, or at expiry.
Lookup is by the hash (unique index), so no secret is ever compared in application code.

Lock order is the engine's: ``tiqora_approval`` first, then approver/step/token rows. Service
functions take an ``AsyncSession``, flush and never commit. ``redeem_link`` is the exception that
rolls back first (lock-first, F1(b)): callers hand it a session without pending work.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import Select, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals import audit
from tiqora.approvals.contract import Actor, ApprovalError, Decision, Principal, StepSpec
from tiqora.approvals.models import (
    TiqoraApproval,
    TiqoraApprovalApprover,
    TiqoraApprovalStep,
    TiqoraApprovalToken,
)
from tiqora.approvals.settings import ApprovalSettings
from tiqora.config import Settings
from tiqora.jobs.service import utc_now

if TYPE_CHECKING:
    from tiqora.approvals.service import DecisionResult

#: a link outlives the step deadline by this much (a late click still counts until the sweeper acts)
_DEADLINE_GRACE = timedelta(hours=24)
_SYSTEM = Actor.system()
_PURGE_BATCH = 500


def link_digest(token: str) -> str:
    """The stored form of a token: sha256 hex (64 characters)."""
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class LinkTarget:
    token_id: int
    approval_id: int
    approver_id: int
    principal: Principal


def _now() -> datetime:
    return utc_now().replace(microsecond=0)  # MariaDB DATETIME has no microseconds


async def _one[T](session: AsyncSession, stmt: Select[tuple[T]], for_update: bool) -> T | None:
    """The one row of *stmt*, locked when asked, never a stale identity-map copy."""
    if for_update:
        stmt = stmt.with_for_update()
    return (
        await session.execute(stmt.execution_options(populate_existing=True))
    ).scalar_one_or_none()


async def issue_link(session: AsyncSession, approver_id: int, *, settings: ApprovalSettings) -> str:
    """A new link for a pending approver row of an active step; the plaintext is returned once.

    Expires after ``link_ttl_hours``, but not later than the step's deadline plus 24 hours. At
    most ``max_links_per_approver`` live links exist: the oldest live ones are revoked.
    ``not_an_approver`` for an unknown row, a row that is not ``pending``, a step that is not
    active or a closed request (no row is inserted).
    """
    from tiqora.approvals import _engine  # the engine imports this module for the revocations

    approval_id = (
        await session.execute(
            select(TiqoraApprovalApprover.approval_id).where(
                TiqoraApprovalApprover.id == approver_id
            )
        )
    ).scalar_one_or_none()
    if approval_id is None:
        raise ApprovalError("not_an_approver", 403)
    try:
        approval = await _engine.lock_approval(session, approval_id)
    except ApprovalError as exc:  # deleted in between
        raise ApprovalError("not_an_approver", 403) from exc
    row = await _one(
        session,
        select(TiqoraApprovalApprover).where(TiqoraApprovalApprover.id == approver_id),
        True,
    )
    step: TiqoraApprovalStep | None = None
    if row is not None:
        step = await _one(
            session, select(TiqoraApprovalStep).where(TiqoraApprovalStep.id == row.step_id), True
        )
    if (
        row is None
        or step is None
        or approval.status != "pending"
        or row.status != "pending"
        or step.status != "active"
    ):
        raise ApprovalError("not_an_approver", 403)
    if not settings.email_links_enabled or not StepSpec.model_validate_json(step.spec).allow_email:
        raise ApprovalError("forbidden", 403, "this step does not accept decisions by e-mail")

    now = _now()
    expires_at = now + timedelta(hours=settings.link_ttl_hours)
    if step.due_at is not None:
        expires_at = min(expires_at, step.due_at + _DEADLINE_GRACE)

    live = (
        (
            await session.execute(
                select(TiqoraApprovalToken)
                .where(
                    TiqoraApprovalToken.approver_id == approver_id,
                    TiqoraApprovalToken.used_at.is_(None),
                    TiqoraApprovalToken.revoked_at.is_(None),
                    TiqoraApprovalToken.expires_at > now,
                )
                .order_by(TiqoraApprovalToken.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    for stale in live[: max(0, len(live) + 1 - settings.max_links_per_approver)]:
        stale.revoked_at = now

    token = secrets.token_urlsafe(32)
    row_token = TiqoraApprovalToken(
        approval_id=approval.id,
        approver_id=approver_id,
        ticket_id=approval.ticket_id,
        token_hash=link_digest(token),
        created_at=now,
        expires_at=expires_at,
    )
    session.add(row_token)
    await session.flush()
    await audit.record(
        session,
        approval=approval,
        action="link_issued",
        actor=_SYSTEM,
        step_id=step.id,
        approver_id=approver_id,
        detail={"token_id": row_token.id},
    )
    return token


async def resolve_link(
    session: AsyncSession, token: str, *, for_update: bool = False
) -> LinkTarget:
    """The approver row a live link stands for, without consuming it.

    ``link_invalid`` (unknown, revoked, request closed, approver not pending or step not
    active), ``link_used`` or ``link_expired`` (all HTTP 410). With *for_update* the token,
    request, approver and step rows are read with locks; the caller must already hold the
    request lock (``redeem_link`` does) so the lock order stays request first.
    """
    digest = link_digest(token)
    row = await _one(
        session,
        select(TiqoraApprovalToken).where(TiqoraApprovalToken.token_hash == digest),
        for_update,
    )
    if row is None:
        raise ApprovalError("link_invalid", 410)
    if row.used_at is not None:
        raise ApprovalError("link_used", 410)
    if row.revoked_at is not None:
        raise ApprovalError("link_invalid", 410)

    approval = await _one(
        session, select(TiqoraApproval).where(TiqoraApproval.id == row.approval_id), for_update
    )
    approver = await _one(
        session,
        select(TiqoraApprovalApprover).where(TiqoraApprovalApprover.id == row.approver_id),
        for_update,
    )
    step: TiqoraApprovalStep | None = None
    if approver is not None:
        step = await _one(
            session,
            select(TiqoraApprovalStep).where(TiqoraApprovalStep.id == approver.step_id),
            for_update,
        )
    if (
        approval is None
        or approver is None
        or step is None
        or approval.status != "pending"
        or approver.status != "pending"
        or step.status != "active"
    ):
        raise ApprovalError("link_invalid", 410)
    if row.expires_at <= _now():
        raise ApprovalError("link_expired", 410)
    return LinkTarget(
        token_id=row.id,
        approval_id=row.approval_id,
        approver_id=row.approver_id,
        principal=Principal.parse(approver.principal),
    )


async def redeem_link(
    session: AsyncSession,
    token: str,
    *,
    decision: Decision,
    comment: str | None,
    settings: Settings | None = None,
) -> DecisionResult:
    """Casts the vote of the link's approver row, once, however many requests race.

    Two phases (F1(b)): a plain lookup of the request id by hash, ``rollback()``, then the
    request lock as the first statement of the transaction. The request lock plus the locked
    re-read of the link, approver and step decide the race (the loser sees ``used_at`` or a
    decided row); the conditional ``UPDATE … WHERE used_at IS NULL AND revoked_at IS NULL``
    (rowcount 1, else ``link_used``) is a second barrier. The vote is cast
    through ``service.decide`` in the same transaction; when it raises, ``redeem_link`` rolls
    back itself so ``used_at`` is undone. Links die while ``email_links_enabled`` is off. The
    caller commits. Raises ``link_*`` (HTTP 410) or whatever
    ``decide`` refuses with.
    """
    from tiqora.approvals import _engine, service

    approval_id = (
        await session.execute(
            select(TiqoraApprovalToken.approval_id).where(
                TiqoraApprovalToken.token_hash == link_digest(token)
            )
        )
    ).scalar_one_or_none()
    if approval_id is None:
        raise ApprovalError("link_invalid", 410)
    await session.rollback()
    try:
        await _engine.lock_approval(session, approval_id)
    except ApprovalError as exc:
        raise ApprovalError("link_invalid", 410) from exc
    if not (await _engine.load_settings(session, settings)).email_links_enabled:
        raise ApprovalError("link_invalid", 410)
    target = await resolve_link(session, token, for_update=True)
    consumed = await session.execute(
        update(TiqoraApprovalToken)
        .where(
            TiqoraApprovalToken.id == target.token_id,
            TiqoraApprovalToken.used_at.is_(None),
            TiqoraApprovalToken.revoked_at.is_(None),
        )
        .values(used_at=_now())
    )
    if consumed.rowcount != 1:  # type: ignore[attr-defined]
        raise ApprovalError("link_used", 410)
    principal = target.principal
    if principal.kind == "agent" and principal.user_id is not None:
        actor = Actor.agent(principal.user_id)
    elif principal.kind == "customer" and principal.login:
        actor = Actor.customer(principal.login)
    else:
        raise ApprovalError("link_invalid", 410)
    try:
        return await service.decide(
            session,
            approval_id,
            actor=actor,
            decision=decision,
            channel="email",
            comment=comment,
            approver_id=target.approver_id,
            token_id=target.token_id,
            lock_held=True,
            settings=settings,
        )
    except BaseException:
        await session.rollback()  # no partial consume (used_at) is left for a caller to commit
        raise


async def revoke_links(
    session: AsyncSession, *, approver_ids: Iterable[int], redeemed: int | None = None
) -> int:
    """Kills the live links of these approver rows; returns how many were revoked.

    *redeemed* (the link a vote came in with) is marked used instead of revoked.
    """
    ids = list(approver_ids)
    if not ids:
        return 0
    now = _now()
    if redeemed is not None:
        await session.execute(
            update(TiqoraApprovalToken)
            .where(
                TiqoraApprovalToken.id == redeemed,
                TiqoraApprovalToken.approver_id.in_(ids),
                TiqoraApprovalToken.used_at.is_(None),
            )
            .values(used_at=now)
        )
    result = await session.execute(
        update(TiqoraApprovalToken)
        .where(
            TiqoraApprovalToken.approver_id.in_(ids),
            TiqoraApprovalToken.used_at.is_(None),
            TiqoraApprovalToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


async def revoke_approval_links(session: AsyncSession, approval_id: int) -> int:
    """Kills every live link of a request (it closed)."""
    result = await session.execute(
        update(TiqoraApprovalToken)
        .where(
            TiqoraApprovalToken.approval_id == approval_id,
            TiqoraApprovalToken.used_at.is_(None),
            TiqoraApprovalToken.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


async def purge_links(session: AsyncSession, *, older_than: datetime) -> int:
    """Deletes links that expired, were used or were revoked before *older_than* (sweeper)."""
    total = 0
    while True:
        ids = (
            (
                await session.execute(
                    select(TiqoraApprovalToken.id)
                    .where(
                        or_(
                            TiqoraApprovalToken.used_at < older_than,
                            TiqoraApprovalToken.revoked_at < older_than,
                            TiqoraApprovalToken.expires_at < older_than,
                        )
                    )
                    .order_by(TiqoraApprovalToken.id)
                    .limit(_PURGE_BATCH)
                )
            )
            .scalars()
            .all()
        )
        if not ids:
            return total
        await session.execute(delete(TiqoraApprovalToken).where(TiqoraApprovalToken.id.in_(ids)))
        total += len(ids)
