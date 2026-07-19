"""Customer portal router aggregation — mounted under ``/api/portal``."""

from fastapi import APIRouter

from tiqora.api.portal import attachments, auth, tickets

portal_router = APIRouter()
portal_router.include_router(auth.router)
portal_router.include_router(tickets.router)
portal_router.include_router(attachments.router)

__all__ = ["portal_router"]
