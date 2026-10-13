"""REST v1 router aggregation."""

from fastapi import APIRouter

from tiqora.api.v1 import (
    agents,
    ai,
    auth,
    auth_sso,
    avatars,
    calendar,
    channels_phone,
    channels_sms,
    channels_telegram,
    channels_whatsapp,
    chat,
    customer_directory,
    customer_keys,
    customers,
    dashboards,
    events,
    integrations,
    itsm,
    jobs,
    kb,
    kiosk,
    oauth2_callback,
    phone_calls,
    phone_cti,
    plugin_channels,
    plugins,
    process,
    queues,
    reference,
    report_schedules,
    reports,
    saved_searches,
    search,
    stats,
    templates,
    ticket_filters,
    tickets,
    tickets_crypto,
    tickets_telegram,
    vcard_import,
)
from tiqora.api.v1.admin import admin_router
from tiqora.api.v1.cmdb import router as cmdb_router

api_v1_router = APIRouter()
api_v1_router.include_router(auth.router)
api_v1_router.include_router(auth_sso.router)
api_v1_router.include_router(oauth2_callback.router)
api_v1_router.include_router(agents.router)
api_v1_router.include_router(calendar.router)
api_v1_router.include_router(queues.router)
# Before tickets.router: /tickets/crypto-options must not hit /tickets/{ticket_id}.
api_v1_router.include_router(tickets_crypto.router)
api_v1_router.include_router(ticket_filters.router)
api_v1_router.include_router(tickets.router)
api_v1_router.include_router(tickets_telegram.router)
api_v1_router.include_router(phone_calls.router)
api_v1_router.include_router(phone_cti.router)
api_v1_router.include_router(ai.router)
api_v1_router.include_router(ai.refine_router)
api_v1_router.include_router(process.router)
api_v1_router.include_router(events.router)
api_v1_router.include_router(chat.router)
api_v1_router.include_router(chat.ticket_router)
api_v1_router.include_router(avatars.router)
# Before customers.router, like /companies/.../vcards before /{login}/vcard.
api_v1_router.include_router(avatars.customer_router)
api_v1_router.include_router(vcard_import.router)
api_v1_router.include_router(customers.router)
api_v1_router.include_router(customer_directory.router)
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
api_v1_router.include_router(reports.router)
api_v1_router.include_router(report_schedules.router)
api_v1_router.include_router(report_schedules.runs_router)
api_v1_router.include_router(saved_searches.router)
api_v1_router.include_router(reports.sources_router)
api_v1_router.include_router(dashboards.router)
api_v1_router.include_router(dashboards.widget_types_router)
api_v1_router.include_router(kiosk.router)
api_v1_router.include_router(integrations.router)
api_v1_router.include_router(jobs.router)
api_v1_router.include_router(itsm.router)
api_v1_router.include_router(itsm.ticket_router)
api_v1_router.include_router(plugins.router)
api_v1_router.include_router(cmdb_router)
api_v1_router.include_router(plugin_channels.router)
api_v1_router.include_router(admin_router)

__all__ = ["api_v1_router"]
