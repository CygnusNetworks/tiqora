"""Dynamic field types from plugins (D8).

A plugin type is stored as one of Znuny's base types (``Text``, ``Dropdown``, ...); the
plugin type's qualified name sits in the field's config YAML under ``TiqoraPluginType`` and
its own settings under ``TiqoraPluginConfig``. Znuny ignores both keys and keeps working with
the field. Core validates and displays through the plugin, but a plugin can never block a
save by crashing: a validator that raises anything but ``ValueError`` (or hangs, or returns
something unusable) is skipped and the value is stored like the base type would store it.
A plugin that is not loaded makes the field behave as its base type.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

import structlog
from pydantic import BaseModel

from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import ExtensionPoint
from tiqora.plugins.calls import call_plugin, call_plugin_sync
from tiqora.plugins.context import PluginContext
from tiqora.plugins.state import is_active

if TYPE_CHECKING:
    from tiqora.domain.dynamic_field_values import DynamicFieldConfig

logger = structlog.get_logger(__name__)

__all__ = [
    "BASE_TYPES",
    "CALL_TIMEOUT_SECONDS",
    "DYNAMIC_FIELD_TYPES",
    "PLUGIN_CONFIG_KEY",
    "PLUGIN_TYPE_CONFIG_KEY",
    "DfBaseType",
    "DfDisplay",
    "DfValidate",
    "DynamicFieldType",
    "display_plugin_value",
    "display_value_bounded",
    "plugin_type_of",
    "validate_plugin_values",
]

DfBaseType = Literal["Text", "TextArea", "Dropdown", "Multiselect", "Checkbox", "Date", "DateTime"]
#: ``Password`` is not offered: its value must never reach plugin code
BASE_TYPES: Final = ("Text", "TextArea", "Dropdown", "Multiselect", "Checkbox", "Date", "DateTime")
#: (normalized base values, plugin config) -> values; ``ValueError("<German reason>")`` rejects
DfValidate = Callable[[list[str], Mapping[str, Any]], list[str]]
DfDisplay = Callable[[list[str], Mapping[str, Any]], str]

PLUGIN_TYPE_CONFIG_KEY: Final = "TiqoraPluginType"
PLUGIN_CONFIG_KEY: Final = "TiqoraPluginConfig"
#: validate / display run in a worker thread; this is how long a request waits for them (F24)
CALL_TIMEOUT_SECONDS: Final = 1.0
_MAX_REASON_LEN: Final = 300


@dataclass(frozen=True, slots=True)
class DynamicFieldType:
    key: str  # qualified, e.g. "Plugin_points_Contract"
    plugin_id: str
    label: str
    base_type: DfBaseType
    validate: DfValidate | None
    display: DfDisplay | None
    config_model: type[BaseModel] | None
    context: PluginContext | None = None


DYNAMIC_FIELD_TYPES: Final[ExtensionPoint[DynamicFieldType]] = ExtensionPoint(
    key="core.dynamic_fields.types",
    item_key=lambda t: t.key,
    description="Dynamic field types a plugin offers on top of Znuny's base types.",
    sdk_stable=True,
)


def plugin_type_of(config: Mapping[str, Any]) -> DynamicFieldType | None:
    """The registered type named by ``config`` whose plugin is active, else ``None``."""
    key = config.get(PLUGIN_TYPE_CONFIG_KEY)
    if not isinstance(key, str) or not key:
        return None
    item = next((t for t in get_registry().items(DYNAMIC_FIELD_TYPES) if t.key == key), None)
    if item is None or not is_active(item.plugin_id):
        return None
    return item


def _plugin_settings(item: DynamicFieldType, config: Mapping[str, Any]) -> dict[str, Any]:
    """The field's ``TiqoraPluginConfig`` with the type's defaults filled in."""
    raw = config.get(PLUGIN_CONFIG_KEY)
    settings: dict[str, Any] = dict(raw) if isinstance(raw, Mapping) else {}
    if item.config_model is not None:
        try:
            return item.config_model.model_validate(settings).model_dump()
        except Exception:  # a hand-edited config; the plugin gets what is stored
            logger.warning("plugin_df_config_invalid", plugin=item.plugin_id, type=item.key)
    return settings


def _checked(values: object, field_type: str) -> list[str] | None:
    from tiqora.domain.dynamic_field_values import DynamicFieldValueError, normalize_df_values

    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        return None
    try:
        return normalize_df_values(field_type, values)
    except DynamicFieldValueError:
        return None


async def validate_plugin_values(field: DynamicFieldConfig, values: list[str]) -> list[str]:
    """Run the plugin type's validator over already normalized ``values``.

    No plugin type (or it is not loaded) -> ``values``. ``ValueError`` from the plugin ->
    :class:`~tiqora.domain.dynamic_field_values.DynamicFieldValueError` with its text. Any other
    failure, a timeout or a result that is not a ``list[str]`` valid for the base type ->
    ``values`` unchanged and a log line (fail-open, Assumption 8).
    """
    from tiqora.domain.dynamic_field_values import DynamicFieldValueError

    item = plugin_type_of(field.config)
    if item is None or item.validate is None:
        return values
    validate = item.validate
    settings = _plugin_settings(item, field.config)

    def run() -> tuple[bool, list[str] | str]:
        try:
            return True, validate(list(values), settings)
        except ValueError as exc:  # a rejection is an answer, not a failure
            return False, str(exc)

    outcome = await call_plugin(
        item.plugin_id,
        f"dynamic field validate {item.key}",
        lambda: asyncio.to_thread(run),
        timeout=CALL_TIMEOUT_SECONDS,
    )
    if not outcome.ok or outcome.value is None:
        return values
    accepted, payload = outcome.value
    if not accepted:
        raise DynamicFieldValueError(str(payload)[:_MAX_REASON_LEN])
    checked = _checked(payload, field.field_type)
    if checked is None:
        logger.warning("plugin_df_validate_result_invalid", plugin=item.plugin_id, type=item.key)
        return values
    return checked


def display_plugin_value(config: Mapping[str, Any], values: list[str]) -> str | None:
    """The plugin's display text, or ``None`` (no plugin type / failure) for the core display.

    Runs inline: the callers are synchronous. Async callers use :func:`display_value_bounded`.
    """
    item = plugin_type_of(config)
    if item is None or item.display is None:
        return None
    display = item.display
    settings = _plugin_settings(item, config)
    outcome = call_plugin_sync(
        item.plugin_id, f"dynamic field display {item.key}", lambda: display(list(values), settings)
    )
    return outcome.value if outcome.ok and isinstance(outcome.value, str) else None


async def display_value_bounded(
    field_type: str, config: Mapping[str, Any], stored: str | list[str] | None
) -> str:
    """:func:`~tiqora.domain.dynamic_field_values.df_display_value` for async callers: a plugin
    display runs in a worker thread with a 1 s limit (F24)."""
    from tiqora.domain.dynamic_field_values import df_display_value

    item = plugin_type_of(config)
    if item is None or item.display is None:
        return df_display_value(field_type, config, stored, plugin=False)
    keys = [stored] if isinstance(stored, str) else [str(v) for v in (stored or [])]
    display = item.display
    settings = _plugin_settings(item, config)
    outcome = await call_plugin(
        item.plugin_id,
        f"dynamic field display {item.key}",
        lambda: asyncio.to_thread(display, keys, settings),
        timeout=CALL_TIMEOUT_SECONDS,
    )
    if outcome.ok and isinstance(outcome.value, str):
        return outcome.value
    return df_display_value(field_type, config, stored, plugin=False)
