"""Read/write a settings section from/to ``tiqora_settings``.

Writes only ``flush``; the caller owns the transaction (unlike
``tiqora.domain.settings_store.set_setting``, which commits by itself).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.crypto.secret import decrypt_secret, encrypt_secret
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.settings_store import SETTING_TRUTHY
from tiqora.settings_sections.section import SECRET_MASK, SettingsSection, field_kind

log = structlog.get_logger(__name__)


class SettingsValidationError(ValueError):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__(f"invalid settings: {errors}")
        self.errors = errors


def encode_setting_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, list):
        return json.dumps([v.value if isinstance(v, Enum) else v for v in value])
    return str(value)


async def _rows(session: AsyncSession, section: SettingsSection) -> dict[str, str]:
    """field name -> raw stored (non-empty) value."""
    keys = {section.storage_key(name): name for name in section.model.model_fields}
    result = await session.execute(
        select(TiqoraSettings.key, TiqoraSettings.value).where(TiqoraSettings.key.in_(list(keys)))
    )
    return {keys[k]: v for k, v in result.all() if v is not None and v != ""}


def _decode(section: SettingsSection, name: str, raw: str, secret_key: str | None) -> Any:
    """Decode one stored row; raise ``ValueError`` when it cannot be used."""
    kind = field_kind(section.model.model_fields[name].annotation)
    if kind == "bool":
        return raw.strip().lower() in SETTING_TRUTHY
    if kind == "list":
        decoded = json.loads(raw)
        if not isinstance(decoded, list):
            raise ValueError("not a list")
        return decoded
    if kind == "secret":
        if secret_key is None:
            raise ValueError("secrets are not decrypted here")
        plain = decrypt_secret(secret_key, raw)
        if plain is None:
            raise ValueError("undecryptable secret")
        return plain
    return raw


def _validate_lenient(section: SettingsSection, decoded: dict[str, Any]) -> BaseModel:
    """Validate; a failing field falls back to its default, others are kept."""
    values = dict(decoded)
    for _ in range(len(section.model.model_fields) + 1):
        try:
            return section.model.model_validate(values)
        except ValidationError as exc:
            bad = {str(e["loc"][0]) for e in exc.errors() if e["loc"]}
            bad &= values.keys()
            if not bad:
                raise
            for name in bad:
                log.warning("settings_value_invalid", section=section.key, field=name)
                del values[name]
    return section.model.model_validate({})


async def _load(
    session: AsyncSession, section: SettingsSection, secret_key: str | None
) -> BaseModel:
    decoded: dict[str, Any] = {}
    for name, raw in (await _rows(session, section)).items():
        try:
            decoded[name] = _decode(section, name, raw, secret_key)
        except ValueError as exc:  # includes json.JSONDecodeError
            if secret_key is not None or name not in section.secret_fields():
                log.warning(
                    "settings_value_invalid", section=section.key, field=name, reason=str(exc)
                )
    return _validate_lenient(section, decoded)


async def load_section_values(
    session: AsyncSession, section: SettingsSection, *, secret_key: str
) -> BaseModel:
    return await _load(session, section, secret_key)


async def read_section_for_admin(
    session: AsyncSession, section: SettingsSection
) -> tuple[dict[str, Any], list[str]]:
    secrets = section.secret_fields()
    model = await _load(session, section, None)
    values = model.model_dump(mode="json", exclude=set(secrets))
    stored = await _rows(session, section)
    secrets_set = [n for n in section.model.model_fields if n in secrets and n in stored]
    for name in secrets:
        values[name] = SECRET_MASK if name in stored else None
    return values, secrets_set


async def save_section_values(
    session: AsyncSession,
    section: SettingsSection,
    changes: Mapping[str, Any],
    *,
    secret_key: str,
) -> None:
    fields = section.model.model_fields
    unknown = {name: "Unbekanntes Feld" for name in changes if name not in fields}
    if unknown:
        raise SettingsValidationError(unknown)

    secrets = section.secret_fields()
    current = await _load(session, section, secret_key)
    merged: dict[str, Any] = {name: getattr(current, name) for name in fields}
    touched: list[str] = []
    for name, value in changes.items():
        if name in secrets:
            if value == SECRET_MASK:
                continue
            if value == "":
                value = None
        merged[name] = value
        touched.append(name)

    try:
        validated = section.model.model_validate(merged)
    except ValidationError as exc:
        errors: dict[str, str] = {}
        for err in exc.errors():
            if err["loc"]:
                errors.setdefault(str(err["loc"][0]), err["msg"])
        raise SettingsValidationError(errors) from exc

    for name in touched:
        key = section.storage_key(name)
        value = getattr(validated, name)
        row = await session.get(TiqoraSettings, key)
        if value is None:
            if row is not None:
                await session.delete(row)
            continue
        encoded = (
            encrypt_secret(secret_key, value.get_secret_value())
            if name in secrets
            else encode_setting_value(value)
        )
        if row is None:
            session.add(TiqoraSettings(key=key, value=encoded))
        else:
            row.value = encoded
    await session.flush()
