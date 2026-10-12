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
from pydantic import ValidationError
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
    DYNAMIC_FIELD_TYPES,
    DynamicFieldCreate,
    DynamicFieldOut,
    DynamicFieldPluginTypeOut,
    DynamicFieldUpdate,
)
from tiqora.db.legacy.dynamic_field import DynamicField
from tiqora.domain.dynamic_field_config import config_from_yaml
from tiqora.extensions.core import get_registry
from tiqora.plugins.df_types import (
    DYNAMIC_FIELD_TYPES as PLUGIN_DF_TYPES,
)
from tiqora.plugins.df_types import (
    PLUGIN_CONFIG_KEY,
    PLUGIN_TYPE_CONFIG_KEY,
    DynamicFieldType,
)
from tiqora.plugins.state import is_active

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


_PLUGIN_KEYS = (PLUGIN_TYPE_CONFIG_KEY, PLUGIN_CONFIG_KEY)


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _active_plugin_types() -> list[DynamicFieldType]:
    return [t for t in get_registry().items(PLUGIN_DF_TYPES) if is_active(t.plugin_id)]


def _validated_plugin_settings(item: DynamicFieldType, raw: Any) -> dict[str, Any]:
    """``TiqoraPluginConfig`` checked against the type's ``config_model`` (German 422)."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise _unprocessable("Einstellungen der Erweiterung: erwartet wird ein Objekt")
    if item.config_model is None:
        if raw:
            raise _unprocessable(
                "Einstellungen der Erweiterung: dieser Feldtyp hat keine Einstellungen"
            )
        return {}
    try:
        # JSON mode: the settings go to YAML, which cannot hold Enum/Decimal/UUID/SecretStr
        return item.config_model.model_validate(raw).model_dump(mode="json")
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'Wert'}: {err['msg']}"
            for err in exc.errors()
        )
        raise _unprocessable(f"Einstellungen der Erweiterung: {problems}"[:500]) from exc


def _core_config(field_type: str, config: dict[str, Any]) -> None:
    """Validate the Znuny part of ``config`` (the plugin keys are checked separately)."""
    try:
        validate_dynamic_field_config(
            field_type, {k: v for k, v in config.items() if k not in _PLUGIN_KEYS}
        )
    except ValueError as exc:
        raise _unprocessable(str(exc)) from exc


def _to_out(row: DynamicField) -> DynamicFieldOut:
    config = config_from_yaml(row.config)
    plugin_type = config.get(PLUGIN_TYPE_CONFIG_KEY)
    return DynamicFieldOut(
        id=row.id,
        internal_field=row.internal_field,
        name=row.name,
        label=row.label,
        field_order=row.field_order,
        field_type=row.field_type,
        object_type=row.object_type,
        config=config,
        plugin_type=plugin_type if isinstance(plugin_type, str) else None,
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


@router.get("/plugin-types", response_model=list[DynamicFieldPluginTypeOut])
async def list_plugin_types(admin: AdminUser) -> list[DynamicFieldPluginTypeOut]:
    """Field types plugins offer (registered by a loaded plugin), for the create dialog."""
    _ = admin
    return [
        DynamicFieldPluginTypeOut(
            key=t.key,
            label=t.label,
            base_type=t.base_type,
            plugin_id=t.plugin_id,
            config_schema=t.config_model.model_json_schema() if t.config_model else None,
        )
        for t in sorted(_active_plugin_types(), key=lambda t: t.key)
    ]


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
    config = dict(body.config)
    if body.plugin_type is not None:
        # D8 / F10: the plugin keys exist only on plugin-typed fields and are set here, not
        # taken from the body; the field is stored as the type's base type
        item = next((t for t in _active_plugin_types() if t.key == body.plugin_type), None)
        if item is None:
            raise _unprocessable(
                f"Der Feldtyp „{body.plugin_type}“ ist nicht verfügbar "
                "(Erweiterung nicht geladen oder Typ unbekannt)"
            )
        if body.object_type not in ("Ticket", "Article"):
            raise _unprocessable("Feldtypen von Erweiterungen gibt es nur für Ticket und Artikel")
        if body.field_type != item.base_type:
            raise _unprocessable(
                f"Der Feldtyp „{item.key}“ wird als „{item.base_type}“ gespeichert; "
                f"field_type „{body.field_type}“ passt nicht"
            )
        if PLUGIN_TYPE_CONFIG_KEY in config:
            raise _unprocessable("TiqoraPluginType wird über plugin_type gesetzt")
        settings = _validated_plugin_settings(item, config.pop(PLUGIN_CONFIG_KEY, None))
        _core_config(item.base_type, config)
        config[PLUGIN_TYPE_CONFIG_KEY] = item.key
        config[PLUGIN_CONFIG_KEY] = settings
    else:
        _core_config(body.field_type, config)
        if any(k in config for k in _PLUGIN_KEYS):
            raise _unprocessable(
                "TiqoraPluginType und TiqoraPluginConfig gibt es nur bei Feldern mit plugin_type"
            )

    ts = now()
    row = DynamicField(
        internal_field=0,
        name=body.name,
        label=body.label,
        field_order=body.field_order,
        field_type=body.field_type,
        object_type=body.object_type,
        config=config_to_yaml(config),
        valid_id=body.valid_id,
        create_time=ts,
        create_by=admin.id,
        change_time=ts,
        change_by=admin.id,
    )
    session.add(row)
    await invalidate_znuny_cache_types(session, DYNAMIC_FIELD_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


def _patched_config(row: DynamicField, incoming: dict[str, Any]) -> dict[str, Any]:
    """The new config of a PATCH: the stored plugin type (and settings, unless sent) stay."""
    stored = config_from_yaml(row.config)
    stored_type = stored.get(PLUGIN_TYPE_CONFIG_KEY)
    config = dict(incoming)
    if not isinstance(stored_type, str) or not stored_type:
        if any(k in config for k in _PLUGIN_KEYS):
            raise _unprocessable(
                "TiqoraPluginType und TiqoraPluginConfig gibt es nur bei Feldern mit plugin_type"
            )
        _core_config(row.field_type, config)
        return config
    if config.setdefault(PLUGIN_TYPE_CONFIG_KEY, stored_type) != stored_type:
        raise _unprocessable("Der Feldtyp einer Erweiterung kann nicht geändert werden")
    _core_config(row.field_type, config)
    item = next((t for t in _active_plugin_types() if t.key == stored_type), None)
    if PLUGIN_CONFIG_KEY not in config:
        if PLUGIN_CONFIG_KEY in stored:
            config[PLUGIN_CONFIG_KEY] = stored[PLUGIN_CONFIG_KEY]
    elif item is not None:
        config[PLUGIN_CONFIG_KEY] = _validated_plugin_settings(item, config[PLUGIN_CONFIG_KEY])
    # a type whose plugin is not loaded cannot be checked: the settings are kept as sent
    return config


@router.patch("/{field_id}", response_model=DynamicFieldOut)
async def update_dynamic_field(
    field_id: int, body: DynamicFieldUpdate, admin: AdminUser, session: DbSession
) -> DynamicFieldOut:
    row = await session.get(DynamicField, field_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dynamic field not found")

    data = body.model_dump(exclude_unset=True)
    if "config" in data and data["config"] is not None:
        row.config = config_to_yaml(_patched_config(row, data.pop("config")))
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
