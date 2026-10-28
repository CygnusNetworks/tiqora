"""Customer portal: the change schedule of the customer's services (Mode B, off by default)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.portal.deps import CurrentCustomer
from tiqora.itsm.change.portal import schedule_enabled, schedule_for_customer


class PortalChangeOut(BaseModel):
    tn: str
    title: str
    change_type: Literal["standard", "normal", "emergency"]
    phase: Literal["scheduled", "implementing", "closed"]
    planned_start: datetime | None
    planned_end: datetime | None
    #: only the customer's own services among the affected ones
    services: list[str]


async def require_schedule_enabled(session: DbSession, settings: AppSettings) -> None:
    """404 (like a disabled portal) unless Mode B and ``portal_schedule_enabled``."""
    if not await schedule_enabled(session, settings):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


router = APIRouter(
    prefix="/change-schedule",
    tags=["portal-changes"],
    dependencies=[Depends(require_schedule_enabled)],
)


@router.get("", response_model=list[PortalChangeOut])
async def get_change_schedule(
    customer: CurrentCustomer,
    session: DbSession,
    start: Annotated[datetime, Query()],
    end: Annotated[datetime, Query()],
) -> list[PortalChangeOut]:
    items = await schedule_for_customer(session, login=customer.login, start=start, end=end)
    return [
        PortalChangeOut(
            tn=i.tn,
            title=i.title,
            change_type=i.change_type,  # type: ignore[arg-type]
            phase=i.phase,  # type: ignore[arg-type]
            planned_start=i.planned_start,
            planned_end=i.planned_end,
            services=list(i.services),
        )
        for i in items
    ]
