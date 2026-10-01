"""REST v1 router aggregation."""

from fastapi import APIRouter

from tiqora.api.v1 import (
    agents,
    ai,
    auth,
    calendar,
    channels_phone,
    channels_sms,
    channels_telegram,
    channels_whatsapp,
    customer_keys,
    customers,
    events,
    integrations,
    kb,
    oauth2_callback,
    phone_calls,
    phone_cti,
    process,
    queues,
    reference,
    search,
    stats,
    templates,
    tickets,
    tickets_crypto,
    tickets_telegram,
)
from tiqora.api.v1.admin import admin_router

api_v1_router = APIRouter()
api_v1_router.include_router(auth.router)
api_v1_router.include_router(oauth2_callback.router)
api_v1_router.include_router(agents.router)
api_v1_router.include_router(calendar.router)
api_v1_router.include_router(queues.router)
# Before tickets.router: /tickets/crypto-options must not hit /tickets/{ticket_id}.
api_v1_router.include_router(tickets_crypto.router)
api_v1_router.include_router(tickets.router)
api_v1_router.include_router(tickets_telegram.router)
api_v1_router.include_router(phone_calls.router)
api_v1_router.include_router(phone_cti.router)
api_v1_router.include_router(ai.router)
api_v1_router.include_router(ai.refine_router)
api_v1_router.include_router(process.router)
api_v1_router.include_router(events.router)
api_v1_router.include_router(customers.router)
api_v1_router.include_router(customer_keys.router)
api_v1_router.include_router(reference.router)
api_v1_router.include_router(templates.router)
api_v1_router.include_router(search.router)
api_v1_router.include_router(kb.router)
api_v1_router.include_router(channels_sms.router)
api_v1_router.include_router(channels_whatsapp.router)
api_v1_router.include_router(channels_telegram.router)
api_v1_router.include_router(channels_phone.router)
api_v1_router.include_router(stats.router)
api_v1_router.include_router(integrations.router)
api_v1_router.include_router(admin_router)

__all__ = ["api_v1_router"]
