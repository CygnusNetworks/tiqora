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

import asyncio
import logging
import time
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

# "confirming": a changed deployed dump was seen for the first time; it becomes a revision
# when the next tick sees the same checksum (two-tick confirmation, ruling T5 fix).
WatchAction = Literal["unchanged", "confirming", "snapshot", "waiting_for_deploy", "running_live"]

_LOCK_TIMEOUT_SECONDS = 10
_LOCK_POLL_SECONDS = 0.1

# Checksum seen with an empty sync table that differs from the newest revision, per
# kind. A Znuny revision is recorded only when the same checksum shows up on two
# consecutive ticks, so a designer edit that starts between our pending check and
# the dump build is never snapshotted half-way (ruling T5 fix). In-process state.
_candidate: dict[str, str] = {}


@dataclass(frozen=True, slots=True)
class DeployResult:
    kind: RevisionKind
    revision_id: int
    checksum: str
    action: Literal["recorded", "unchanged"]


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
            deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
            while True:
                got = (
                    await conn.execute(
                        text("SELECT pg_try_advisory_lock(hashtext(:n))"), {"n": name}
                    )
                ).scalar_one()
                if got:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"could not get lock {name}")
                await asyncio.sleep(_LOCK_POLL_SECONDS)
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
            # Never mask the body's exception; the lock dies with the connection anyway.
            try:
                if postgres:
                    await conn.execute(text("SELECT pg_advisory_unlock(hashtext(:n))"), {"n": name})
                else:
                    await conn.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": name})
            except Exception:  # noqa: BLE001
                logger.exception("deploy_watch_unlock_failed", extra={"lock": name})


async def check_deploy_state(
    session: AsyncSession, kind: RevisionKind, *, ownership_active: bool
) -> WatchOutcome:
    """One watcher decision for one kind (plan D4 table).

    This function OWNS its transaction: every exit path ends committed (or rolled
    back when it raises), because the write path has to commit to take a fresh
    snapshot under the lock. Callers must not have pending writes of their own.
    """
    try:
        outcome = await _check_deploy_state(session, kind, ownership_active=ownership_active)
    except BaseException:
        await session.rollback()
        raise
    await session.commit()
    return outcome


async def _check_deploy_state(
    session: AsyncSession, kind: RevisionKind, *, ownership_active: bool
) -> WatchOutcome:
    pending = await pending_sync_count(session, kind)
    latest = await latest_revision_info(session, kind)

    if not ownership_active and pending > 0:
        _candidate.pop(kind, None)
        # Drafts exist: keep the current revision; without one the engine runs live.
        action: WatchAction = "running_live" if latest is None else "waiting_for_deploy"
        return WatchOutcome(kind, action, latest.id if latest else None, pending)

    dump = await _build_dump(session, kind)
    if latest is not None and dump_checksum(dump) == latest.checksum:
        _candidate.pop(kind, None)
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
            _candidate.pop(kind, None)
            action = "running_live" if latest is None else "waiting_for_deploy"
            return WatchOutcome(kind, action, latest.id if latest else None, pending)
        dump = await _build_dump(session, kind)
        checksum = dump_checksum(dump)
        if latest is not None and checksum == latest.checksum:
            _candidate.pop(kind, None)
            if ownership_active and pending > 0:
                await clear_sync_table(session, kind)
            await session.commit()
            return WatchOutcome(kind, "unchanged", latest.id, 0 if ownership_active else pending)
        if latest is not None and not ownership_active and _candidate.get(kind) != checksum:
            # First sighting of a changed dump after a deploy: confirm on the next tick.
            _candidate[kind] = checksum
            return WatchOutcome(kind, "confirming", latest.id, 0)
        _candidate.pop(kind, None)
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


async def deploy_from_tiqora(
    session: AsyncSession, kind: RevisionKind, *, user_id: int
) -> DeployResult:
    """Admin deploy: make the current ``pm_*``/``acl`` rows (drafts) the live revision.

    Znuny's deploy DB effect is "dump goes live, sync table is purged"
    (AdminProcessManagement.pm ProcessSync -> EntitySyncStatePurge, Entity.pm:324-345;
    AdminACL.pm ACLDeploy -> ACLsNeedSyncReset, ACL.pm:795-815). Single tick, no
    two-tick confirmation: the admin asked for exactly this state. Like
    :func:`check_deploy_state` this OWNS its transaction (commit, or rollback when it
    raises) and the caller must not have pending writes. Raises ``TimeoutError``
    when another writer holds the revision lock.
    """
    try:
        result = await _deploy_from_tiqora(session, kind, user_id=user_id)
    except BaseException:
        await session.rollback()
        raise
    await session.commit()
    return result


async def _deploy_from_tiqora(
    session: AsyncSession, kind: RevisionKind, *, user_id: int
) -> DeployResult:
    async with revision_write_lock(session, kind):
        # Fresh snapshot (MariaDB REPEATABLE READ) so the checksum re-check sees
        # revisions committed by another writer while we waited for the lock.
        await session.commit()
        latest = await latest_revision_info(session, kind)
        dump = await _build_dump(session, kind)
        checksum = dump_checksum(dump)
        _candidate.pop(kind, None)
        if latest is not None and latest.checksum == checksum:
            # Drafts that net out to the live state still count as deployed.
            await clear_sync_table(session, kind)
            await session.commit()
            logger.info(
                "deploy_from_tiqora",
                extra={
                    "kind": kind,
                    "user_id": user_id,
                    "revision_id": latest.id,
                    "checksum": checksum,
                    "action": "unchanged",
                },
            )
            return DeployResult(kind, latest.id, checksum, "unchanged")
        info = await record_revision(session, kind, dump=dump, source="tiqora", deployed_by=user_id)
        await clear_sync_table(session, kind)
        await session.commit()
        logger.info(
            "deploy_from_tiqora",
            extra={
                "kind": kind,
                "user_id": user_id,
                "revision_id": info.id,
                "checksum": info.checksum,
            },
        )
        return DeployResult(kind, info.id, info.checksum, "recorded")


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
