"""Admin API for the "Dienste" page — ``/api/v1/admin/daemons``.

Read/write layer over ``tiqora.worker.services.DAEMON_SERVICES`` (the single
source of truth for slugs/keys/defaults) plus the live tick status written by
``tiqora.worker.status.record_tick_status``. Tiqora overrides live in
``tiqora_settings`` only — this never touches Znuny SysConfig.
"""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.schemas import DaemonListOut, DaemonServiceOut, DaemonUpdate
from tiqora.config import get_settings
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.worker.services import DAEMON_SERVICES, DAEMON_SERVICES_BY_SLUG, DaemonService

router = APIRouter(prefix="/daemons", tags=["admin:daemons"])

_MIN_INTERVAL_SECONDS = 5


def _all_keys() -> list[str]:
    keys: list[str] = []
    for svc in DAEMON_SERVICES:
        if svc.enabled_key:
            keys.append(svc.enabled_key)
        if svc.interval_key:
            keys.append(svc.interval_key)
        base = f"daemon.{svc.slug}.status"
        keys += [f"{base}.last_run", f"{base}.last_ok", f"{base}.last_error", f"{base}.last_result"]
    return keys


async def _raw_settings(session: DbSession) -> dict[str, str]:
    rows = (
        (await session.execute(select(TiqoraSettings).where(TiqoraSettings.key.in_(_all_keys()))))
        .scalars()
        .all()
    )
    return {r.key: r.value for r in rows if r.value is not None}


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _to_out(svc: DaemonService, raw: dict[str, str]) -> DaemonServiceOut:
    settings = get_settings()

    if svc.enabled_key is None:
        enabled = True
    else:
        raw_enabled = raw.get(svc.enabled_key)
        if raw_enabled is None or raw_enabled == "":
            enabled = svc.default_enabled
        else:
            enabled = raw_enabled.strip().lower() in ("1", "true", "yes", "on")

    interval_seconds: int | None = None
    interval_overridden = False
    if svc.schedule_kind == "interval":
        assert svc.interval_settings_attr is not None
        default_interval = int(getattr(settings, svc.interval_settings_attr))
        override_raw = raw.get(svc.interval_key) if svc.interval_key else None
        if override_raw is not None:
            try:
                interval_seconds = max(_MIN_INTERVAL_SECONDS, int(override_raw))
                interval_overridden = True
            except ValueError:
                interval_seconds = max(_MIN_INTERVAL_SECONDS, default_interval)
        else:
            interval_seconds = max(_MIN_INTERVAL_SECONDS, default_interval)

    status_base = f"daemon.{svc.slug}.status"
    last_result_raw = raw.get(f"{status_base}.last_result")
    last_result = None
    if last_result_raw:
        try:
            last_result = json.loads(last_result_raw)
        except ValueError:
            last_result = None

    return DaemonServiceOut(
        slug=svc.slug,
        enabled=enabled,
        toggleable=svc.toggleable,
        schedule=svc.schedule_kind,
        interval_seconds=interval_seconds,
        interval_overridden=interval_overridden,
        daily_at=svc.daily_at,
        last_run_at=_parse_dt(raw.get(f"{status_base}.last_run")),
        last_ok_at=_parse_dt(raw.get(f"{status_base}.last_ok")),
        last_error=raw.get(f"{status_base}.last_error"),
        last_result=last_result,
    )


async def _upsert_setting(session: DbSession, key: str, value: str) -> None:
    existing = (
        await session.execute(select(TiqoraSettings).where(TiqoraSettings.key == key))
    ).scalar_one_or_none()
    if existing is not None:
        existing.value = value
    else:
        session.add(TiqoraSettings(key=key, value=value))


async def _delete_setting(session: DbSession, key: str) -> None:
    existing = (
        await session.execute(select(TiqoraSettings).where(TiqoraSettings.key == key))
    ).scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)


@router.get("", response_model=DaemonListOut)
async def list_daemons(admin: AdminUser, session: DbSession) -> DaemonListOut:
    _ = admin
    raw = await _raw_settings(session)
    return DaemonListOut(services=[_to_out(svc, raw) for svc in DAEMON_SERVICES])


@router.put("/{slug}", response_model=DaemonServiceOut)
async def update_daemon(
    slug: str, body: DaemonUpdate, admin: AdminUser, session: DbSession
) -> DaemonServiceOut:
    _ = admin
    svc = DAEMON_SERVICES_BY_SLUG.get(slug)
    if svc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown daemon: {slug}")

    data = body.model_dump(exclude_unset=True)

    if "enabled" in data and data["enabled"] is not None:
        if not svc.toggleable:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{slug} cannot be toggled (always on)",
            )
        assert svc.enabled_key is not None
        await _upsert_setting(session, svc.enabled_key, "1" if data["enabled"] else "0")

    if "interval_seconds" in data:
        value = data["interval_seconds"]
        if svc.schedule_kind != "interval" or svc.interval_key is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{slug} has no editable interval",
            )
        if value is None or value == 0:
            await _delete_setting(session, svc.interval_key)
        elif value < _MIN_INTERVAL_SECONDS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"interval_seconds must be >= {_MIN_INTERVAL_SECONDS}",
            )
        else:
            await _upsert_setting(session, svc.interval_key, str(value))

    await session.commit()
    raw = await _raw_settings(session)
    return _to_out(svc, raw)
