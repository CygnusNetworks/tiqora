"""Read-only access to Znuny's saved ticket-search profiles and the lookups the mapper needs.

Nothing here writes: ``search_profile`` is only ever selected from. No SQL join between the
Znuny tables and ``tiqora_*`` tables (mixed collations on production, error 1267) - every
lookup is its own query.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.dynamic_field import DynamicField
from tiqora.db.legacy.misc import SEARCH_PROFILE
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.ticket import TicketLockType, TicketStateType
from tiqora.domain.timezones import resolve_user_time_zone
from tiqora.saved_searches.znuny_import import ZnunyLookups
from tiqora.znuny.sysconfig import SysConfig

ProfileValues = dict[str, str | list[str]]

#: Znuny keeps the form's "previous search" under this name; it is not a saved template.
LAST_SEARCH = "last-search"
_LOGIN_PREFIX = "TicketSearch::"


async def load_znuny_profiles(session: AsyncSession, user_login: str) -> dict[str, ProfileValues]:
    """The agent's ticket-search profiles by name (``last-search`` skipped).

    The login matches case-insensitively, like Znuny's ``SearchProfileGet``. ``ARRAY`` rows
    become lists (row order), everything else a string, a NULL value an empty string.
    """
    stmt = select(
        SEARCH_PROFILE.c.profile_name,
        SEARCH_PROFILE.c.profile_type,
        SEARCH_PROFILE.c.profile_key,
        SEARCH_PROFILE.c.profile_value,
    ).where(func.lower(SEARCH_PROFILE.c.login) == func.lower(_LOGIN_PREFIX + user_login))
    profiles: dict[str, ProfileValues] = {}
    for name, ptype, key, value in (await session.execute(stmt)).all():
        if name == LAST_SEARCH:
            continue
        values = profiles.setdefault(str(name), {})
        text_value = "" if value is None else str(value)
        if ptype == "ARRAY":
            current = values.setdefault(str(key), [])
            if isinstance(current, list):
                current.append(text_value)
        else:
            values[str(key)] = text_value
    return profiles


async def load_lookups(session: AsyncSession, user_id: int) -> ZnunyLookups:
    """Id -> name maps of the Znuny master data plus the agent's time zone."""
    state_types = {
        int(i): str(n)
        for i, n in (await session.execute(select(TicketStateType.id, TicketStateType.name))).all()
    }
    queues = {
        int(i): str(n)
        for i, n in (
            await session.execute(select(Queue.id, Queue.name).where(Queue.valid_id == 1))
        ).all()
    }
    locks = {
        int(i): str(n)
        for i, n in (await session.execute(select(TicketLockType.id, TicketLockType.name))).all()
    }
    # every valid ticket field with its type: the mapper reports a type the filter catalogue
    # does not support per key (DF_TYPE_REASON) instead of treating the field as unknown
    dfs = {
        str(n): str(t)
        for n, t in (
            await session.execute(
                select(DynamicField.name, DynamicField.field_type).where(
                    DynamicField.object_type == "Ticket", DynamicField.valid_id == 1
                )
            )
        ).all()
    }
    zone = await resolve_user_time_zone(session, SysConfig(session), user_id)
    return ZnunyLookups(
        state_type_names=state_types,
        queue_names=queues,
        lock_names=locks,
        dynamic_fields=dfs,
        time_zone=zone,
    )
