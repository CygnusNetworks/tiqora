"""Portal: which web chat to show on the customer portal pages.

The portal frame sends the visitor's portal session to ``POST /api/chat/v1/identity/portal``
(own origin only); this endpoint just tells the portal UI which chat key to embed.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from tiqora.api.portal.deps import CurrentCustomer, DbSession
from tiqora.channels.common import channel_enabled
from tiqora.db.tiqora.models import TiqoraChatConfig

router = APIRouter(prefix="/webchat", tags=["portal-webchat"])


class PortalWebchatOut(BaseModel):
    public_key: str


@router.get("", response_model=PortalWebchatOut)
async def get_portal_webchat(customer: CurrentCustomer, db: DbSession) -> PortalWebchatOut:
    """The public key of the valid chat shown on the portal (404 when there is none or the
    web-chat channel is off)."""
    _ = customer
    if await channel_enabled(db, "webchat"):
        row = (
            await db.execute(
                select(TiqoraChatConfig)
                .where(TiqoraChatConfig.valid.is_(True), TiqoraChatConfig.show_on_portal.is_(True))
                .order_by(TiqoraChatConfig.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is not None:
            return PortalWebchatOut(public_key=row.public_key)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
