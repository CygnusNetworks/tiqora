"""Admin read-only email communication log — ``/api/v1/admin/mail/log``.

Paginated list with direction/status/q/date filters plus detail by id.
Mirrors Znuny's Communication Log (inbound + outbound send attempts).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParams, Page, window
from tiqora.db.tiqora.models import TiqoraMailLog

router = APIRouter(prefix="/mail", tags=["admin:mail"])

MailDirection = Literal["in", "out"]
MailLogStatus = Literal["queued", "sent", "failed", "received", "filtered"]


class MailLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    direction: str
    status: str
    from_addr: str
    to_addr: str
    cc_addr: str | None = None
    subject: str
    message_id: str | None = None
    ticket_id: int | None = None
    article_id: int | None = None
    queue: str | None = None
    smtp_code: int | None = None
    detail: str | None = None
    duration_ms: int | None = None


class MailLogListParams(ListParams):
    """ListParams plus mail-log-specific filters (``valid`` is unused)."""

    direction: MailDirection | None = None
    status: MailLogStatus | None = None
    q: str | None = None
    from_dt: datetime | None = None
    to_dt: datetime | None = None


def mail_log_list_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 25,
    direction: Annotated[MailDirection | None, Query()] = None,
    status_filter: Annotated[MailLogStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query()] = None,
    from_dt: Annotated[datetime | None, Query(alias="from")] = None,
    to_dt: Annotated[datetime | None, Query(alias="to")] = None,
) -> MailLogListParams:
    return MailLogListParams(
        page=page,
        page_size=page_size,
        valid="all",
        direction=direction,
        status=status_filter,
        q=q,
        from_dt=from_dt,
        to_dt=to_dt,
    )


MailLogListParamsDep = Annotated[MailLogListParams, Depends(mail_log_list_params)]


@router.get("/log", response_model=Page[MailLogOut])
async def list_mail_log(
    admin: AdminUser,
    session: DbSession,
    params: MailLogListParamsDep,
) -> Page[MailLogOut]:
    _ = admin
    stmt = select(TiqoraMailLog)
    if params.direction is not None:
        stmt = stmt.where(TiqoraMailLog.direction == params.direction)
    if params.status is not None:
        stmt = stmt.where(TiqoraMailLog.status == params.status)
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(
            or_(
                TiqoraMailLog.from_addr.ilike(like),
                TiqoraMailLog.to_addr.ilike(like),
                TiqoraMailLog.subject.ilike(like),
            )
        )
    if params.from_dt is not None:
        # MariaDB DateTime is naive; strip tzinfo for comparison.
        from_val = params.from_dt.replace(tzinfo=None) if params.from_dt.tzinfo else params.from_dt
        stmt = stmt.where(TiqoraMailLog.created_at >= from_val)
    if params.to_dt is not None:
        to_val = params.to_dt.replace(tzinfo=None) if params.to_dt.tzinfo else params.to_dt
        stmt = stmt.where(TiqoraMailLog.created_at <= to_val)
    stmt = stmt.order_by(TiqoraMailLog.created_at.desc(), TiqoraMailLog.id.desc())
    rows, total = await window(session, stmt, params)
    return Page(
        items=[MailLogOut.model_validate(r) for r in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get("/log/{log_id}", response_model=MailLogOut)
async def get_mail_log(
    log_id: int,
    admin: AdminUser,
    session: DbSession,
) -> MailLogOut:
    _ = admin
    row = await session.get(TiqoraMailLog, log_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Mail log entry not found"
        )
    return MailLogOut.model_validate(row)
