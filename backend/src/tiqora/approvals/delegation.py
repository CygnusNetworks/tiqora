"""Delegation (Vertretung) of approvals (A8).

A delegation lets ``substitute_user_id`` decide the approver rows of ``user_id`` for a period.
It is one hop only: the substitute's own delegations never extend to the delegator's rows
(:func:`active_delegators` answers for exactly one substitute). Functions flush, never commit;
timestamps are naive UTC like every other approvals column.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals.contract import Actor, ApprovalError
from tiqora.approvals.models import TiqoraApprovalDelegation
from tiqora.db.legacy.user import Users
from tiqora.jobs.service import utc_now
from tiqora.permissions.engine import PermissionEngine

_CHUNK = 500
# Znuny's root@localhost never substitutes anybody.
_SYSTEM_USER_ID = 1


def _naive_utc(value: datetime) -> datetime:
    """Naive UTC without microseconds (MariaDB ``DATETIME`` has none)."""
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.replace(microsecond=0)


def _active(at: datetime) -> ColumnElement[bool]:
    return and_(
        TiqoraApprovalDelegation.revoked_at.is_(None),
        TiqoraApprovalDelegation.valid_from <= at,
        or_(
            TiqoraApprovalDelegation.valid_until.is_(None),
            TiqoraApprovalDelegation.valid_until > at,
        ),
    )


async def list_delegations(
    session: AsyncSession,
    *,
    user_id: int | None = None,
    substitute_user_id: int | None = None,
    include_ended: bool = False,
) -> list[TiqoraApprovalDelegation]:
    """Delegations of a delegator and/or to a substitute, oldest first.

    Without *include_ended* revoked and expired ones are left out; future ones stay.
    """
    stmt = select(TiqoraApprovalDelegation).order_by(TiqoraApprovalDelegation.id)
    if user_id is not None:
        stmt = stmt.where(TiqoraApprovalDelegation.user_id == user_id)
    if substitute_user_id is not None:
        stmt = stmt.where(TiqoraApprovalDelegation.substitute_user_id == substitute_user_id)
    if not include_ended:
        stmt = stmt.where(
            TiqoraApprovalDelegation.revoked_at.is_(None),
            or_(
                TiqoraApprovalDelegation.valid_until.is_(None),
                TiqoraApprovalDelegation.valid_until > utc_now(),
            ),
        )
    return list((await session.execute(stmt)).scalars())


async def create_delegation(
    session: AsyncSession,
    *,
    user_id: int,
    substitute_user_id: int,
    valid_from: datetime,
    valid_until: datetime | None,
    created_by: int,
) -> TiqoraApprovalDelegation:
    """Creates a delegation.

    ``forward_target_invalid`` (422) when the substitute is the delegator, unknown or invalid;
    ``invalid_definition`` (422) when the period is empty; ``delegation_overlap`` (409) when
    the delegator has another non-revoked delegation overlapping the period.
    """
    start = _naive_utc(valid_from)
    end = _naive_utc(valid_until) if valid_until is not None else None
    if substitute_user_id in (user_id, _SYSTEM_USER_ID):
        raise ApprovalError("forward_target_invalid", 422, "a substitute must be another agent")
    valid = (
        await session.execute(select(Users.valid_id).where(Users.id == substitute_user_id))
    ).scalar_one_or_none()
    if valid != 1:
        raise ApprovalError("forward_target_invalid", 422, "the substitute is not a valid agent")
    if end is not None and end <= start:
        raise ApprovalError("invalid_definition", 422, "valid_until must be after valid_from")
    clash = select(TiqoraApprovalDelegation.id).where(
        TiqoraApprovalDelegation.user_id == user_id,
        TiqoraApprovalDelegation.revoked_at.is_(None),
        or_(
            TiqoraApprovalDelegation.valid_until.is_(None),
            TiqoraApprovalDelegation.valid_until > start,
        ),
    )
    if end is not None:
        clash = clash.where(TiqoraApprovalDelegation.valid_from < end)
    if (await session.execute(clash.limit(1))).first() is not None:
        raise ApprovalError("delegation_overlap", 409)
    row = TiqoraApprovalDelegation(
        user_id=user_id,
        substitute_user_id=substitute_user_id,
        valid_from=start,
        valid_until=end,
        created_by=created_by,
        created_at=_naive_utc(utc_now()),
    )
    session.add(row)
    await session.flush()
    return row


async def revoke_delegation(session: AsyncSession, delegation_id: int, *, actor: Actor) -> None:
    """Ends a delegation now. The delegator, an admin or the system may do that.

    ``forbidden`` (403) for anybody else, ``delegation_not_active`` (403) when it does not
    exist or has already ended.
    """
    row = (
        await session.execute(
            select(TiqoraApprovalDelegation)
            .where(TiqoraApprovalDelegation.id == delegation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApprovalError("delegation_not_active", 403)
    principal = actor.principal
    if principal is not None:
        allowed = principal.kind == "agent" and principal.user_id is not None
        if allowed:
            assert principal.user_id is not None
            allowed = principal.user_id == row.user_id or await PermissionEngine(session).is_admin(
                principal.user_id
            )
        if not allowed:
            raise ApprovalError("forbidden", 403)
    now = utc_now()
    if row.revoked_at is not None or (row.valid_until is not None and row.valid_until <= now):
        raise ApprovalError("delegation_not_active", 403)
    row.revoked_at = _naive_utc(now)
    await session.flush()


async def active_delegators(
    session: AsyncSession, substitute_user_id: int, *, at: datetime
) -> set[int]:
    """User ids whose approvals *substitute_user_id* may decide at *at* (one hop only)."""
    rows = await session.execute(
        select(TiqoraApprovalDelegation.user_id).where(
            TiqoraApprovalDelegation.substitute_user_id == substitute_user_id,
            _active(at),
        )
    )
    return {int(r[0]) for r in rows}


async def active_substitutes(
    session: AsyncSession, user_ids: Iterable[int], *, at: datetime
) -> dict[int, set[int]]:
    """``{delegator: {substitutes}}`` at *at* for the given delegators (for mails)."""
    wanted: Sequence[int] = sorted(set(user_ids))
    out: dict[int, set[int]] = {}
    for start in range(0, len(wanted), _CHUNK):
        rows = await session.execute(
            select(TiqoraApprovalDelegation.user_id, TiqoraApprovalDelegation.substitute_user_id)
            .where(
                TiqoraApprovalDelegation.user_id.in_(wanted[start : start + _CHUNK]),
                _active(at),
            )
            .order_by(TiqoraApprovalDelegation.id)
        )
        for delegator, substitute in rows:
            out.setdefault(int(delegator), set()).add(int(substitute))
    return out
