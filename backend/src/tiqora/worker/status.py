"""Daemon tick status tracking — ``daemon.<slug>.status.*`` tiqora_settings keys.

Every takeover loop in ``tiqora.worker.__main__`` calls ``record_tick_status``
after each tick so the admin "Dienste" page (``/admin/daemons``) can show a
live health badge per service, independent of container logs. Writing status
must never be allowed to kill a loop or mask the tick's own exception — see
the outer try/except below.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.engine import get_session_factory
from tiqora.db.tiqora.models import TiqoraSettings

logger = structlog.get_logger(__name__)


def _status_keys(service: str) -> tuple[str, str, str, str]:
    base = f"daemon.{service}.status"
    return f"{base}.last_run", f"{base}.last_ok", f"{base}.last_error", f"{base}.last_result"


async def _upsert(session: AsyncSession, key: str, value: str | None) -> None:
    """Upsert or (when ``value`` is None) delete one tiqora_settings row."""
    existing = (
        await session.execute(select(TiqoraSettings).where(TiqoraSettings.key == key))
    ).scalar_one_or_none()
    if value is None:
        if existing is not None:
            await session.delete(existing)
        return
    if existing is not None:
        existing.value = value
    else:
        session.add(TiqoraSettings(key=key, value=value))


async def record_tick_status(
    service: str,
    *,
    ok: bool,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Record one tick's outcome for ``service`` in a single commit.

    Best-effort and side-channel: any DB failure here is logged and swallowed
    so a status write can never kill the loop it instruments, nor mask the
    tick's own success/failure (already decided by the caller before this is
    called). ``last_error`` is cleared on a successful tick; ``last_ok`` is
    only advanced on success, so a stuck-failing service keeps its last-good
    timestamp visible.
    """
    last_run_key, last_ok_key, last_error_key, last_result_key = _status_keys(service)
    now = datetime.now(UTC).isoformat()
    factory = session_factory or get_session_factory()

    try:
        async with factory() as session, session.begin():
            await _upsert(session, last_run_key, now)
            if ok:
                await _upsert(session, last_ok_key, now)
                await _upsert(session, last_error_key, None)
            elif error is not None:
                await _upsert(session, last_error_key, error)
            await _upsert(
                session, last_result_key, json.dumps(result) if result is not None else None
            )
    except Exception:  # noqa: BLE001 — status tracking must never kill the loop
        logger.exception("record_tick_status_failed", service=service)


def seconds_until_daily(at_hhmm: str, *, now: datetime | None = None) -> float:
    """Seconds until the next UTC occurrence of ``HH:MM`` (today or tomorrow).

    Pure function (no I/O) so the daily loop in ``worker/__main__.py`` can
    recompute after every wake-up without special-casing "just missed it".
    """
    hour_str, minute_str = at_hhmm.split(":", 1)
    hour, minute = int(hour_str), int(minute_str)
    current = now if now is not None else datetime.now(UTC)
    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= current:
        candidate += timedelta(days=1)
    return (candidate - current).total_seconds()
