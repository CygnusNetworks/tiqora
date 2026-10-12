"""Deploy through the Znuny add-on TiqoraSync while Znuny still runs (plan D15).

TiqoraSync >= 1.2.0 writes a heartbeat and, when Tiqora queues a ``TiqoraDeploy_<kind>_<id>``
cache signal, runs Znuny's own ``ProcessDump``/``EntitySyncStatePurge`` (``ACLDump``/
``ACLsNeedSyncReset`` for ACLs) and reports back through ``tiqora_settings``.

Keys (all in ``tiqora_settings``)::

    tiqorasync.heartbeat       {"version", "framework", "capabilities": [...], "at": <epoch>}
    tiqorasync.deploy.<kind>   {"signal_id": n, "revision_id": n, "status": "ok"|"error",
                                "message": "", "at": <epoch>}        (written by the add-on)
    tiqorasync.deploy.<kind>.requested
                               {"signal_id": n, "revision_id": n, "at": <epoch>}   (Tiqora)

The status is keyed by the signal row id (``tiqora_cache_invalidation.id``): a revision id is
not unique per deploy request (an unchanged deploy queues a signal for the same revision).
``at`` values are integer epoch seconds, never formatted times (Znuny formats in
``OTRSTimeZone``); they are converted to naive UTC here.
"""

from __future__ import annotations

import calendar
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraCacheInvalidation, TiqoraSettings
from tiqora.deployed.store import RevisionInfo, RevisionKind
from tiqora.domain.schemas import UtcDateTime
from tiqora.domain.settings_store import get_setting
from tiqora.jobs.service import utc_now

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "tiqorasync.heartbeat"
DEPLOY_STATUS_KEY: dict[str, str] = {
    "process": "tiqorasync.deploy.process",
    "acl": "tiqorasync.deploy.acl",
}
DEPLOY_REQUESTED_KEY: dict[str, str] = {k: f"{v}.requested" for k, v in DEPLOY_STATUS_KEY.items()}
HEARTBEAT_MAX_AGE = timedelta(minutes=10)
PENDING_WARN_AFTER = timedelta(minutes=5)

DeployMode = Literal["local", "signal"]
ZnunySyncState = Literal["not_needed", "pending", "ok", "error", "addon_missing"]


@dataclass(frozen=True, slots=True)
class AddonInfo:
    version: str
    capabilities: frozenset[str]
    seen_at: datetime  # naive UTC


class ZnunySyncOut(BaseModel):
    state: ZnunySyncState
    revision_id: int | None = None
    message: str = ""
    since: UtcDateTime | None = None
    overdue: bool = False  # pending longer than PENDING_WARN_AFTER


def _epoch_to_naive_utc(at: Any) -> datetime | None:
    if isinstance(at, bool) or not isinstance(at, int | float):
        return None
    try:
        return datetime.fromtimestamp(at, UTC).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def _naive_utc_to_epoch(moment: datetime) -> int:
    return calendar.timegm(moment.timetuple())


async def _read_json(session: AsyncSession, key: str) -> dict[str, Any] | None:
    raw = await get_setting(session, key)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        logger.warning("tiqorasync_key_unparsable", extra={"key": key})
        return None
    return value if isinstance(value, dict) else None


async def read_addon_info(session: AsyncSession) -> AddonInfo | None:
    data = await _read_json(session, HEARTBEAT_KEY)
    if data is None:
        return None
    seen_at = _epoch_to_naive_utc(data.get("at"))
    if seen_at is None:
        return None
    caps = data.get("capabilities")
    capabilities = frozenset(str(c) for c in caps) if isinstance(caps, list) else frozenset()
    return AddonInfo(
        version=str(data.get("version") or ""), capabilities=capabilities, seen_at=seen_at
    )


