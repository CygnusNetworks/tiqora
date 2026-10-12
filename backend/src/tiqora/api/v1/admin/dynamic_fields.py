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

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import (
    DYNAMIC_FIELD_CACHE_TYPES,
    invalidate_znuny_cache_types,
    now,
)
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParamsDep, Page, apply_valid_filter, window
from tiqora.api.v1.admin.schemas import (
    DynamicFieldCreate,
    DynamicFieldOut,
    DynamicFieldUpdate,
)
from tiqora.db.legacy.dynamic_field import DynamicField
from tiqora.domain.dynamic_field_config import (
    config_from_yaml,
    config_to_yaml,
    validate_dynamic_field_config,
)
from tiqora.domain.znuny_admin_writes import create_dynamic_field_row

router = APIRouter(prefix="/dynamic-fields", tags=["admin:dynamic-fields"])


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
        row = await create_dynamic_field_row(
            session,
            name=body.name,
            label=body.label,
            field_type=body.field_type,
            object_type=body.object_type,
            config=body.config,
            field_order=body.field_order,
            valid_id=body.valid_id,
            user_id=admin.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
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
    await invalidate_znuny_cache_types(session, DYNAMIC_FIELD_CACHE_TYPES)
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
    await invalidate_znuny_cache_types(session, DYNAMIC_FIELD_CACHE_TYPES)
    await session.commit()


__all__ = ["router", "validate_dynamic_field_config", "config_to_yaml", "config_from_yaml"]
