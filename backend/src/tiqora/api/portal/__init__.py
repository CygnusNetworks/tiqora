"""Customer portal router aggregation — mounted under ``/api/portal``."""

from fastapi import APIRouter

from tiqora.api.portal import (
    approvals,
    attachments,
    auth,
    auth_sso,
    changes,
    cmdb,
    kb,
    preferences,
    process,
    security,
    tickets,
    webchat,
)

portal_router = APIRouter()
portal_router.include_router(auth.router)
portal_router.include_router(auth_sso.router)
portal_router.include_router(security.router)
portal_router.include_router(tickets.router)
portal_router.include_router(attachments.router)
portal_router.include_router(cmdb.router)
portal_router.include_router(changes.router)
portal_router.include_router(kb.router)
portal_router.include_router(process.router)
portal_router.include_router(approvals.router)
portal_router.include_router(preferences.router)
portal_router.include_router(webchat.router)

__all__ = ["portal_router"]
