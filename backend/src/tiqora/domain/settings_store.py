"""Key/value helpers for ``tiqora_settings`` (watermarks, index progress)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraSettings

# Watermark keys used by indexer / poller
KEY_HISTORY_WATERMARK = "poller.ticket_history.max_id"
KEY_ARTICLE_WATERMARK = "poller.article.max_id"
KEY_INDEX_REBUILD_WATERMARK = "index.rebuild.ticket_id"
KEY_INDEX_REBUILD_STATUS = "index.rebuild.status"

# Postmaster (Phase 4a) feature flags — daemon takeover switches. Default OFF:
# Znuny's own daemon task (Daemon::SchedulerCronTaskManager::Task###MailAccountFetch)
# must remain the sole mail-fetching path until an operator flips these keys.
# See docs/parallel-operation.md → "Taking over mail processing".
KEY_POSTMASTER_ENABLED = "daemon.postmaster.enabled"
KEY_POSTMASTER_LEAVE_ON_SERVER = "daemon.postmaster.leave_on_server"
KEY_POSTMASTER_INTERVAL_SECONDS = "daemon.postmaster.interval_seconds"


async def get_setting_bool(session: AsyncSession, key: str, default: bool = False) -> bool:
    raw = await get_setting(session, key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


async def get_setting(session: AsyncSession, key: str) -> str | None:
    result = await session.execute(select(TiqoraSettings.value).where(TiqoraSettings.key == key))
    return result.scalar_one_or_none()


async def get_setting_int(session: AsyncSession, key: str, default: int = 0) -> int:
    raw = await get_setting(session, key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    """Upsert a setting value (works on PostgreSQL and MySQL/MariaDB)."""
    existing = (
        await session.execute(select(TiqoraSettings).where(TiqoraSettings.key == key))
    ).scalar_one_or_none()
    if existing is not None:
        existing.value = value
    else:
        session.add(TiqoraSettings(key=key, value=value))
    await session.commit()
