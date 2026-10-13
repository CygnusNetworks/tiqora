"""CI attributes: decode ``xml_storage`` into values and resolve them for display.

Three steps, so that the display of a whole page costs a bounded number of statements:

1. :func:`values_from_xml_data` (pure) walks the class definition over Znuny's ``XMLData``
   (``build_xml_hash`` of the version's ``xml_storage`` rows) and yields :class:`AttrValue` trees:
   nested (``Sub``), repeatable (``CountMax > 1``), in definition order, ``Content`` per instance.
   Keys that the definition does not know are ignored, as Znuny's zoom does.
2. :func:`collect_lookup_keys` and :func:`load_lookups` (one statement per input type, IN lists of
   at most 500) fetch the names behind ids: general catalog, CIs, users, customers, services ...
3. :func:`resolve_display` (pure) turns the values into the contract's :class:`CiAttribute` tree.

The display strings follow the ``ValueLookup`` of Znuny's ``XML/Type/*`` modules (catalog and
service names, ``Name (Number)`` for a CI, ``"First Last" <mail>`` for a user). Input types that
Znuny itself shows raw, and types this code does not know, become ``kind="other"`` with the stored
value as display.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.itsm.ci_provider import CiAttribute, CiRef
from tiqora.itsm.znuny_adapter.catalog import chunks
from tiqora.itsm.znuny_adapter.definitions import AttrDef

# Znuny ``Input.Type`` -> contract ``kind`` (the 20 modules under ``XML/Type``).
_KIND: Final[Mapping[str, str]] = {
    "Text": "text",
    "TextLink": "text",
    "TextArea": "textarea",
    "Integer": "integer",
    "Date": "date",
    "DateTime": "datetime",
    "GeneralCatalog": "catalog",
    "CI": "ci",
    "Customer": "customer",
    "CustomerCompany": "customer_company",
    "User": "user",
    "Service": "service",
    "SLA": "sla",
    "Queue": "queue",
    "Priority": "priority",
    "Type": "ticket_type",
    "State": "state",
}
# kinds whose stored value is looked up, with (table, key column, id is numeric)
_LOOKUP_TABLE: Final[Mapping[str, tuple[str, str, bool]]] = {
    "catalog": ("general_catalog", "id", True),
    "service": ("service", "id", True),
    "sla": ("sla", "id", True),
    "queue": ("queue", "id", True),
    "priority": ("ticket_priority", "id", True),
    "ticket_type": ("ticket_type", "id", True),
    "state": ("ticket_state", "id", True),
    "customer_company": ("customer_company", "customer_id", False),
}
_BLANK_KINDS: Final = frozenset({"Dummy"})  # Znuny shows nothing for these


def kind_for(input_type: str) -> str:
    return _KIND.get(input_type, "other")


@dataclass(frozen=True, slots=True)
class AttrValue:
    """One stored instance of an attribute (``Content``) with its stored sub attributes."""

    key: str
    content: str | None
    subs: tuple[AttrValue, ...] = ()


def _instances(node: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    raw = node.get(key)
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def values_from_xml_data(defs: Sequence[AttrDef], xml_data: Sequence[Any]) -> list[AttrValue]:
    """Decode ``build_xml_hash`` output (``[None, {'Version': [None, {...}]}]``).

    Instances without any value (empty content and no non-empty child) are dropped.
    """
    root = xml_data[1] if len(xml_data) > 1 and isinstance(xml_data[1], dict) else {}
    versions = _instances(root, "Version")
    if not versions:
        return []
    return _decode(defs, versions[0])


def _decode(defs: Sequence[AttrDef], node: Mapping[str, Any]) -> list[AttrValue]:
    out: list[AttrValue] = []
    for attr in defs:
        for instance in _instances(node, attr.key):
            content = instance.get("Content")
            text_value = None if content is None else str(content)
            subs = tuple(_decode(attr.sub, instance)) if attr.sub else ()
            if not text_value and not subs:
                continue
            out.append(AttrValue(attr.key, text_value or None, subs))
    return out


# ---------------------------------------------------------------------------- lookups


@dataclass(frozen=True, slots=True)
class CiLookup:
    id: int
    number: str
    name: str
    class_id: int


@dataclass(frozen=True, slots=True)
class Lookups:
    #: kind -> stored value -> display name
    names: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    cis: Mapping[int, CiLookup] = field(default_factory=dict)


EMPTY_LOOKUPS: Final = Lookups()


def collect_lookup_keys(
    values: Iterable[AttrValue], defs: Sequence[AttrDef]
) -> dict[str, set[str]]:
    """kind -> stored values that need a name (only kinds that have a lookup)."""
    keys: dict[str, set[str]] = {}
    _collect(values, defs, keys)
    return keys


def _collect(
    values: Iterable[AttrValue], defs: Sequence[AttrDef], keys: dict[str, set[str]]
) -> None:
    by_key = {d.key: d for d in defs}
    for value in values:
        attr = by_key.get(value.key)
        if attr is None:
            continue
        kind = kind_for(attr.input_type)
        if value.content and (kind in _LOOKUP_TABLE or kind in ("ci", "user", "customer")):
            keys.setdefault(kind, set()).add(value.content)
        _collect(value.subs, attr.sub, keys)


def _person(first: object, last: object, email: object) -> str:
    return f'"{first} {last}" <{email}>'


async def user_full_names(session: AsyncSession, ids: Collection[int]) -> dict[int, str]:
    """``users.id`` -> ``First Last`` (the login when both names are empty)."""
    return {uid: full for uid, (full, _login) in (await _users(session, ids)).items()}


async def _users(session: AsyncSession, ids: Collection[int]) -> dict[int, tuple[str, str]]:
    stmt = text("SELECT id, first_name, last_name, login FROM users WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    out: dict[int, tuple[str, str]] = {}
    for part in chunks(sorted(set(ids))):
        for uid, first, last, login in (await session.execute(stmt, {"ids": list(part)})).all():
            full = f"{first} {last}".strip() or str(login)
            out[int(uid)] = (full, str(login))
    return out


async def load_lookups(
    session: AsyncSession,
    keys: Mapping[str, Collection[str]],
    *,
    readable_class_ids: Collection[int] | None = None,
) -> Lookups:
    """Names for :func:`collect_lookup_keys` output; one statement per kind (per 500 values).

    ``readable_class_ids`` limits CI references to CIs of those classes: a CI the caller may not
    read stays unresolved (raw id, no ``ref``) instead of leaking its name.
    """
    names: dict[str, dict[str, str]] = {}
    cis: dict[int, CiLookup] = {}
    for kind, raw_values in keys.items():
        values = sorted(raw_values)
        if kind in _LOOKUP_TABLE:
            table, column, numeric = _LOOKUP_TABLE[kind]
            wanted: list[Any] = [int(v) for v in values if v.isdigit()] if numeric else values
            stmt = text(f"SELECT {column}, name FROM {table} WHERE {column} IN :ids").bindparams(
                bindparam("ids", expanding=True)
            )
            found = names.setdefault(kind, {})
            for part in chunks(wanted):
                for key, name in (await session.execute(stmt, {"ids": list(part)})).all():
                    found[str(key)] = str(name)
        elif kind == "user":
            found = names.setdefault(kind, {})
            ids = [int(v) for v in values if v.isdigit()]
            # Znuny's ``User`` type shows "First Last" <UserEmail>; an agent's e-mail address is
            # its login unless a preference overrides it (not read here).
            for uid, (full, login) in (await _users(session, ids)).items():
                found[str(uid)] = f'"{full}" <{login}>'
        elif kind == "customer":
            found = names.setdefault(kind, {})
            stmt = text(
                "SELECT login, first_name, last_name, email FROM customer_user WHERE login IN :ids"
            ).bindparams(bindparam("ids", expanding=True))
            for part in chunks(values):
                for login, first, last, email in (
                    await session.execute(stmt, {"ids": list(part)})
                ).all():
                    found[str(login)] = _person(first, last, email)
        elif kind == "ci":
            ci_ids = [int(v) for v in values if v.isdigit()]
            stmt = text(
                "SELECT c.id, c.configitem_number, c.class_id, v.name FROM configitem c"
                " LEFT JOIN configitem_version v ON v.id = c.last_version_id"
                " WHERE c.id IN :ids"
            ).bindparams(bindparam("ids", expanding=True))
            allowed = None if readable_class_ids is None else set(readable_class_ids)
            for part in chunks(ci_ids):
                for cid, number, class_id, name in (
                    await session.execute(stmt, {"ids": list(part)})
                ).all():
                    if allowed is not None and int(class_id) not in allowed:
                        continue
                    cis[int(cid)] = CiLookup(int(cid), str(number), str(name or ""), int(class_id))
    return Lookups(names=names, cis=cis)


# ---------------------------------------------------------------------------- display


def resolve_display(
    values: Sequence[AttrValue], defs: Sequence[AttrDef], lookups: Lookups = EMPTY_LOOKUPS
) -> tuple[CiAttribute, ...]:
    """The contract's attribute tree. Unknown input types keep the stored value (``other``)."""
    by_key = {d.key: d for d in defs}
    out: list[CiAttribute] = []
    for value in values:
        attr = by_key.get(value.key)
        if attr is None:
            continue
        kind = kind_for(attr.input_type)
        raw = value.content
        display = "" if attr.input_type in _BLANK_KINDS else (raw or "")
        ref: CiRef | None = None
        if raw:
            if kind == "ci" and raw.isdigit() and int(raw) in lookups.cis:
                ci = lookups.cis[int(raw)]
                ref = CiRef("znuny", ci.id)
                display = f"{ci.name} ({ci.number})" if ci.name else ci.number
            elif kind in lookups.names:
                display = lookups.names[kind].get(raw, raw)
        children = resolve_display(value.subs, attr.sub, lookups) if value.subs else ()
        out.append(
            CiAttribute(
                key=attr.key,
                label=attr.name,
                kind=kind,
                display=display,
                raw=raw,
                ref=ref,
                children=children,
            )
        )
    return tuple(out)
