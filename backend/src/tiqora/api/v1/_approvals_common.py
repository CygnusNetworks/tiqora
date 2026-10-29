"""Pieces shared by the agent (``approvals.py``) and the admin (``admin/approvals.py``) routers."""

from __future__ import annotations

from collections.abc import Awaitable, Iterable
from datetime import UTC, datetime
from typing import NoReturn

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals import detail
from tiqora.approvals.contract import ApprovalError
from tiqora.approvals.models import TiqoraApprovalDelegation
from tiqora.approvals.schemas import DelegationOut
from tiqora.jobs.service import utc_now


def http_error(err: ApprovalError) -> HTTPException:
    """``{"detail": {"code": ..., "detail": ...}}``: the frontend reads ``detail.code``."""
    return HTTPException(err.http_status, detail={"code": err.code, "detail": err.detail})


def refuse(code: str, http_status: int, detail_text: str | None = None) -> NoReturn:
    raise http_error(ApprovalError(code, http_status, detail_text))


async def run_engine[T](session: AsyncSession, operation: Awaitable[T]) -> T:
    """Awaits an engine call; an :class:`ApprovalError` rolls back and becomes an HTTP error."""
    try:
        return await operation
    except ApprovalError as err:
        await session.rollback()
        raise http_error(err) from err


def ensure_period_open(valid_until: datetime | None) -> None:
    """A delegation whose period is over already would never act: 422 ``invalid_definition``."""
    if valid_until is None:
        return
    end = valid_until
    if end.tzinfo is not None:
        end = end.astimezone(UTC).replace(tzinfo=None)
    if end <= utc_now():
        refuse("invalid_definition", 422, "valid_until lies in the past")


async def delegation_out(
    session: AsyncSession, rows: Iterable[TiqoraApprovalDelegation]
) -> list[DelegationOut]:
    rows = list(rows)
    labels = await detail.user_labels(
        session, [uid for r in rows for uid in (r.user_id, r.substitute_user_id)]
    )
    return [
        DelegationOut(
            id=r.id,
            user_id=r.user_id,
            user_label=labels.get(r.user_id, "?"),
            substitute_user_id=r.substitute_user_id,
            substitute_label=labels.get(r.substitute_user_id, "?"),
            valid_from=r.valid_from,
            valid_until=r.valid_until,
            created_at=r.created_at,
        )
        for r in rows
    ]
