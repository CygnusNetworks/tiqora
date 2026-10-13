"""Znuny ``XMLData`` <-> Tiqora CI attributes (cmdb D4: ``{"Key": [{"v": ..., "s": {...}}]}``).

Znuny stores a CI version as ``{"Key": [{"Content": "x", "Sub": [{...}]}]}``; a Tiqora attribute
entry carries the value in ``v`` and the sub attributes in ``s``. This is the only file of the
importer that depends on the cmdb attribute model; it walks the class schema
(``{"attributes": [AttributeDef JSON]}``) next to the data.

Values: ids of ``catalog`` and ``ci_ref`` attributes are mapped through the :class:`RefResolver`
(an unresolved id stays as text and is reported); other id types are Znuny ids as well and stay
numeric; ``date`` is ``YYYY-MM-DD``, ``datetime`` is naive UTC ``YYYY-MM-DDTHH:MM:SS``. Znuny
``Attachment`` values (binary content) are not carried by an attribute value and are reported.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal, Protocol

from tiqora.cmdb.definition import INT_ID_TYPES
from tiqora.itsm.znuny_format._limits import guard_xml_data

_INT = re.compile(r"^[+-]?\d{1,18}$")
_DATETIME = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(:\d{2})?$")

#: id-valued types that are Znuny ids already (same tables in Tiqora)
_PLAIN_ID_TYPES: Final = INT_ID_TYPES - {"catalog", "ci_ref", "attachment"}


class RefResolver(Protocol):
    def ci(self, znuny_ci_id: str) -> int | None: ...

    def catalog(self, znuny_gc_id: str) -> int | None: ...


@dataclass(frozen=True, slots=True)
class AttrIssue:
    code: Literal["unresolved_reference", "unknown_attribute", "bad_value"]
    path: str
    value: str


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _convert_value(
    kind: str, raw: str, resolver: RefResolver, path: str, issues: list[AttrIssue]
) -> Any:
    """The stored scalar for a Znuny ``Content`` string (``None`` for empty)."""
    if kind == "group" or raw == "":
        return None
    if kind in ("catalog", "ci_ref"):
        resolved = resolver.catalog(raw) if kind == "catalog" else resolver.ci(raw)
        if resolved is None:
            issues.append(AttrIssue("unresolved_reference", path, raw))
            return raw
        return resolved
    if kind == "integer" or kind in _PLAIN_ID_TYPES:
        if _INT.match(raw.strip()):
            return int(raw.strip())
        issues.append(AttrIssue("bad_value", path, raw))
        return raw
    if kind == "date":
        try:
            datetime.strptime(raw.strip(), "%Y-%m-%d")
        except ValueError:
            issues.append(AttrIssue("bad_value", path, raw))
            return raw
        return raw.strip()
    if kind == "datetime":
        m = _DATETIME.match(raw.strip())
        if m is None:
            issues.append(AttrIssue("bad_value", path, raw))
            return raw
        out = f"{m.group(1)}T{m.group(2)}{m.group(3) or ':00'}"
        try:
            datetime.fromisoformat(out)
        except ValueError:
            issues.append(AttrIssue("bad_value", path, raw))
            return raw
        return out
    if kind == "attachment":
        issues.append(AttrIssue("bad_value", path, raw[:80]))
        return None
    return raw


def _is_attribute_list(value: Any) -> bool:
    return isinstance(value, list) and any(isinstance(e, Mapping) for e in value)


def _to_attributes(
    node: Mapping[str, Any],
    attrs: list[dict[str, Any]],
    resolver: RefResolver,
    prefix: str,
    issues: list[AttrIssue],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    known = {str(a["key"]) for a in attrs}
    for a in attrs:
        key = str(a["key"])
        entries = node.get(key)
        if not isinstance(entries, list):
            continue
        kept: list[dict[str, Any]] = []
        for idx, entry in enumerate((e for e in entries if isinstance(e, Mapping)), start=1):
            path = f"{prefix}{key}[{idx}]"
            item: dict[str, Any] = {
                "v": _convert_value(a["type"], _text(entry.get("Content")), resolver, path, issues)
            }
            if a.get("sub"):
                item["s"] = _to_attributes(entry, a["sub"], resolver, f"{path}.", issues)
            else:
                for k, v in entry.items():
                    if k != "Content" and _is_attribute_list(v):
                        issues.append(AttrIssue("unknown_attribute", f"{path}.{k}", k))
            kept.append(item)
        if kept:
            out[key] = kept
    for key, value in node.items():
        if key in known or not _is_attribute_list(value):
            continue
        # not in the class definition: kept as text, reported
        issues.append(AttrIssue("unknown_attribute", f"{prefix}{key}", key))
        out[key] = [{"v": _text(e.get("Content")) or None} for e in value if isinstance(e, Mapping)]
    return out


def xmldata_to_attributes(
    xml_data: Mapping[str, Any], schema: Mapping[str, Any], resolver: RefResolver
) -> tuple[dict[str, Any], list[AttrIssue]]:
    """Znuny XMLData -> cmdb attribute values plus what could not be mapped cleanly.

    Raises ``ValueError`` for XMLData deeper than 16 levels or with over 50 000 values.
    """
    guard_xml_data(xml_data)
    issues: list[AttrIssue] = []
    attrs = list(schema.get("attributes", []))
    return _to_attributes(xml_data, attrs, resolver, "", issues), issues


def _to_content(kind: str, value: Any) -> str:
    if value is None:
        return ""
    if kind == "datetime" and isinstance(value, str):
        return value.replace("T", " ", 1)
    return str(value)


def _to_xml(values: Mapping[str, Any], attrs: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for a in attrs:
        key = str(a["key"])
        entries = values.get(key)
        if not isinstance(entries, list):
            continue
        xml_entries: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            xml: dict[str, Any] = {}
            sub = entry.get("s")
            if a.get("sub") and isinstance(sub, Mapping):
                xml.update(_to_xml(sub, a["sub"]))
            xml["Content"] = _to_content(a["type"], entry.get("v"))
            xml_entries.append(xml)
        if xml_entries:
            out[key] = xml_entries
    return out


def attributes_to_xmldata(
    attributes: Mapping[str, Any], schema: Mapping[str, Any]
) -> dict[str, Any]:
    """cmdb attribute values -> Znuny XMLData (``Content`` strings; keys outside the schema drop).

    ``catalog`` and ``ci_ref`` values stay Tiqora ids; callers that talk to a Znuny that only
    knows its own ids map them first.
    """
    return _to_xml(attributes, list(schema.get("attributes", [])))
