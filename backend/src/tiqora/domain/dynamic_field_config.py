"""Parsing of ``dynamic_field.config`` YAML (shared by API and domain layers)."""

from __future__ import annotations

from typing import Any

import yaml

# Znuny writes these configs with YAML::XS and treats every PossibleValues key
# and label as a string. PyYAML's implicit resolvers would coerce unquoted
# ``yes``/``no``/``on``/``01`` keys to bool/int (collapsing keys), so the
# PossibleValues subtree is composed and forced to plain strings.
_STR_TAG = "tag:yaml.org,2002:str"
_STRING_MAPPINGS = ("PossibleValues",)


def _force_str(node: yaml.Node) -> None:
    if isinstance(node, yaml.ScalarNode):
        node.tag = _STR_TAG
    elif isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            _force_str(k)
            _force_str(v)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            _force_str(item)


def _stringify_possible_values(root: yaml.Node) -> None:
    if not isinstance(root, yaml.MappingNode):
        return
    for k, v in root.value:
        if (
            isinstance(k, yaml.ScalarNode)
            and k.value in _STRING_MAPPINGS
            and isinstance(v, yaml.MappingNode)
        ):
            _force_str(v)


def config_from_yaml(raw: str | bytes | None) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, memoryview | bytearray):
        raw = bytes(raw)
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    try:
        node = yaml.compose(text, Loader=yaml.SafeLoader)
        if node is None:
            return {}
        _stringify_possible_values(node)
        loader = yaml.SafeLoader("")
        try:
            loaded = loader.construct_document(node)
        finally:
            loader.dispose()
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


# --- validation + serialisation for writes ------------------------------------------------
# Keys recognised (validated/allowed) per field_type. Extra unknown keys are rejected to avoid
# silently persisting typos that Znuny would ignore. The sets contain every key the Znuny
# 6.5/7.3 drivers and the ITSM packages write (``Link`` on Date/DateTime comes from the ITSM
# packages' own fields; ``LinkPreview``, ``TreeView`` and ``DateRestriction`` from the core admin
# screens), so a config Znuny wrote can be written back unchanged.
_COMMON_TEXT_KEYS = {"DefaultValue", "Link", "LinkPreview", "RegExList"}
_SELECT_KEYS = {
    "DefaultValue",
    "PossibleValues",
    "PossibleNone",
    "TranslatableValues",
    "TreeView",
    "Link",
    "LinkPreview",
}
_DATETIME_KEYS = {
    "DefaultValue",
    "YearsPeriod",
    "YearsInPast",
    "YearsInFuture",
    "Link",
    "LinkPreview",
    "DateRestriction",
}

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

DYNAMIC_FIELD_TYPES = frozenset(_ALLOWED_KEYS)


def znuny_config_keys(field_type: str) -> frozenset[str]:
    """The config keys Znuny knows for *field_type* (empty for an unsupported type)."""
    return frozenset(_ALLOWED_KEYS.get(field_type, ()))


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
