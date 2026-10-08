"""Deploy watcher: turns Znuny's "drafts + deploy" model into immutable revisions.

Znuny's designer edits ``pm_*``/``acl`` rows immediately but only a deploy makes
them live (the sync tables fill on edit and empty on deploy). The watcher snapshots
the dump whenever the sync table is empty and the dump changed, so the engine runs
the last *deployed* state and never half-edited drafts.

Writers (this watcher and a later admin deploy) serialise check-then-insert with a
database lock on a dedicated connection and compare the newest checksum, so a
revision is never duplicated and the newest id is the newest commit (rulings T4/T5/T5b).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.db.engine import get_session_factory
from tiqora.deployed.acl_dump import build_acl_dump
from tiqora.deployed.store import (
    RevisionKind,
    RevisionSource,
    latest_revision_info,
    record_revision,
)
from tiqora.deployed.sync_tables import clear_sync_table, pending_sync_count
from tiqora.domain.ownership import get_ownership_state
from tiqora.process.dump import build_process_dump, dump_checksum

logger = logging.getLogger(__name__)

WatchAction = Literal["unchanged", "snapshot", "waiting_for_deploy", "running_live"]

_LOCK_TIMEOUT_SECONDS = 10


@dataclass(frozen=True, slots=True)
class WatchOutcome:
    kind: RevisionKind
    action: WatchAction
    revision_id: int | None
    pending_changes: int


async def _build_dump(session: AsyncSession, kind: RevisionKind) -> dict[str, Any]:
    if kind == "process":
        return await build_process_dump(session)
    return await build_acl_dump(session)


@asynccontextmanager
async def revision_write_lock(session: AsyncSession, kind: RevisionKind) -> AsyncIterator[None]:
    """Cross-process lock for check-then-insert on one revision table.

    Held on a dedicated connection (a MariaDB ``GET_LOCK`` belongs to its
    connection, which the session may swap after a commit). Reusable by the admin
    deploy endpoint.
    """
    engine = session.bind
    if not isinstance(engine, AsyncEngine):  # pragma: no cover - always bound in the app
        raise RuntimeError("revision_write_lock needs a session bound to an AsyncEngine")
    name = f"tiqora_deploy_{kind}"
    postgres = engine.dialect.name == "postgresql"
    async with engine.connect() as conn:
        if postgres:
            await conn.execute(text("SELECT pg_advisory_lock(hashtext(:n))"), {"n": name})
        else:
            got = (
                await conn.execute(
                    text("SELECT GET_LOCK(:n, :t)"), {"n": name, "t": _LOCK_TIMEOUT_SECONDS}
                )
            ).scalar_one()
            if got != 1:
                raise TimeoutError(f"could not get lock {name}")
        try:
            yield
        finally:
            if postgres:
                await conn.execute(text("SELECT pg_advisory_unlock(hashtext(:n))"), {"n": name})
            else:
                await conn.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": name})


async def check_deploy_state(
    session: AsyncSession, kind: RevisionKind, *, ownership_active: bool
) -> WatchOutcome:
    """One watcher decision for one kind (table in the module docs / plan D4)."""
    pending = await pending_sync_count(session, kind)
    latest = await latest_revision_info(session, kind)

    if not ownership_active and pending > 0:
        # Drafts exist: keep the current revision; without one the engine runs live.
        action: WatchAction = "running_live" if latest is None else "waiting_for_deploy"
        return WatchOutcome(kind, action, latest.id if latest else None, pending)

    dump = await _build_dump(session, kind)
    if latest is not None and dump_checksum(dump) == latest.checksum:
        if ownership_active and pending > 0:
            await clear_sync_table(session, kind)
            pending = 0
        return WatchOutcome(kind, "unchanged", latest.id, pending)

    async with revision_write_lock(session, kind):
        # End the read snapshot (MariaDB REPEATABLE READ) so the re-check sees
        # revisions another writer committed while we waited for the lock.
        await session.commit()
        pending = await pending_sync_count(session, kind)
        latest = await latest_revision_info(session, kind)
        if not ownership_active and pending > 0:
            action = "running_live" if latest is None else "waiting_for_deploy"
            return WatchOutcome(kind, action, latest.id if latest else None, pending)
        dump = await _build_dump(session, kind)
        if latest is not None and dump_checksum(dump) == latest.checksum:
            if ownership_active and pending > 0:
                await clear_sync_table(session, kind)
            await session.commit()
            return WatchOutcome(kind, "unchanged", latest.id, 0 if ownership_active else pending)
        source: RevisionSource
        if ownership_active:
            source = "tiqora"
        elif latest is None:
            source = "initial"
        else:
            source = "znuny"
        info = await record_revision(session, kind, dump=dump, source=source, deployed_by=None)
        if ownership_active and pending > 0:
            await clear_sync_table(session, kind)
        await session.commit()
        logger.info("deploy_watch_snapshot", extra={"kind": kind, "source": source, "id": info.id})
        return WatchOutcome(kind, "snapshot", info.id, 0 if ownership_active else pending)


async def run_deploy_watch_tick(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, Any]:
    factory = session_factory or get_session_factory()
    async with factory() as session:
        ownership_active = (await get_ownership_state(session, settings)).active
        process = await check_deploy_state(session, "process", ownership_active=ownership_active)
        acl = await check_deploy_state(session, "acl", ownership_active=ownership_active)
        await session.commit()
    live = "running_live" in (process.action, acl.action)
    return {
        "mode": "live" if live else "deployed",
        "process": asdict(process),
        "acl": asdict(acl),
    }
