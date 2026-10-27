"""Change-management endpoints for agents — ``/api/v1/itsm/changes`` (API-key area ``tickets``).

``ChangeError`` is an ``HTTPException``: services raise it and the answer is
``{"detail": {"code", ...}}`` with its status. Static paths (``/changes/status``) are declared
before ``/changes/{ticket_id:int}``.
"""

from __future__ import annotations

from fastapi import APIRouter

from tiqora.api.deps import AppSettings, CurrentUser, DbSession
from tiqora.api.v1.itsm_change_schemas import ChangeStatusOut
from tiqora.itsm.change.gate import change_availability

router = APIRouter(prefix="/itsm", tags=["itsm-change"])
#: ticket sub-resources (``/tickets/{id}/change``, …)
ticket_router = APIRouter(prefix="/tickets", tags=["itsm-change"])


@router.get("/changes/status", response_model=ChangeStatusOut)
async def get_change_status(
    user: CurrentUser, session: DbSession, settings: AppSettings
) -> ChangeStatusOut:
    """Whether changes are available and what the caller may do. Answers in every mode."""
    availability = await change_availability(session, settings, user_id=user.id)
    return ChangeStatusOut(
        mode=availability.mode,
        available=availability.available,
        reason=availability.reason,
        legacy=availability.legacy,
        can_manage=availability.can_manage,
        can_see_legacy=availability.can_see_legacy,
        setup_complete_change=availability.setup_complete_change,
    )
