"""Typed CMDB class definitions (D4): the attribute tree of a class and its validation.

Pure module: no database, no I/O. Znuny parity: a Znuny ITSM ``ITSMConfigItem`` definition is a
nested ``Input``/``Sub`` tree (``Kernel/System/ITSMConfigItem/Definition.pm``); here the same
tree is a list of :class:`AttributeDef` stored as JSON in ``tiqora_cmdb_class_definition``.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

AttributeType = Literal[
    "text",
    "textarea",
    "integer",
    "decimal",
    "date",
    "datetime",
    "catalog",
    "ci_ref",
    "customer_user",
    "customer_company",
    "agent",
    "service",
    "sla",
    "queue",
    "priority",
    "ticket_type",
    "state",
    "url",
    "email",
    "ip",
    "mac",
    "money",
    "attachment",
    "group",
]

KEY_RE = r"^[A-Za-z][A-Za-z0-9_]{0,63}$"
MAX_DEPTH = 5
MAX_COUNT = 100
MAX_ATTRIBUTES = 500

_KEY = re.compile(KEY_RE)
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INT = re.compile(r"^[+-]?\d+$")

#: types whose stored value is an integer id
INT_ID_TYPES: frozenset[str] = frozenset(
    {
        "catalog",
        "ci_ref",
        "agent",
        "service",
        "sla",
        "queue",
        "priority",
        "ticket_type",
        "state",
        "attachment",
    }
)
_NO_DEFAULT_TYPES = frozenset({"group", "attachment"})


class AttributeOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_length: int | None = None  #: text/textarea/url/email (default 255 text, 20000 textarea)
    min: Decimal | None = None  #: integer/decimal/money
    max: Decimal | None = None
    currency: str | None = None  #: money: ISO 4217, three upper-case letters
    catalog_class: str | None = None  #: catalog (required)
    class_keys: list[str] = Field(default_factory=list)  #: ci_ref: allowed classes ([] = any)
    multiline: bool = False  #: text shown as textarea in the view only


class AttributeDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    type: AttributeType
    required: bool = False
    searchable: bool = False
    portal: bool = False
    count_min: int = 0
    count_max: int = 1
    count_default: int = 1
    default: str | int | None = None
    help: str | None = None
    options: AttributeOptions = Field(default_factory=AttributeOptions)
    sub: list[AttributeDef] = Field(default_factory=list)


AttributeDef.model_rebuild()


@dataclass(frozen=True)
class DefinitionIssue:
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class DefinitionChange:
    path: str
    kind: Literal[
        "added", "removed", "type_changed", "count_changed", "renamed_label", "flags_changed"
    ]
    detail: str = ""


def parse_attrs(raw: list[dict[str, Any]]) -> list[AttributeDef]:
    """Strict: validate raw dicts (e.g. an API body) into the attribute tree."""
    return [AttributeDef.model_validate(item) for item in raw]


def parse_definition_json(raw: str) -> list[AttributeDef]:
    """Tolerant: the stored JSON as attributes; ``[]`` (and a log line) when it is broken."""
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("definition JSON is not a list")
        return parse_attrs(data)
    except (ValueError, TypeError, ValidationError):
        logger.warning("cmdb: stored class definition is not valid JSON, reading it as empty")
        return []


def dump_definition_json(attrs: list[AttributeDef]) -> str:
    """Deterministic JSON (sorted keys): equal definitions dump to equal strings."""
    return json.dumps(
        [a.model_dump(mode="json") for a in attrs],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def iter_attribute_paths(attrs: list[AttributeDef]) -> Iterator[tuple[str, AttributeDef]]:
    """Depth-first ``("NIC", def)``, ``("NIC.IPAddress", def)``."""

    def walk(items: list[AttributeDef], prefix: str) -> Iterator[tuple[str, AttributeDef]]:
        for a in items:
            path = f"{prefix}{a.key}"
            yield path, a
            yield from walk(a.sub, f"{path}.")

    yield from walk(attrs, "")


def _default_ok(a: AttributeDef) -> bool:
    d = a.default
    if d is None:
        return True
    if a.type in _NO_DEFAULT_TYPES:
        return False
    if a.type == "integer" or a.type in INT_ID_TYPES:
        return (
            isinstance(d, int)
            and not isinstance(d, bool)
            or (isinstance(d, str) and bool(_INT.match(d.strip())))
        )
    if a.type in ("decimal", "money"):
        try:
            return Decimal(str(d)).is_finite()
        except InvalidOperation:
            return False
    if a.type == "date":
        return isinstance(d, str) and bool(_DATE.match(d))
    return True


def validate_definition(
    attrs: list[AttributeDef],
    *,
    known_catalog_classes: set[str],
    known_class_keys: set[str],
) -> list[DefinitionIssue]:
    issues: list[DefinitionIssue] = []

    def add(path: str, code: str, message: str) -> None:
        issues.append(DefinitionIssue(path, code, message))

    total = sum(1 for _ in iter_attribute_paths(attrs))
    if total > MAX_ATTRIBUTES:
        add("", "too_many", f"Mehr als {MAX_ATTRIBUTES} Attribute.")

    def check(a: AttributeDef, path: str, depth: int) -> None:
        if not _KEY.match(a.key):
            add(path, "bad_key", "Der Schlüssel muss mit einem Buchstaben beginnen (A-Z, 0-9, _).")
        if not 1 <= len(a.name) <= 200:
            add(path, "bad_name", "Die Bezeichnung muss 1 bis 200 Zeichen lang sein.")
        if a.help is not None and len(a.help) > 500:
            add(path, "bad_name", "Der Hilfetext darf höchstens 500 Zeichen lang sein.")
        if depth > MAX_DEPTH:
            add(path, "depth", f"Höchstens {MAX_DEPTH} Ebenen sind erlaubt.")
        if not (
            1 <= a.count_max <= MAX_COUNT
            and 0 <= a.count_min <= a.count_max
            and a.count_min <= a.count_default <= a.count_max
        ):
            add(
                path,
                "count_range",
                f"Anzahl: 0 <= min <= Standard <= max <= {MAX_COUNT} muss gelten.",
            )
        if a.type == "group" and not a.sub:
            add(path, "group_without_sub", "Eine Gruppe braucht mindestens ein Unterattribut.")
        if a.searchable and a.type in ("group", "attachment"):
            add(path, "searchable_not_allowed", "Dieser Typ kann nicht durchsucht werden.")
        if a.type == "catalog" and a.options.catalog_class not in known_catalog_classes:
            add(path, "catalog_class_missing", "Die Katalogklasse fehlt oder ist unbekannt.")
        if a.type == "ci_ref":
            for k in a.options.class_keys:
                if k not in known_class_keys:
                    add(path, "unknown_class_key", f"Unbekannte Klasse: {k}")
        cur = a.options.currency
        if a.type == "money" and cur is not None and not _CURRENCY.match(cur):
            add(path, "bad_currency", "Die Währung muss ein ISO-4217-Code sein (z. B. EUR).")
        if not _default_ok(a):
            add(path, "bad_default", "Der Standardwert passt nicht zum Typ.")
        seen: set[str] = set()
        for child in a.sub:
            if child.key in seen:
                add(f"{path}.{child.key}", "duplicate_key", f"Doppelter Schlüssel: {child.key}")
            seen.add(child.key)
            check(child, f"{path}.{child.key}", depth + 1)

    seen_top: set[str] = set()
    for a in attrs:
        if a.key in seen_top:
            add(a.key, "duplicate_key", f"Doppelter Schlüssel: {a.key}")
        seen_top.add(a.key)
        check(a, a.key, 1)
    return issues


_COUNT_FIELDS = ("count_min", "count_max", "count_default")
_FLAG_FIELDS = ("required", "searchable", "portal", "default", "options")


def definition_changes(old: list[AttributeDef], new: list[AttributeDef]) -> list[DefinitionChange]:
    """Differences per attribute path; children of an added/removed parent are not repeated."""
    old_map = dict(iter_attribute_paths(old))
    new_map = dict(iter_attribute_paths(new))
    out: list[DefinitionChange] = []

    def covered(path: str, other: dict[str, AttributeDef]) -> bool:
        parts = path.split(".")
        return any(".".join(parts[:i]) not in other for i in range(1, len(parts)))

    for path in old_map:
        if path not in new_map and not covered(path, new_map):
            out.append(DefinitionChange(path, "removed", old_map[path].type))
    for path, n in new_map.items():
        o = old_map.get(path)
        if o is None:
            if not covered(path, old_map):
                out.append(DefinitionChange(path, "added", n.type))
            continue
        if o.type != n.type:
            out.append(DefinitionChange(path, "type_changed", f"{o.type} -> {n.type}"))
        changed = [f for f in _COUNT_FIELDS if getattr(o, f) != getattr(n, f)]
        if changed:
            out.append(DefinitionChange(path, "count_changed", ", ".join(changed)))
        if o.name != n.name or o.help != n.help:
            out.append(DefinitionChange(path, "renamed_label", n.name))
        changed = [f for f in _FLAG_FIELDS if getattr(o, f) != getattr(n, f)]
        if changed:
            out.append(DefinitionChange(path, "flags_changed", ", ".join(changed)))
    return out
