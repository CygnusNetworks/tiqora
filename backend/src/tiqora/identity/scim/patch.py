"""SCIM PATCH engine (RFC 7644 section 3.5.2), tolerant of the Entra ID and Okta request shapes.

Pure functions: no I/O. Users are patched as documents, groups reduce to a ``MemberDelta``.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal, cast

from tiqora.identity.scim.errors import ScimError
from tiqora.identity.scim.protocol import SCHEMA_ENTERPRISE, SCHEMA_PATCH

_OPS: Final = ("add", "remove", "replace")
_ENTERPRISE_PREFIX: Final = SCHEMA_ENTERPRISE.casefold() + ":"
_PATH_RE: Final = re.compile(
    r"^(?P<attr>[A-Za-z][A-Za-z0-9_]*)(?:\[(?P<flt>[^\]]*)\])?(?:\.(?P<sub>[A-Za-z][A-Za-z0-9_]*))?$"
)
_FILTER_RE: Final = re.compile(
    r'^\s*(?P<attr>[A-Za-z][A-Za-z0-9_]*)\s+eq\s+(?:"(?P<s>[^"]*)"|(?P<b>true|false))\s*$',
    re.IGNORECASE,
)
_MULTI: Final = {"emails": "emails", "phonenumbers": "phoneNumbers"}
# Attributes IdPs send with their default mappings (Entra customappsso, Okta) that Tiqora does not
# store. PATCH is atomic, so rejecting them would also fail a coinciding deactivation (S6a).
# Read-only keys (id, schemas, meta, groups) and password are ignored as well; ``nickName`` stays
# invalidPath.
_IGNORED_ATTRS: Final = frozenset(
    {
        "title",
        "preferredlanguage",
        "addresses",
        "locale",
        "timezone",
        "usertype",
        "profileurl",
        "id",
        "schemas",
        "meta",
        "groups",
        "password",
    }
)
_IGNORED_GROUP_ATTRS: Final = frozenset({"id", "schemas", "meta"})
_ATTR_HEAD_RE: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_]*")
_ENTERPRISE_URN: Final = SCHEMA_ENTERPRISE.casefold()
_IGNORED_NAME_SUBS: Final = frozenset(
    {"formatted", "middlename", "honorificprefix", "honorificsuffix"}
)


@dataclass(frozen=True, slots=True)
class PatchOp:
    op: Literal["add", "remove", "replace"]
    path: str | None
    value: Any


def _syntax(detail: str) -> ScimError:
    return ScimError(400, "invalidSyntax", detail)


def parse_patch(doc: Mapping[str, Any], *, max_ops: int = 1000) -> list[PatchOp]:
    """Validate the PatchOp envelope; op names are case-insensitive ("Replace" from Entra)."""
    if doc.get("schemas") != [SCHEMA_PATCH]:
        raise _syntax("schemas must be the PatchOp message schema")
    raw = doc.get("Operations")
    if not isinstance(raw, list) or not raw:
        raise _syntax("Operations must be a non-empty list")
    if len(raw) > max_ops:
        raise ScimError(400, "invalidValue", "Too many operations")
    out: list[PatchOp] = []
    for item in raw:
        if not isinstance(item, dict):
            raise _syntax("Operation must be an object")
        op = item.get("op")
        if not isinstance(op, str) or op.strip().casefold() not in _OPS:
            raise _syntax("Unsupported op")
        path = item.get("path")
        if path is not None and not isinstance(path, str):
            raise _syntax("path must be a string")
        path = path.strip() if path else None
        norm = cast(Literal["add", "remove", "replace"], op.strip().casefold())
        out.append(PatchOp(norm, path or None, item.get("value")))
    return out


@dataclass(frozen=True, slots=True)
class _Path:
    attr: str  # lower-cased
    flt: tuple[str, str | bool] | None
    sub: str | None  # lower-cased


def _parse_path(raw: str) -> _Path | None:
    """None means "ignored" (enterprise extension attributes); invalidPath otherwise."""
    low = raw.casefold()
    if low == _ENTERPRISE_URN or low.startswith(_ENTERPRISE_PREFIX):
        return None
    head = _ATTR_HEAD_RE.match(raw)
    if head is not None and head.group(0).casefold() in _IGNORED_ATTRS:
        return None
    m = _PATH_RE.match(raw)
    if m is None:
        raise ScimError(400, "invalidPath", "Unsupported path")
    flt: tuple[str, str | bool] | None = None
    if m.group("flt") is not None:
        fm = _FILTER_RE.match(m.group("flt"))
        if fm is None:
            raise ScimError(400, "invalidPath", "Unsupported path filter")
        value: str | bool = (
            fm.group("s") if fm.group("s") is not None else fm.group("b").casefold() == "true"
        )
        flt = (fm.group("attr").casefold(), value)
    sub = m.group("sub")
    return _Path(m.group("attr").casefold(), flt, sub.casefold() if sub else None)


def _exclusive_primary(entries: list[dict[str, Any]], winners: list[int]) -> None:
    """A newly written ``primary: true`` entry demotes the other entries' primary flag."""
    if not any(entries[i].get("primary") is True for i in winners):
        return
    for i, entry in enumerate(entries):
        if i not in winners and entry.get("primary") is True:
            entries[i] = {**entry, "primary": False}


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().casefold() in ("true", "false"):
        return value.strip().casefold() == "true"
    raise ScimError(400, "invalidValue", "active must be a boolean")