async def addon_can_deploy(session: AsyncSession, kind: RevisionKind, *, now: datetime) -> bool:
    """Fresh heartbeat (<= 10 minutes) of an add-on that has the ``<kind>_deploy`` capability."""
    info = await read_addon_info(session)
    if info is None or f"{kind}_deploy" not in info.capabilities:
        return False
    # A clock slightly ahead of ours still counts as a live add-on; one further ahead than the
    # max age would otherwise stay "fresh" forever.
    return -HEARTBEAT_MAX_AGE <= now - info.seen_at <= HEARTBEAT_MAX_AGE


async def _upsert_setting(session: AsyncSession, key: str, value: str) -> None:
    """Upsert without committing (the caller's deploy transaction owns the commit)."""
    existing = (
        await session.execute(select(TiqoraSettings).where(TiqoraSettings.key == key))
    ).scalar_one_or_none()
    if existing is not None:
        existing.value = value
    else:
        session.add(TiqoraSettings(key=key, value=value))
    await session.flush()


async def queue_deploy_signal(session: AsyncSession, kind: RevisionKind, revision_id: int) -> int:
    """Queue ``TiqoraDeploy_<kind>_<revision_id>`` and remember its row id; returns the id.

    Flush only; the caller commits. The cache type matches ``[A-Za-z0-9_]``, <= 100 chars.
    """
    result = await session.execute(
        insert(TiqoraCacheInvalidation).values(
            ticket_id=None, cache_type=f"TiqoraDeploy_{kind}_{revision_id}", created=utc_now()
        )
    )
    pk = cast("CursorResult[Any]", result).inserted_primary_key
    if pk is None:  # pragma: no cover - single-row insert always has a key
        raise RuntimeError("cache invalidation insert returned no id")
    signal_id = int(pk[0])
    await _upsert_setting(
        session,
        DEPLOY_REQUESTED_KEY[kind],
        json.dumps(
            {
                "signal_id": signal_id,
                "revision_id": revision_id,
                "at": _naive_utc_to_epoch(utc_now()),
            }
        ),
    )
    return signal_id


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


async def read_znuny_sync_status(
    session: AsyncSession,
    kind: RevisionKind,
    *,
    newest: RevisionInfo | None,
    mode: DeployMode | None,
    now: datetime,
) -> ZnunySyncOut:
    """Where Tiqora's last signalled deploy stands in Znuny.

    ``not_needed``: no revision, ``mode == "local"`` or no signal was ever queued.
    ``ok``/``error``: the add-on reported a status whose ``signal_id`` is at least the last
    queued one (the status of an older signal means ``pending``).
    ``addon_missing``: still unreported and there is no fresh heartbeat (``mode`` is ``None``).
    """
    if newest is None or mode == "local":
        return ZnunySyncOut(state="not_needed")
    requested = await _read_json(session, DEPLOY_REQUESTED_KEY[kind])
    requested_id = _as_int(requested.get("signal_id")) if requested else None
    if requested is None or requested_id is None:
        return ZnunySyncOut(state="not_needed")
    revision_id = _as_int(requested.get("revision_id"))
    since = _epoch_to_naive_utc(requested.get("at"))

    status = await _read_json(session, DEPLOY_STATUS_KEY[kind])
    status_id = _as_int(status.get("signal_id")) if status else None
    if status is not None and status_id is not None and status_id >= requested_id:
        reported = _as_int(status.get("revision_id"))
        if status.get("status") == "ok":
            return ZnunySyncOut(
                state="ok",
                revision_id=reported if reported is not None else revision_id,
                since=_epoch_to_naive_utc(status.get("at")),
            )
        return ZnunySyncOut(
            state="error",
            revision_id=reported if reported is not None else revision_id,
            message=str(status.get("message") or ""),
            since=_epoch_to_naive_utc(status.get("at")),
        )
    if mode is None:
        return ZnunySyncOut(state="addon_missing", revision_id=revision_id, since=since)
    overdue = since is not None and now - since > PENDING_WARN_AFTER
    return ZnunySyncOut(state="pending", revision_id=revision_id, since=since, overdue=overdue)
