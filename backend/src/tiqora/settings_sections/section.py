"""Settings section definition and the supported field-type subset.

A section is a Pydantic model whose fields are stored one-per-row in
``tiqora_settings`` (key ``<section.key>.<field>`` unless an explicit legacy key
is mapped in ``storage_keys``). The admin UI renders a form from the JSON schema,
so only a small, well-defined subset of field types is allowed.
"""

from __future__ import annotations

import re
import types
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Literal, Union, get_args, get_origin

from pydantic import BaseModel, SecretStr

from tiqora.extensions.registry import ExtensionPoint

# Placeholder returned on GET for stored secrets; sending it back must not rotate.
SECRET_MASK: Final = "********"

_KEY_RE: Final = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")


class UnsupportedSettingsSchema(ValueError):
    """A section model uses a field type the settings UI cannot render."""


def _strip_optional(annotation: Any) -> tuple[Any, bool]:
    """``X | None`` -> ``(X, True)``; anything else -> ``(annotation, False)``."""
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1 and len(get_args(annotation)) == 2:
            return args[0], True
    return annotation, False


def _is_literal_of_str(annotation: Any) -> bool:
    return get_origin(annotation) is Literal and all(
        isinstance(a, str) for a in get_args(annotation)
    )


def _is_str_enum(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, StrEnum)


def _is_scalar(annotation: Any) -> bool:
    return (
        annotation in (bool, int, float, str, SecretStr)
        or _is_literal_of_str(annotation)
        or _is_str_enum(annotation)
    )


def _is_supported(annotation: Any) -> bool:
    inner, _ = _strip_optional(annotation)
    if _is_scalar(inner):
        return True
    if get_origin(inner) is list:
        (item,) = get_args(inner) or (None,)
        return item is str or _is_literal_of_str(item)
    return False


def field_kind(annotation: Any) -> str:
    """Storage kind of a (validated) annotation: bool/list/secret/other."""
    inner, _ = _strip_optional(annotation)
    if inner is bool:
        return "bool"
    if inner is SecretStr:
        return "secret"
    if get_origin(inner) is list:
        return "list"
    return "other"


@dataclass(frozen=True, slots=True)
class SettingsSection:
    key: str
    model: type[BaseModel]
    title_key: str
    i18n_prefix: str
    storage_keys: Mapping[str, str] = field(default_factory=dict)

    def storage_key(self, field_name: str) -> str:
        return self.storage_keys.get(field_name) or f"{self.key}.{field_name}"

    def secret_fields(self) -> frozenset[str]:
        return frozenset(
            name
            for name, info in self.model.model_fields.items()
            if field_kind(info.annotation) == "secret"
        )

    def json_schema(self) -> dict[str, Any]:
        return self.model.model_json_schema()


def validate_section_schema(section: SettingsSection) -> None:
    if not _KEY_RE.match(section.key):
        raise UnsupportedSettingsSchema(f"{section.key!r}: invalid section key")
    for name, info in section.model.model_fields.items():
        if info.is_required():
            raise UnsupportedSettingsSchema(f"{section.key}.{name}: field needs a default")
        if not _is_supported(info.annotation):
            raise UnsupportedSettingsSchema(
                f"{section.key}.{name}: unsupported field type {info.annotation!r}"
            )


SETTINGS_SECTIONS: ExtensionPoint[SettingsSection] = ExtensionPoint(
    key="core.settings_sections",
    item_key=lambda s: s.key,
    description="Admin settings sections stored in tiqora_settings.",
)
