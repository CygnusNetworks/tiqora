"""``ConfigItemUpdate`` attribute merge, ported from Znuny (pure part).

Port of ``ConfigItemAttributesGet`` (Znuny 6.5 ``ProcessManagement/TransitionAction/
ConfigItemUpdate.pm``): the flat transition-action parameters become one CI update. Header keys
are kept as header fields, every other key is a (stringified) attribute path, list keys are
expanded, and the result is merged with the current attribute values along the class definition.
Catalog and CI reference values stay ids (no resolution happens here).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from tiqora.itsm.znuny_format.definition import to_znuny_definition
from tiqora.itsm.znuny_format.stringified import (
    HEADER_KEYS,
    expand_list_keys,
    stringified_to_xmldata,
    xmldata_to_stringified,
)
from tiqora.itsm.znuny_format.xmldata import (
    attributes_to_xmldata,
    xmldata_to_attributes,
)
from tiqora.util.perl import perl_truthy

__all__ = ["HEADER_KEYS", "CiUpdate", "build_update"]


@dataclass(frozen=True)
class CiUpdate:
    name: str | None
    depl_state_name: str | None
    inci_state_name: str | None
    depl_state_id: int | None
    inci_state_id: int | None
    attributes: dict[str, Any]


class _IdResolver:
    """Ids stay ids: Znuny ids of catalog entries and CIs are kept as they are."""

    def ci(self, znuny_ci_id: str) -> int | None:
        return _to_int(znuny_ci_id)

    def catalog(self, znuny_gc_id: str) -> int | None:
        return _to_int(znuny_gc_id)


def _to_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _header_text(config: Mapping[str, Any], key: str) -> str | None:
    value = config.get(key)
    return str(value) if perl_truthy(value) else None


def _header_id(config: Mapping[str, Any], key: str) -> int | None:
    value = config.get(key)
    return _to_int(value) if perl_truthy(value) else None


def build_update(
    config: Mapping[str, Any],
    *,
    current_attributes: Mapping[str, Any],
    schema: Mapping[str, Any],
    separator: str = ",",
    customer_lookup: Callable[[str], list[str]] | None = None,
) -> CiUpdate:
    """Merge the transition-action ``config`` into the CI's current attributes.

    ``separator`` is the default; a truthy ``Separator`` in ``config`` overrides it (Znuny's
    ``$Param{Separator} || ','``). Keys the class definition does not name are dropped.
    """
    sep = config.get("Separator")
    effective = str(sep) if perl_truthy(sep) else separator
    definition = to_znuny_definition(schema)
    original = xmldata_to_stringified(attributes_to_xmldata(current_attributes, schema))
    flat = expand_list_keys(config, effective)
    merged = stringified_to_xmldata(flat, definition, original, customer_lookup=customer_lookup)
    attributes, _issues = xmldata_to_attributes(merged, schema, _IdResolver())
    return CiUpdate(
        name=_header_text(config, "Name"),
        depl_state_name=_header_text(config, "DeplStateName"),
        inci_state_name=_header_text(config, "InciStateName"),
        depl_state_id=_header_id(config, "DeplStateID"),
        inci_state_id=_header_id(config, "InciStateID"),
        attributes=attributes,
    )
