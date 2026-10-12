"""SCIM User resource mapping (S6): request document -> ``ScimUserInput`` -> ``MappedProfile``,
and the stored account -> response document. Pure functions, no I/O."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from tiqora.identity.mapping import MappedProfile
from tiqora.identity.scim.errors import ScimError
from tiqora.identity.scim.protocol import SCHEMA_ENTERPRISE, SCHEMA_USER, meta

MAX_USER_NAME: Final = 200
MAX_EXTERNAL_ID: Final = 255
MAX_NAME: Final = 255
MAX_EMAIL: Final = 254
# Top-level keys that never count as "sent": read-only, ignored, or protocol framing.
_NOT_SENT: Final = frozenset({"schemas", "id", "meta", "groups", "password"})
_ENTERPRISE_KEY: Final = SCHEMA_ENTERPRISE.casefold()


@dataclass(frozen=True, slots=True)
class ScimUserInput:
    user_name: str
    external_id: str | None
    active: bool | None  # None = not sent
    given_name: str | None
    family_name: str | None
    display_name: str | None
    email: str | None
    mobile: str | None
    sent: frozenset[str]


def _invalid(detail: str) -> ScimError:
    return ScimError(400, "invalidValue", detail)


def _syntax(detail: str) -> ScimError:
    return ScimError(400, "invalidSyntax", detail)


def _has_control(value: str) -> bool:
    return any(unicodedata.category(ch) == "Cc" for ch in value)


def _clean_text(value: str) -> str:
    """Drop control (Cc) and format/bidi (Cf) characters, then trim."""
    return "".join(ch for ch in value if unicodedata.category(ch) not in ("Cc", "Cf")).strip()


def _bool(value: Any, what: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise _invalid(f"{what} must be a boolean")


def _opt_string(doc: Mapping[str, Any], key: str, limit: int) -> str | None:
    value = doc.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _syntax(f"{key} must be a string")
    cleaned = _clean_text(value)
    if len(cleaned) > limit:
        raise _invalid(f"{key} is too long")
    return cleaned or None


def _lower_domain(address: str) -> str:
    local, sep, domain = address.rpartition("@")
    return f"{local}@{domain.lower()}" if sep else address


def _entries(doc: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = doc.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise _syntax(f"{key} must be a list")
    return [e for e in value if isinstance(e, Mapping)]


def _entry_value(entry: Mapping[str, Any]) -> str | None:
    value = entry.get("value")
    if not isinstance(value, str):
        return None
    return _clean_text(value) or None


def _is_primary(entry: Mapping[str, Any]) -> bool:
    primary = entry.get("primary")
    if isinstance(primary, bool):
        return primary
    return isinstance(primary, str) and primary.strip().lower() == "true"


def _type_of(entry: Mapping[str, Any]) -> str:
    kind = entry.get("type")
    return kind.strip().lower() if isinstance(kind, str) else ""


def _pick_email(doc: Mapping[str, Any]) -> str | None:
    candidates = [(e, v) for e in _entries(doc, "emails") if (v := _entry_value(e))]
    if not candidates:
        return None
    for matcher in (_is_primary, lambda e: _type_of(e) == "work"):
        for entry, value in candidates:
            if matcher(entry):
                return _checked_email(value)
    return _checked_email(candidates[0][1])


def _checked_email(value: str) -> str:
    if len(value) > MAX_EMAIL:
        raise _invalid("emails.value is too long")
    return _lower_domain(value)


def _pick_mobile(doc: Mapping[str, Any]) -> str | None:
    for entry in _entries(doc, "phoneNumbers"):
        if _type_of(entry) == "mobile" and (value := _entry_value(entry)):
            return value[:64]
    return None


def parse_user(doc: Mapping[str, Any]) -> ScimUserInput:
    raw_name = doc.get("userName")
    if raw_name is None:
        raise _invalid("userName is required")
    if not isinstance(raw_name, str):
        raise _syntax("userName must be a string")
    user_name = raw_name.strip()
    if not user_name:
        raise _invalid("userName is required")
    if len(user_name) > MAX_USER_NAME:
        raise _invalid("userName is too long")
    if _has_control(user_name):
        raise _invalid("userName contains control characters")

    active = _bool(doc["active"], "active") if doc.get("active") is not None else None

    name = doc.get("name")
    if name is not None and not isinstance(name, Mapping):
        raise _syntax("name must be an object")
    name = name or {}

    sent = frozenset(k for k in doc if k not in _NOT_SENT and str(k).casefold() != _ENTERPRISE_KEY)
    return ScimUserInput(
        user_name=user_name,
        external_id=_opt_string(doc, "externalId", MAX_EXTERNAL_ID),
        active=active,
        given_name=_opt_string(name, "givenName", MAX_NAME),
        family_name=_opt_string(name, "familyName", MAX_NAME),
        display_name=_opt_string(doc, "displayName", MAX_NAME),
        email=_pick_email(doc),
        mobile=_pick_mobile(doc),
        sent=sent,
    )


def to_profile(inp: ScimUserInput) -> MappedProfile:
    first, last = inp.given_name, inp.family_name
    if (not first or not last) and inp.display_name:
        head, _, rest = inp.display_name.partition(" ")
        first = first or head or None
        last = last or rest.strip() or None
    return MappedProfile(
        login=inp.user_name,
        email=inp.email,
        first_name=first,
        last_name=last,
        customer_id=None,
        mobile=inp.mobile,
    )


def user_resource(
    *,
    scim_id: str,
    user_name: str,
    external_id: str | None,
    active: bool,
    first_name: str,
    last_name: str,
    email: str | None,
    mobile: str | None,
    groups: list[tuple[str, str]],
    location: str,
    created: datetime,
    changed: datetime,
    version: int,
) -> dict[str, Any]:
    formatted = f"{first_name} {last_name}".strip()
    base = location.rsplit("/Users/", 1)[0]
    doc: dict[str, Any] = {"schemas": [SCHEMA_USER], "id": scim_id}
    if external_id:
        doc["externalId"] = external_id
    doc["userName"] = user_name
    doc["name"] = {"givenName": first_name, "familyName": last_name, "formatted": formatted}
    doc["displayName"] = formatted
    doc["active"] = active
    if email:
        doc["emails"] = [{"value": email, "type": "work", "primary": True}]
    if mobile:
        doc["phoneNumbers"] = [{"value": mobile, "type": "mobile"}]
    doc["groups"] = [
        {"value": gid, "display": display, "$ref": f"{base}/Groups/{gid}"}
        for gid, display in groups
    ]
    doc["meta"] = meta("User", location, created, changed, version)
    return doc
