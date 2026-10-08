"""Znuny session purge: removes expired ``sessions`` rows (Znuny Maint::Session::DeleteExpired).

Always on. Znuny's own daemon may run the same cleanup; running both is harmless.
"""

from __future__ import annotations

import time

import structlog

from tiqora.db.engine import get_session_factory
from tiqora.znuny.sessions import purge_expired_sessions
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)


async def run_znuny_session_purge_tick() -> dict[str, int]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            deleted = await purge_expired_sessions(
                session, now=int(time.time()), sysconfig=SysConfig(session)
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    logger.info("znuny_session_purge_tick", deleted=deleted)
    return {"deleted": deleted}
