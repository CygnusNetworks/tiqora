"""Admin CRUD API router aggregation, mounted at ``/api/v1/admin``.

Every route requires :data:`tiqora.api.v1.admin.deps.AdminUser` (403 for
non-admin agents). Follows the same include-router pattern as
``api/v1/__init__.py``.
"""

from __future__ import annotations

from fastapi import APIRouter

from tiqora.api.v1.admin import (
    api_keys,
    attachments,
    auth_config,
    auto_responses,
    channels,
    customers,
    dynamic_fields,
    gdpr,
    groups,
    mail_log,
    mail_outbound,
    placeholder_variables,
    postmaster_filters,
    priorities,
    queues,
    readonly,
    roles,
    states,
    subject_config,
    templates,
    users,
    webhooks,
)

admin_router = APIRouter(prefix="/admin")
admin_router.include_router(users.router)
admin_router.include_router(auth_config.router)
admin_router.include_router(groups.router)
admin_router.include_router(roles.router)
admin_router.include_router(queues.router)
admin_router.include_router(states.router)
admin_router.include_router(priorities.router)
admin_router.include_router(customers.router)
admin_router.include_router(templates.router)
admin_router.include_router(attachments.router)
admin_router.include_router(auto_responses.router)
admin_router.include_router(dynamic_fields.router)
admin_router.include_router(postmaster_filters.router)
admin_router.include_router(readonly.router)
admin_router.include_router(webhooks.router)
admin_router.include_router(api_keys.router)
admin_router.include_router(channels.router)
admin_router.include_router(mail_outbound.router)
admin_router.include_router(mail_log.router)
admin_router.include_router(gdpr.router)
admin_router.include_router(subject_config.router)
admin_router.include_router(placeholder_variables.queue_variables_router)
admin_router.include_router(placeholder_variables.customer_fields_router)

__all__ = ["admin_router"]
