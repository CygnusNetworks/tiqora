"""Admin CRUD for dynamic fields, with YAML config validation per field type.

Znuny stores ``dynamic_field.config`` as a YAML-serialized TEXT column, read
via ``YAML::Load`` in Perl. We must
write the exact key names Znuny's drivers expect so existing Znuny UI code
keeps working against fields created here. Key names below are taken directly
from ``Kernel/System/DynamicField/Driver/*.pm`` in the Znuny 6.5.22 source
tree (``BaseText.pm``, ``TextArea.pm``, ``Checkbox.pm``, ``BaseSelect.pm``/
``Dropdown.pm``/``Multiselect.pm``, ``BaseDateTime.pm``).
"""

from __future__ import annotations

from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import now
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParamsDep, Page, apply_valid_filter, window
from tiqora.api.v1.admin.schemas import (
    DYNAMIC_FIELD_TYPES,
    DynamicFieldCreate,
    DynamicFieldOut,
    DynamicFieldUpdate,
)
from tiqora.db.legacy.dynamic_field import DynamicField

router = APIRouter(prefix="/dynamic-fields", tags=["admin:dynamic-fields"])


# Keys recognised (validated/allowed) per field_type. Extra unknown keys are
# rejected to avoid silently persisting typos that Znuny would ignore.
_COMMON_TEXT_KEYS = {"DefaultValue", "Link", "RegExList"}
_SELECT_KEYS = {"DefaultValue", "PossibleValues", "PossibleNone", "TranslatableValues", "Link"}
_DATETIME_KEYS = {"DefaultValue", "YearsPeriod", "YearsInPast", "YearsInFuture"}

_ALLOWED_KEYS: dict[str, set[str]] = {
    "Text": _COMMON_TEXT_KEYS,
    "TextArea": _COMMON_TEXT_KEYS | {"Rows", "Cols"},
    "Checkbox": {"DefaultValue"},
    "Dropdown": _SELECT_KEYS,
    "Multiselect": _SELECT_KEYS,
    "Date": _DATETIME_KEYS,
    "DateTime": _DATETIME_KEYS,
}

_REQUIRED_KEYS: dict[str, set[str]] = {
    "Dropdown": {"PossibleValues"},
    "Multiselect": {"PossibleValues"},
}


def validate_dynamic_field_config(field_type: str, config: dict[str, Any]) -> None:
    """Validate *config* keys/shape against Znuny's expectations for *field_type*.

    Raises :class:`ValueError` on an invalid field_type or config shape.
    """
    if field_type not in DYNAMIC_FIELD_TYPES:
        raise ValueError(
            f"Unsupported field_type {field_type!r}; expected one of {sorted(DYNAMIC_FIELD_TYPES)}"
        )
    allowed = _ALLOWED_KEYS[field_type]
    unknown = set(config) - allowed
    if unknown:
        raise ValueError(f"Unknown config keys for {field_type}: {sorted(unknown)}")
    required = _REQUIRED_KEYS.get(field_type, set())
    missing = required - set(config)
    if missing:
        raise ValueError(f"Missing required config keys for {field_type}: {sorted(missing)}")
    if field_type in {"Dropdown", "Multiselect"} and "PossibleValues" in config:
        pv = config["PossibleValues"]
        if not isinstance(pv, dict) or not pv:
            raise ValueError("PossibleValues must be a non-empty mapping of value -> label")


def config_to_yaml(config: dict[str, Any]) -> str:
    """Serialize *config* the way Znuny's ``YAML::Dump`` round-trips it.

    ``dynamic_field.config`` is a TEXT column on both dialects (see the
    :class:`~tiqora.db.legacy.dynamic_field.DynamicField` docstring), so this
    returns ``str``, not ``bytes``.
    """
    return yaml.safe_dump(config, default_flow_style=False, allow_unicode=True)


def config_from_yaml(raw: str | bytes | None) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, memoryview | bytearray):
        raw = bytes(raw)
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _to_out(row: DynamicField) -> DynamicFieldOut:
    return DynamicFieldOut(
        id=row.id,
        internal_field=row.internal_field,
        name=row.name,
        label=row.label,
        field_order=row.field_order,
        field_type=row.field_type,
        object_type=row.object_type,
        config=config_from_yaml(row.config),
        valid_id=row.valid_id,
        create_time=row.create_time,
        change_time=row.change_time,
    )


@router.get("", response_model=Page[DynamicFieldOut])
async def list_dynamic_fields(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[DynamicFieldOut]:
    _ = admin
    stmt = apply_valid_filter(select(DynamicField), DynamicField.valid_id, params.valid).order_by(
        DynamicField.field_order
    )
    rows, total = await window(session, stmt, params)
    return Page(
        items=[_to_out(r) for r in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get("/{field_id}", response_model=DynamicFieldOut)
async def get_dynamic_field(field_id: int, admin: AdminUser, session: DbSession) -> DynamicFieldOut:
    _ = admin
    row = await session.get(DynamicField, field_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dynamic field not found")
    return _to_out(row)


@router.post("", response_model=DynamicFieldOut, status_code=status.HTTP_201_CREATED)
async def create_dynamic_field(
    body: DynamicFieldCreate, admin: AdminUser, session: DbSession
) -> DynamicFieldOut:
    try:
        validate_dynamic_field_config(body.field_type, body.config)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    ts = now()
    row = DynamicField(
        internal_field=0,
        name=body.name,
        label=body.label,
        field_order=body.field_order,
        field_type=body.field_type,
        object_type=body.object_type,
        config=config_to_yaml(body.config),
        valid_id=body.valid_id,
        create_time=ts,
        create_by=admin.id,
        change_time=ts,
        change_by=admin.id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.patch("/{field_id}", response_model=DynamicFieldOut)
async def update_dynamic_field(
    field_id: int, body: DynamicFieldUpdate, admin: AdminUser, session: DbSession
) -> DynamicFieldOut:
    row = await session.get(DynamicField, field_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dynamic field not found")

    data = body.model_dump(exclude_unset=True)
    if "config" in data and data["config"] is not None:
        try:
            validate_dynamic_field_config(row.field_type, data["config"])
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        row.config = config_to_yaml(data.pop("config"))
    for field, value in data.items():
        setattr(row, field, value)
    row.change_time = now()
    row.change_by = admin.id
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.delete("/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_dynamic_field(field_id: int, admin: AdminUser, session: DbSession) -> None:
    """Soft-invalidate — dynamic field values referencing this field must survive."""
    row = await session.get(DynamicField, field_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dynamic field not found")
    row.valid_id = 2
    row.change_time = now()
    row.change_by = admin.id
    await session.commit()


__all__ = ["router", "validate_dynamic_field_config", "config_to_yaml", "config_from_yaml"]
