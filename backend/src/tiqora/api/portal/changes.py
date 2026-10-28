"""Customer portal: the change schedule of the customer's services (Mode B, off by default)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.portal.deps import CurrentCustomer
from tiqora.itsm.change.portal import PortalChangeOut, schedule_enabled, schedule_for_customer


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
    response: Response,
    start: Annotated[datetime, Query()],
    end: Annotated[datetime, Query()],
) -> list[PortalChangeOut]:
    """The body stays a list; ``X-Schedule-Truncated: true`` says the scan cap bit."""
    schedule = await schedule_for_customer(session, login=customer.login, start=start, end=end)
    if schedule.truncated:
        response.headers["X-Schedule-Truncated"] = "true"
    return schedule.items
