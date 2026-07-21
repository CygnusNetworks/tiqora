"""Admin API for ticket subject-hook configuration.

``GET/PUT /api/v1/admin/subject-config`` — Tiqora overrides in
``tiqora_settings`` only (never writes Znuny SysConfig). Effective values
default to live Znuny ``Ticket::Hook`` / ``Ticket::HookDivider`` /
``Ticket::SubjectFormat`` so parallel-operation threading stays consistent.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.schemas import (
    SubjectConfigOut,
    SubjectConfigUpdate,
    SubjectHookOverridesOut,
    SubjectHookZnunyOut,
)
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.subject_hook import (
    KEY_SUBJECT_FORMAT,
    KEY_SUBJECT_HOOK,
    KEY_SUBJECT_HOOK_DIVIDER,
    KEY_SUBJECT_HOOK_ENABLED,
    load_subject_config,
    load_znuny_subject_baseline,
)
from tiqora.znuny.sysconfig import SysConfig

router = APIRouter(prefix="/subject-config", tags=["admin:subject-config"])

_VALID_FORMATS = frozenset({"Left", "Right", "None"})

_KEY_MAP = {
    "enabled": KEY_SUBJECT_HOOK_ENABLED,
    "hook": KEY_SUBJECT_HOOK,
    "divider": KEY_SUBJECT_HOOK_DIVIDER,
    "subject_format": KEY_SUBJECT_FORMAT,
}


async def _get_raw_overrides(session: DbSession) -> dict[str, str | None]:
    keys = list(_KEY_MAP.values())
    rows = (
        (await session.execute(select(TiqoraSettings).where(TiqoraSettings.key.in_(keys))))
        .scalars()
        .all()
    )
    by_key = {r.key: r.value for r in rows}
    return {name: by_key.get(key) for name, key in _KEY_MAP.items()}


def _parse_enabled_override(raw: str | None) -> bool | None:
    if raw is None or raw.strip() == "":
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


async def _build_out(session: DbSession) -> SubjectConfigOut:
    sysconfig = SysConfig(session)
    effective = await load_subject_config(session, sysconfig)
    znuny_hook, znuny_divider, znuny_format = await load_znuny_subject_baseline(sysconfig)
    raw = await _get_raw_overrides(session)
    overrides = SubjectHookOverridesOut(
        enabled=_parse_enabled_override(raw.get("enabled")),
        hook=raw.get("hook") if raw.get("hook") not in (None, "") else None,
        divider=raw.get("divider") if raw.get("divider") not in (None, "") else None,
        subject_format=(
            raw.get("subject_format") if raw.get("subject_format") not in (None, "") else None
        ),
    )
    return SubjectConfigOut(
        enabled=effective.enabled,
        hook=effective.hook,
        divider=effective.divider,
        subject_format=effective.subject_format,
        overrides=overrides,
        znuny=SubjectHookZnunyOut(
            hook=znuny_hook,
            divider=znuny_divider,
            subject_format=znuny_format,
        ),
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


@router.get("", response_model=SubjectConfigOut)
async def get_subject_config(admin: AdminUser, session: DbSession) -> SubjectConfigOut:
    _ = admin
    return await _build_out(session)


@router.put("", response_model=SubjectConfigOut)
async def put_subject_config(
    body: SubjectConfigUpdate, admin: AdminUser, session: DbSession
) -> SubjectConfigOut:
    """Upsert the four Tiqora override keys. Empty string / null clears."""
    _ = admin
    data = body.model_dump(exclude_unset=True)

    if "subject_format" in data:
        fmt = data["subject_format"]
        if fmt is not None and str(fmt).strip() != "":
            candidate = str(fmt).strip()
            canonical = None
            for allowed in _VALID_FORMATS:
                if candidate.lower() == allowed.lower():
                    canonical = allowed
                    break
            if canonical is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="subject_format must be one of: Left, Right, None",
                )
            data["subject_format"] = canonical

    if "enabled" in data:
        enabled = data["enabled"]
        if enabled is None:
            await _delete_setting(session, KEY_SUBJECT_HOOK_ENABLED)
        else:
            await _upsert_setting(session, KEY_SUBJECT_HOOK_ENABLED, "1" if enabled else "0")

    if "hook" in data:
        hook = data["hook"]
        if hook is None or str(hook).strip() == "":
            await _delete_setting(session, KEY_SUBJECT_HOOK)
        else:
            await _upsert_setting(session, KEY_SUBJECT_HOOK, str(hook).strip())

    if "divider" in data:
        divider = data["divider"]
        # Empty / null clears (re-inherit Znuny). Non-empty stores override.
        if divider is None or divider == "":
            await _delete_setting(session, KEY_SUBJECT_HOOK_DIVIDER)
        else:
            await _upsert_setting(session, KEY_SUBJECT_HOOK_DIVIDER, str(divider))

    if "subject_format" in data:
        fmt = data["subject_format"]
        if fmt is None or str(fmt).strip() == "":
            await _delete_setting(session, KEY_SUBJECT_FORMAT)
        else:
            await _upsert_setting(session, KEY_SUBJECT_FORMAT, str(fmt).strip())

    await session.commit()
    return await _build_out(session)
