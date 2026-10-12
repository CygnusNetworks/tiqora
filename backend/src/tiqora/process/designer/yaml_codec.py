"""YAML in Znuny's format (``Kernel::System::YAML``, Perl YAML::XS)."""

from __future__ import annotations

from typing import Any

import yaml

_FastLoader: type = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _reject_anchors(raw: str) -> None:
    for event in yaml.parse(raw, Loader=yaml.SafeLoader):
        anchor = getattr(event, "anchor", None)
        if anchor is not None or isinstance(event, yaml.events.AliasEvent):
            raise yaml.YAMLError("YAML anchors and aliases are not allowed")


def stringify_scalars(data: Any) -> Any:
    """Deep copy with int/float/bool -> str (True -> "1", False -> "0") and None -> ""."""
    if isinstance(data, dict):
        return {str(k): stringify_scalars(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [stringify_scalars(v) for v in data]
    if data is None:
        return ""
    if isinstance(data, bool):
        return "1" if data else "0"
    if isinstance(data, (int, float)):
        return str(data)
    return data


def dump_znuny_yaml(data: Any) -> str:
    return yaml.safe_dump(
        stringify_scalars(data),
        default_flow_style=False,
        allow_unicode=True,
        explicit_start=True,
        sort_keys=True,
    )


def load_znuny_yaml(raw: str | bytes | None) -> Any:
    if raw is None:
        return {}
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    if not text.strip():
        return {}
    _reject_anchors(text)
    loaded = yaml.load(text, Loader=_FastLoader)
    return {} if loaded is None else loaded
