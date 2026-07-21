"""Shared write-path helpers for the admin CRUD API.

Mirrors the invariant bookkeeping already used by
``domain/ticket_write_service.py`` / ``domain/queue_service.py``: every
Znuny row carries ``create_time``/``create_by``/``change_time``/``change_by``,
and "deletion" is a soft ``valid_id`` flip rather than a hard ``DELETE``
(Znuny never hard-deletes master-data rows referenced by tickets).

Also maps each admin resource to the Znuny ``CacheType`` string(s) declared
in the vendored ``znuny-6.5.22/Kernel/System/*.pm`` modules, and enqueues
cleanup signals via :func:`tiqora.znuny.cache_invalidation.invalidate_cache_type`
so the TiqoraSync daemon can clear Znuny's in-process master-data caches.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.znuny.cache_invalidation import invalidate_cache_type, invalidate_ticket_cache

# ---------------------------------------------------------------------------
# Znuny CacheType sets per admin resource
#
# Source pins are relative to znuny-6.5.22/Kernel/System/. CleanUp/Delete
# calls in the corresponding *Add/*Update/*Delete paths dictate the set.
# ---------------------------------------------------------------------------

# Queue.pm: $Self->{CacheType} = 'Queue'
QUEUE_CACHE_TYPES: tuple[str, ...] = ("Queue",)

# State.pm: $Self->{CacheType} = 'State'
STATE_CACHE_TYPES: tuple[str, ...] = ("State",)

# Priority.pm: $Self->{CacheType} = 'Priority'
PRIORITY_CACHE_TYPES: tuple[str, ...] = ("Priority",)

# Group.pm GroupAdd/GroupUpdate: Type => 'Group' + CleanUp CustomerGroup;
# validity change also cleans GroupPermissionUserGet / GroupPermissionGroupGet.
GROUP_CACHE_TYPES: tuple[str, ...] = (
    "Group",
    "CustomerGroup",
    "GroupPermissionUserGet",
    "GroupPermissionGroupGet",
)

# Group.pm RoleAdd/RoleUpdate: role list lives under CacheType 'Group';
# validity change also cleans permission lookup caches.
ROLE_CACHE_TYPES: tuple[str, ...] = (
    "Group",
    "GroupPermissionUserGet",
    "GroupPermissionGroupGet",
)

# Group.pm PermissionGroupRoleAdd: DBGroupRoleGet + permission lookups.
GROUP_ROLE_CACHE_TYPES: tuple[str, ...] = (
    "DBGroupRoleGet",
    "GroupPermissionUserGet",
    "GroupPermissionGroupGet",
)

# User.pm: $Self->{CacheType} = 'User'; UserUpdate also drops
# GroupPermissionGroupGet (and per-user GroupPermissionUserGet keys).
USER_CACHE_TYPES: tuple[str, ...] = (
    "User",
    "GroupPermissionUserGet",
    "GroupPermissionGroupGet",
)

# Group.pm PermissionGroupUserAdd: DBGroupUserGet + permission lookups.
USER_GROUP_CACHE_TYPES: tuple[str, ...] = (
    "DBGroupUserGet",
    "GroupPermissionUserGet",
    "GroupPermissionGroupGet",
)

# Group.pm PermissionRoleUserAdd: DBRoleUserGet + permission lookups.
USER_ROLE_CACHE_TYPES: tuple[str, ...] = (
    "DBRoleUserGet",
    "GroupPermissionUserGet",
    "GroupPermissionGroupGet",
)

# CustomerUser.pm: CacheType 'CustomerUser'; CustomerUser/DB.pm (Count '')
# also uses CustomerUser + CustomerUser_* suffixes and CustomerGroup.
CUSTOMER_USER_CACHE_TYPES: tuple[str, ...] = (
    "CustomerUser",
    "CustomerUser_CustomerIDList",
    "CustomerUser_CustomerSearch",
    "CustomerUser_CustomerSearchDetail",
    "CustomerUser_CustomerSearchDetailDynamicFields",
    "CustomerGroup",
)

# CustomerCompany/DB.pm (Count ''): CustomerCompany + list/search suffixes.
CUSTOMER_COMPANY_CACHE_TYPES: tuple[str, ...] = (
    "CustomerCompany",
    "CustomerCompany_CustomerCompanyList",
    "CustomerCompany_CustomerCompanySearchDetail",
    "CustomerCompany_CustomerSearchDetailDynamicFields",
)

# CustomerGroup.pm: $Self->{CacheType} = 'CustomerGroup'
CUSTOMER_USER_GROUP_CACHE_TYPES: tuple[str, ...] = ("CustomerGroup",)

# DynamicField.pm Add/Update/Delete: CleanUp DynamicField + DynamicFieldValue.
DYNAMIC_FIELD_CACHE_TYPES: tuple[str, ...] = (
    "DynamicField",
    "DynamicFieldValue",
)

# Salutation.pm: $Self->{CacheType} = 'Salutation'
SALUTATION_CACHE_TYPES: tuple[str, ...] = ("Salutation",)

# Signature.pm has no CacheType; queue rows reference signatures and Queue.pm
# caches queue data (including signature_id). Clear Queue so GUI reloads.
SIGNATURE_CACHE_TYPES: tuple[str, ...] = ("Queue",)

# StandardTemplate.pm Add/Update/Delete: CleanUp Type => 'Queue' only
# (templates are listed via QueueStandardTemplateMemberList under Queue).
TEMPLATE_CACHE_TYPES: tuple[str, ...] = ("Queue",)

# StdAttachment.pm: $Self->{CacheType} = 'StdAttachment'
ATTACHMENT_CACHE_TYPES: tuple[str, ...] = ("StdAttachment",)

# AutoResponse.pm has no CacheType; queue↔auto-response membership is stored
# under Queue cache keys (Queue.pm / AutoResponse list joins).
AUTO_RESPONSE_CACHE_TYPES: tuple[str, ...] = ("Queue",)


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def invalidate_znuny_cache_types(session: AsyncSession, cache_types: Sequence[str]) -> None:
    """Enqueue one signal row per distinct non-empty CacheType."""
    seen: set[str] = set()
    for cache_type in cache_types:
        cleaned = (cache_type or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        await invalidate_cache_type(session, cleaned)


async def invalidate_cache_for_queue(session: AsyncSession, queue_id: int) -> None:
    """Queue cache invalidation for every open ticket in *queue_id*.

    Config changes to a queue (escalation timers, salutation/signature,
    validity) are ticket-relevant for every ticket currently sitting in it.
    Also signals the master ``Queue`` CacheType cleanup.
    """
    await invalidate_znuny_cache_types(session, QUEUE_CACHE_TYPES)
    result = await session.execute(
        text("SELECT id FROM ticket WHERE queue_id = :qid"), {"qid": queue_id}
    )
    for (ticket_id,) in result.all():
        await invalidate_ticket_cache(session, ticket_id)


async def invalidate_cache_for_state(session: AsyncSession, state_id: int) -> None:
    await invalidate_znuny_cache_types(session, STATE_CACHE_TYPES)
    result = await session.execute(
        text("SELECT id FROM ticket WHERE ticket_state_id = :sid"), {"sid": state_id}
    )
    for (ticket_id,) in result.all():
        await invalidate_ticket_cache(session, ticket_id)


async def invalidate_cache_for_priority(session: AsyncSession, priority_id: int) -> None:
    await invalidate_znuny_cache_types(session, PRIORITY_CACHE_TYPES)
    result = await session.execute(
        text("SELECT id FROM ticket WHERE ticket_priority_id = :pid"), {"pid": priority_id}
    )
    for (ticket_id,) in result.all():
        await invalidate_ticket_cache(session, ticket_id)