def _string(value: Any, what: str) -> str:
    if isinstance(value, str):
        return value
    raise ScimError(400, "invalidValue", f"{what} must be a string")


def _matches(entry: Mapping[str, Any], flt: tuple[str, str | bool]) -> bool:
    attr, wanted = flt
    for key, have in entry.items():
        if key.casefold() != attr:
            continue
        if isinstance(wanted, bool):
            return have is wanted
        return isinstance(have, str) and have.casefold() == wanted.casefold()
    return False


def _entries(value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else [value]
    if not all(isinstance(i, dict) for i in items):
        raise ScimError(400, "invalidValue", "Expected complex values")
    return [dict(i) for i in items]


def _filter_seed(flt: tuple[str, str | bool]) -> dict[str, Any]:
    return {flt[0]: flt[1]}


def _apply_multi(doc: dict[str, Any], key: str, op: PatchOp, p: _Path) -> None:
    current: list[dict[str, Any]] = list(doc.get(key) or [])
    if p.sub not in (None, "value"):
        raise ScimError(400, "invalidPath", "Unsupported sub-attribute")
    if op.op == "remove":
        if p.flt is None:
            doc.pop(key, None)
            return
        flt = p.flt
        remaining = [e for e in current if not _matches(e, flt)]
        if remaining:
            doc[key] = remaining
        else:
            doc.pop(key, None)
        return
    if op.value is None:
        raise ScimError(400, "invalidValue", "value is required")
    if p.flt is None:
        if p.sub == "value":
            raise ScimError(400, "invalidPath", "A filter is required for .value")
        new = _entries(op.value)
        if op.op == "replace":
            doc[key] = new
        else:
            doc[key] = _merge_entries(current, new)
        return
    flt = p.flt
    hits = [i for i, e in enumerate(current) if _matches(e, flt)]
    if p.sub == "value":
        new_value = _string(op.value, "value")
        if hits:
            for i in hits:
                current[i]["value"] = new_value
        else:
            current.append({**_filter_seed(flt), "value": new_value})
    else:
        entry = {**_filter_seed(flt), **_entries(op.value)[0]}
        if hits:
            for i in hits:
                current[i] = {**current[i], **entry}
            winners = hits
        else:
            current.append(entry)
            winners = [len(current) - 1]
        _exclusive_primary(current, winners)
    doc[key] = current


def _merge_entries(
    current: list[dict[str, Any]], new: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged = list(current)
    winners: list[int] = []
    for entry in new:
        for i, have in enumerate(merged):
            if str(have.get("value", "")).casefold() == str(entry.get("value", "")).casefold():
                merged[i] = {**have, **entry}
                winners.append(i)
                break
        else:
            merged.append(entry)
            winners.append(len(merged) - 1)
    _exclusive_primary(merged, winners)
    return merged


def _apply_name(doc: dict[str, Any], op: PatchOp, p: _Path) -> bool:
    """Returns True when a stored name attribute may have changed."""
    name: dict[str, Any] = dict(doc.get("name") or {})
    if p.flt is not None:
        raise ScimError(400, "invalidPath", "Unsupported path filter")
    if p.sub is None:
        if op.op == "remove":
            doc.pop("name", None)
            return True
        if not isinstance(op.value, dict):
            raise ScimError(400, "invalidValue", "name must be an object")
        changed = False
        for k, v in op.value.items():
            sub = str(k).casefold()
            name = _set_name(name, sub, v, op.op)
            changed = changed or sub not in _IGNORED_NAME_SUBS
        if changed:
            doc["name"] = name
        return changed
    if p.sub in _IGNORED_NAME_SUBS:
        return False
    name = _set_name(name, p.sub, None if op.op == "remove" else op.value, op.op)
    if name:
        doc["name"] = name
    else:
        doc.pop("name", None)
    return True


def _set_name(name: dict[str, Any], sub: str, value: Any, op: str) -> dict[str, Any]:
    fields = {"givenname": "givenName", "familyname": "familyName"}
    if sub in _IGNORED_NAME_SUBS:
        return name
    if sub not in fields:
        raise ScimError(400, "invalidPath", "Unsupported name attribute")
    out = dict(name)
    if value is None:
        out.pop(fields[sub], None)
    else:
        out[fields[sub]] = _string(value, fields[sub])
    return out


def _apply_scalar(doc: dict[str, Any], key: str, op: PatchOp, p: _Path) -> None:
    if p.flt is not None or p.sub is not None:
        raise ScimError(400, "invalidPath", "Unsupported path")
    if op.op == "remove":
        if key in ("userName", "active"):
            raise ScimError(400, "mutability", f"{key} cannot be removed")
        doc.pop(key, None)
        return
    if key == "active":
        doc[key] = _bool(op.value)
    elif op.value is None and key != "userName":
        doc.pop(key, None)
    else:
        doc[key] = _string(op.value, key)


_SCALARS: Final = {
    "active": "active",
    "username": "userName",
    "externalid": "externalId",
    "displayname": "displayName",
}


def _apply_one(doc: dict[str, Any], touched: set[str], op: PatchOp) -> None:
    if op.path is None:
        if op.op == "remove":
            raise ScimError(400, "noTarget", "remove requires a path")
        if not isinstance(op.value, dict):
            raise ScimError(400, "invalidValue", "value must be an object without a path")
        for key, value in op.value.items():
            _apply_one(doc, touched, PatchOp(op.op, str(key), value))
        return
    p = _parse_path(op.path)
    if p is None or p.attr in _IGNORED_ATTRS:
        return
    if p.attr in _SCALARS:
        key = _SCALARS[p.attr]
        _apply_scalar(doc, key, op, p)
    elif p.attr in _MULTI:
        key = _MULTI[p.attr]
        _apply_multi(doc, key, op, p)
    elif p.attr == "name":
        key = "name"
        if not _apply_name(doc, op, p):
            return
    else:
        raise ScimError(400, "invalidPath", "Unsupported path")
    touched.add(key)


def apply_user_patch(
    resource: dict[str, Any], ops: list[PatchOp]
) -> tuple[dict[str, Any], frozenset[str]]:
    """Apply ``ops`` to a copy of ``resource``; returns (document, touched top-level keys)."""
    doc = copy.deepcopy(resource)
    touched: set[str] = set()
    for op in ops:
        _apply_one(doc, touched, op)
    return doc, frozenset(touched)


@dataclass(frozen=True, slots=True)
class MemberDelta:
    add: frozenset[str]
    remove: frozenset[str]
    replace_all: frozenset[str] | None
    display_name: str | None
    external_id: str | None | Literal[False]


_MEMBER_FILTER_RE: Final = re.compile(r'^members\[\s*value\s+eq\s+"([^"]+)"\s*\]$', re.IGNORECASE)


def _member_ids(value: Any, max_members: int) -> set[str]:
    if value is None:
        return set()
    items = value if isinstance(value, list) else [value]
    if len(items) > max_members:
        raise ScimError(400, "invalidValue", "Too many members in one request")
    ids: set[str] = set()
    for item in items:
        v = item.get("value") if isinstance(item, dict) else None
        if not isinstance(v, str) or not v:
            raise ScimError(400, "invalidValue", "Each member needs a value")
        ids.add(v)
    return ids


def apply_group_patch(ops: list[PatchOp], *, max_members: int = 5000) -> MemberDelta:
    add: set[str] = set()
    remove: set[str] = set()
    replace_all: set[str] | None = None
    display_name: str | None = None
    external_id: str | None | Literal[False] = False

    def do_add(ids: set[str]) -> None:
        nonlocal replace_all
        if replace_all is not None:
            replace_all |= ids
        else:
            add.update(ids)
        remove.difference_update(ids)

    def do_remove(ids: set[str]) -> None:
        nonlocal replace_all
        if replace_all is not None:
            replace_all -= ids
        else:
            remove.update(ids)
        add.difference_update(ids)

    def one(op: PatchOp) -> None:
        nonlocal replace_all, display_name, external_id
        if op.path is None:
            if op.op == "remove":
                raise ScimError(400, "noTarget", "remove requires a path")
            if not isinstance(op.value, dict):
                raise ScimError(400, "invalidValue", "value must be an object without a path")
            for key, value in op.value.items():
                one(PatchOp(op.op, str(key), value))
            return
        path = op.path.strip()
        low = path.casefold()
        if low in _IGNORED_GROUP_ATTRS:
            return
        m = _MEMBER_FILTER_RE.match(path)
        if m is not None:
            if op.op != "remove":
                raise ScimError(400, "invalidPath", "members filter is only valid for remove")
            do_remove({m.group(1)})
        elif low == "members":
            if op.op == "remove":
                if op.value is None:
                    replace_all = set()
                    add.clear()
                    remove.clear()
                else:
                    do_remove(_member_ids(op.value, max_members))
            elif op.op == "add":
                do_add(_member_ids(op.value, max_members))
            else:
                replace_all = _member_ids(op.value, max_members)
                add.clear()
                remove.clear()
        elif low == "displayname":
            if op.op == "remove":
                raise ScimError(400, "mutability", "displayName cannot be removed")
            display_name = _string(op.value, "displayName")
        elif low == "externalid":
            external_id = (
                None if op.op == "remove" or op.value is None else _string(op.value, "externalId")
            )
        else:
            raise ScimError(400, "invalidPath", "Unsupported path")

    for op in ops:
        one(op)
    if (
        len(add) > max_members
        or len(remove) > max_members
        or (replace_all is not None and len(replace_all) > max_members)
    ):
        raise ScimError(400, "invalidValue", "Too many members in one request")
    return MemberDelta(
        frozenset(add),
        frozenset(remove),
        None if replace_all is None else frozenset(replace_all),
        display_name,
        external_id,
    )


__all__: Final = ["MemberDelta", "PatchOp", "apply_group_patch", "apply_user_patch", "parse_patch"]
