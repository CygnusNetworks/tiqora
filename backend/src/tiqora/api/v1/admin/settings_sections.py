"""Generic admin API for settings sections, mounted at
``/api/v1/admin/settings-sections``."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.extensions import get_registry
from tiqora.settings_sections.section import SETTINGS_SECTIONS, SettingsSection
from tiqora.settings_sections.store import (
    SettingsValidationError,
    read_section_for_admin,
    save_section_values,
)

router = APIRouter(prefix="/settings-sections", tags=["admin:settings"])


class SettingsSectionSummaryOut(BaseModel):
    key: str
    title_key: str
    owner: str


class SettingsSectionOut(BaseModel):
    key: str
    title_key: str
    i18n_prefix: str
    json_schema: dict[str, Any]
    values: dict[str, Any]
    secrets_set: list[str]


class SettingsSectionUpdate(BaseModel):
    values: dict[str, Any]


def _section_or_404(key: str) -> SettingsSection:
    section = get_registry().get(SETTINGS_SECTIONS, key)
    if section is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Einstellungen nicht gefunden")
    return section


async def _out(session: DbSession, section: SettingsSection) -> SettingsSectionOut:
    values, secrets_set = await read_section_for_admin(session, section)
    return SettingsSectionOut(
        key=section.key,
        title_key=section.title_key,
        i18n_prefix=section.i18n_prefix,
        json_schema=section.json_schema(),
        values=values,
        secrets_set=secrets_set,
    )


@router.get("", response_model=list[SettingsSectionSummaryOut])
async def list_settings_sections(admin: AdminUser) -> list[SettingsSectionSummaryOut]:
    _ = admin
    return [
        SettingsSectionSummaryOut(key=r.key, title_key=r.item.title_key, owner=r.owner)
        for r in get_registry().registrations(SETTINGS_SECTIONS)
    ]


@router.get("/{key}", response_model=SettingsSectionOut)
async def get_settings_section(
    key: str, admin: AdminUser, session: DbSession
) -> SettingsSectionOut:
    _ = admin
    return await _out(session, _section_or_404(key))


@router.put("/{key}", response_model=SettingsSectionOut)
async def put_settings_section(
    key: str,
    body: SettingsSectionUpdate,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
) -> SettingsSectionOut:
    _ = admin
    section = _section_or_404(key)
    try:
        await save_section_values(session, section, body.values, secret_key=settings.secret_key)
    except SettingsValidationError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Bitte die markierten Felder prüfen.", "errors": exc.errors},
        ) from exc
    await session.commit()
    return await _out(session, section)
