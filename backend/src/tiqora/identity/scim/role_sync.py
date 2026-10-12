"""SCIM-driven role sync: the pending queue (Task 2); ``scim_attributes`` and the tick follow.

Import cycle (see login.py): ``identity.provisioning`` -> ``domain.user_provisioning`` ->
``tiqora.api.v1`` -> ``identity.http`` -> ``login`` -> ``store``. ``store.update_provider`` imports
this module inside the function, never at module level.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraScimPendingSync
from tiqora.identity.types import utc_now

_CHUNK = 1000


async def enqueue_role_sync(
    session: AsyncSession, provider_id: int, user_ids: Iterable[int]
) -> int:
    """Queue agents for a role re-evaluation; returns the number of rows written.

    Delete-then-bulk-insert with ``generation = old + 1`` (the tick deletes ``WHERE generation =
    <read>``, so a newer enqueue survives). Flushes only; the caller owns the transaction.
    """
    ids = sorted(set(user_ids))
    if not ids:
        return 0
    model = TiqoraScimPendingSync
    old: dict[int, int] = {}
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i : i + _CHUNK]
        rows = await session.execute(
            select(model.user_id, model.generation).where(
                model.provider_id == provider_id, model.user_id.in_(chunk)
            )
        )
        old.update({uid: gen for uid, gen in rows.all()})
        await session.execute(
            delete(model).where(model.provider_id == provider_id, model.user_id.in_(chunk))
        )
    now = utc_now()
    await session.execute(
        insert(model),
        [
            {
                "provider_id": provider_id,
                "user_id": uid,
                "queued_at": now,
                "generation": old.get(uid, 0) + 1,
            }
            for uid in ids
        ],
    )
    await session.flush()
    return len(ids)
