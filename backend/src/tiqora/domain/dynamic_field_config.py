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
