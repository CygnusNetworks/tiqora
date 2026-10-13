"""Admin API for runtime plugin packages: coordinated restart (D11).

Package upload, install and uninstall endpoints join this router in later tasks. The
restart routes live here so ``POST /plugins/restart`` is registered next to
``POST /plugins/{plugin_id}/uninstall`` (different suffix, no path clash).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.db.tiqora.models import TiqoraRestart
from tiqora.plugins.restart import (
    BUSY_MESSAGE,
    RestartStatusOut,
    plugin_job_active,
    request_restart,
    restart_status,
    utcnow,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/plugins", tags=["admin:plugins"])

DEFAULT_REASON: Final = "Manuell angefordert"
RUNNING_MESSAGE: Final = "Ein Neustart läuft bereits"
MANUAL_MESSAGE: Final = (
    "Der koordinierte Neustart ist abgeschaltet. Bitte starten Sie Tiqora selbst neu."
)
#: an unfinished request older than this many timeouts is stuck and may be superseded
STUCK_AFTER_TIMEOUTS: Final = 4


class RestartIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


@router.get("/restart", response_model=RestartStatusOut | None)
async def get_restart(
    admin: AdminUser, session: DbSession, settings: AppSettings
) -> RestartStatusOut | None:
    _ = admin
    return await restart_status(
        session,
        now=utcnow(),
        coordinated=settings.plugins_coordinated_restart,
        timeout_seconds=settings.plugins_restart_timeout_seconds,
    )


@router.post("/restart", response_model=RestartStatusOut, status_code=status.HTTP_201_CREATED)
async def post_restart(
    body: RestartIn, admin: AdminUser, session: DbSession, settings: AppSettings
) -> RestartStatusOut:
    if not settings.plugins_coordinated_restart:
        raise HTTPException(status.HTTP_409_CONFLICT, MANUAL_MESSAGE)
    if await plugin_job_active(session):
        raise HTTPException(status.HTTP_409_CONFLICT, BUSY_MESSAGE)
    now = utcnow()
    open_requested = (
        (
            await session.execute(
                select(TiqoraRestart.requested_at).where(
                    TiqoraRestart.finished_at.is_(None), TiqoraRestart.failed_message.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    stuck_before = now - timedelta(
        seconds=STUCK_AFTER_TIMEOUTS * settings.plugins_restart_timeout_seconds
    )
    if any(requested >= stuck_before for requested in open_requested):
        raise HTTPException(status.HTTP_409_CONFLICT, RUNNING_MESSAGE)
    generation = await request_restart(
        session,
        by=admin.id,
        reason=(body.reason or DEFAULT_REASON).strip() or DEFAULT_REASON,
        now=now,
        ensure_roles=("api",),
    )
    await session.commit()
    log.info("plugin_restart_requested", generation=generation, by=admin.id)
    out = await restart_status(
        session,
        now=now,
        coordinated=settings.plugins_coordinated_restart,
        timeout_seconds=settings.plugins_restart_timeout_seconds,
    )
    if out is None:  # pragma: no cover - the row was committed above
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Neustart nicht gefunden")
    return out
