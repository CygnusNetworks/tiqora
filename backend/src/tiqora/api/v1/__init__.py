"""REST v1 router aggregation."""

from fastapi import APIRouter

from tiqora.api.v1 import (
    auth,
    channels_sms,
    channels_whatsapp,
    customers,
    events,
    kb,
    queues,
    search,
    stats,
    tickets,
)
from tiqora.api.v1.admin import admin_router

api_v1_router = APIRouter()
api_v1_router.include_router(auth.router)
api_v1_router.include_router(queues.router)
api_v1_router.include_router(tickets.router)
api_v1_router.include_router(events.router)
api_v1_router.include_router(customers.router)
api_v1_router.include_router(search.router)
api_v1_router.include_router(kb.router)
api_v1_router.include_router(channels_sms.router)
api_v1_router.include_router(channels_whatsapp.router)
api_v1_router.include_router(stats.router)
api_v1_router.include_router(admin_router)

__all__ = ["api_v1_router"]
