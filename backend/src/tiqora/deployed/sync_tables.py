"""Znuny's "unsynced changes" tables: ``pm_entity_sync`` (processes) and ``acl_sync``.

Znuny's designer / ACL editor mark drafts there; a deploy deletes every row
(``EntitySyncStatePurge`` Entity.pm:324-345, ``ACLsNeedSyncReset`` ACL.pm:795-815).
The deploy watcher uses "table is empty" as "the last deploy is finished".
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.deployed.store import RevisionKind

AclSyncState = Literal["not_sync", "deleted"]

_TABLES: dict[str, str] = {"process": "pm_entity_sync", "acl": "acl_sync"}


async def pending_sync_count(session: AsyncSession, kind: RevisionKind) -> int:
    table = _TABLES[kind]
    return int((await session.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one())  # noqa: S608


async def mark_acl_changed(session: AsyncSession, *, acl_id: int, sync_state: AclSyncState) -> None:
    """Insert one ``acl_sync`` row like Znuny (ACL.pm:197, 256, 565): always an INSERT."""
    await session.execute(
        text(
            "INSERT INTO acl_sync (acl_id, sync_state, create_time, change_time)"
            " VALUES (:acl_id, :state, current_timestamp, current_timestamp)"
        ),
        {"acl_id": str(acl_id), "state": sync_state},
    )


async def clear_sync_table(session: AsyncSession, kind: RevisionKind) -> int:
    """Znuny's deploy DB effect. Only for ownership mode (Tiqora is the only system)."""
    table = _TABLES[kind]
    result = await session.execute(text(f"DELETE FROM {table}"))  # noqa: S608
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
