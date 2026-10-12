"""SCIM protocol layer: filter, list parameters, projection, discovery documents.

Pure functions, no I/O. The filter parser is a hand-written scanner (no regex on
attacker input) and rejects anything beyond ``<attr> eq "<string>"``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal

from tiqora.identity.scim.errors import ScimError

SCHEMA_USER: Final = "urn:ietf:params:scim:schemas:core:2.0:User"
SCHEMA_GROUP: Final = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCHEMA_ENTERPRISE: Final = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
SCHEMA_LIST: Final = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCHEMA_PATCH: Final = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCHEMA_SPC: Final = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
SCHEMA_RESOURCE_TYPE: Final = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"
SCHEMA_SCHEMA: Final = "urn:ietf:params:scim:schemas:core:2.0:Schema"
MAX_COUNT: Final = 200
DEFAULT_COUNT: Final = 100
MAX_FILTER_LENGTH: Final = 1000

FilterAttr = Literal["id", "userName", "externalId", "emails.value", "displayName"]

_USER_ATTRS: Final[dict[str, FilterAttr]] = {
    "id": "id",
    "username": "userName",
    "externalid": "externalId",
    "emails.value": "emails.value",
}
_GROUP_ATTRS: Final[dict[str, FilterAttr]] = {
    "id": "id",
    "displayname": "displayName",
    "externalid": "externalId",
}
_PATH_CHARS: Final = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._")


@dataclass(frozen=True, slots=True)
class ScimFilter:
    attr: FilterAttr
    value: str


def _bad_filter(detail: str = "Unsupported or malformed filter") -> ScimError:
    return ScimError(400, "invalidFilter", detail)


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in " \t":
        i += 1
    return i


def _scan_path(s: str, i: int) -> tuple[str, int]:
    """Attribute path with an optional ``[type eq "work"]`` qualifier (emails only)."""
    start = i
    while i < len(s) and s[i] in _PATH_CHARS:
        i += 1
    path = s[start:i].lower()
    if not path:
        raise _bad_filter()
    if i < len(s) and s[i] == "[":
        end = s.find("]", i)
        if path != "emails" or end < 0:
            raise _bad_filter()
        inner = s[i + 1 : end]
        parts = inner.split()
        if len(parts) != 3 or parts[0].lower() != "type" or parts[1].lower() != "eq":
            raise _bad_filter()
        if parts[2].lower() not in ('"work"', '"home"', '"other"'):
            raise _bad_filter()
        i = end + 1
        if s[i : i + 6].lower() != ".value":
            raise _bad_filter()
        path = "emails.value"
        i += 6
    return path, i


def _scan_string(s: str, i: int) -> tuple[str, int]:
    """One JSON string token starting at ``s[i] == '"'``; linear scan."""
    if i >= len(s) or s[i] != '"':
        raise _bad_filter("Filter value must be a quoted string")
    j = i + 1
    while j < len(s):
        ch = s[j]
        if ch == "\\":
            j += 2
            continue
        if ch == '"':
            try:
                value = json.loads(s[i : j + 1])
            except ValueError:
                raise _bad_filter("Invalid string escape in filter") from None
            if not isinstance(value, str):
                raise _bad_filter()
            return value, j + 1
        j += 1
    raise _bad_filter("Unterminated string in filter")


def parse_filter(raw: str | None, *, resource: Literal["User", "Group"]) -> ScimFilter | None:
    """Parse ``<attrPath> eq "<string>"``; ``None`` for a missing/blank filter."""
    if raw is None or not raw.strip():
        return None
    if len(raw) > MAX_FILTER_LENGTH:
        raise _bad_filter("Filter too long")
    path, i = _scan_path(raw, _skip_ws(raw, 0))
    allowed = _USER_ATTRS if resource == "User" else _GROUP_ATTRS
    attr = allowed.get(path)
    if attr is None:
        raise _bad_filter("Unsupported filter attribute")
    j = _skip_ws(raw, i)
    if j == i:  # operator needs leading whitespace
        raise _bad_filter()
    if raw[j : j + 2].lower() != "eq":
        raise _bad_filter("Only the eq operator is supported")
    k = j + 2
    k2 = _skip_ws(raw, k)
    if k2 == k:
        raise _bad_filter()
    value, end = _scan_string(raw, k2)
    if raw[end:].strip():
        raise _bad_filter("Trailing text in filter")
    return ScimFilter(attr, value)


@dataclass(frozen=True, slots=True)
class ListParams:
    start_index: int
    count: int
    attributes: frozenset[str] | None
    excluded: frozenset[str]


def _int_param(query: Mapping[str, str], key: str) -> int | None:
    raw = query.get(key)
    if raw is None:
        return None
    body = raw.strip()
    digits = body[1:] if body[:1] in "+-" else body
    if not digits or not digits.isascii() or not digits.isdigit() or len(digits) > 12:
        raise ScimError(400, "invalidValue", f"{key} must be an integer")
    return int(body)


def _names(raw: str | None) -> frozenset[str]:
    if not raw:
        return frozenset()
    out: set[str] = set()
    for part in raw.split(","):
        name = part.strip()
        if not name:
            continue
        if not name.lower().startswith("urn:"):
            name = name.split(".", 1)[0]
        out.add(name.lower())
    return frozenset(out)


def parse_list_params(query: Mapping[str, str]) -> ListParams:
    start = _int_param(query, "startIndex")
    count = _int_param(query, "count")
    attrs = _names(query.get("attributes"))
    return ListParams(
        start_index=max(1, start) if start is not None else 1,
        count=DEFAULT_COUNT if count is None else min(max(count, 0), MAX_COUNT),
        attributes=attrs or None,
        excluded=_names(query.get("excludedAttributes")),
    )


_ALWAYS: Final = frozenset({"schemas", "id", "meta"})


def project(doc: dict[str, Any], params: ListParams) -> dict[str, Any]:
    """Apply ``attributes`` / ``excludedAttributes``; schemas, id and meta always stay."""
    out: dict[str, Any] = {}
    for key, value in doc.items():
        low = key.lower()
        if low in _ALWAYS:
            out[key] = value
        elif params.attributes is not None:
            if low in params.attributes:
                out[key] = value
        elif low not in params.excluded:
            out[key] = value
    return out


def list_response(
    total: int, resources: list[dict[str, Any]], params: ListParams
) -> dict[str, Any]:
    return {
        "schemas": [SCHEMA_LIST],
        "totalResults": total,
        "startIndex": params.start_index,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


def _iso_z(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def meta(
    resource_type: str, location: str, created: datetime, changed: datetime, version: int
) -> dict[str, Any]:
    return {
        "resourceType": resource_type,
        "created": _iso_z(created),
        "lastModified": _iso_z(changed),
        "location": location,
        "version": f'W/"{version}"',
    }


def service_provider_config(base: str) -> dict[str, Any]:
    return {
        "schemas": [SCHEMA_SPC],
        "documentationUri": "https://github.com/CygnusNetworks/tiqora",
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": MAX_COUNT},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "OAuth Bearer Token",
                "description": "Authentication via the Tiqora provisioning token",
                "specUri": "https://www.rfc-editor.org/info/rfc6750",
                "primary": True,
            }
        ],
        "meta": {
            "resourceType": "ServiceProviderConfig",
            "location": f"{base}/ServiceProviderConfig",
        },
    }


def resource_types(base: str, *, groups: bool) -> list[dict[str, Any]]:
    types: list[dict[str, Any]] = [
        {
            "schemas": [SCHEMA_RESOURCE_TYPE],
            "id": "User",
            "name": "User",
            "endpoint": "/Users",
            "description": "User account",
            "schema": SCHEMA_USER,
            "schemaExtensions": [{"schema": SCHEMA_ENTERPRISE, "required": False}],
            "meta": {"resourceType": "ResourceType", "location": f"{base}/ResourceTypes/User"},
        }
    ]
    if groups:
        types.append(
            {
                "schemas": [SCHEMA_RESOURCE_TYPE],
                "id": "Group",
                "name": "Group",
                "endpoint": "/Groups",
                "description": "Group",
                "schema": SCHEMA_GROUP,
                "meta": {
                    "resourceType": "ResourceType",
                    "location": f"{base}/ResourceTypes/Group",
                },
            }
        )
    return types


def _attr(
    name: str,
    typ: str = "string",
    *,
    mutability: str = "readWrite",
    returned: str = "default",
    uniqueness: str = "none",
    case_exact: bool = False,
    required: bool = False,
    multi: bool = False,
    sub: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": name,
        "type": typ,
        "multiValued": multi,
        "required": required,
        "caseExact": case_exact,
        "mutability": mutability,
        "returned": returned,
        "uniqueness": uniqueness,
    }
    if sub is not None:
        out["subAttributes"] = sub
    return out


def schemas_document() -> list[dict[str, Any]]:
    user_attrs = [
        _attr("userName", required=True, uniqueness="server"),
        _attr("externalId", case_exact=True),
        _attr(
            "name",
            "complex",
            sub=[_attr("givenName"), _attr("familyName"), _attr("formatted")],
        ),
        _attr("displayName"),
        _attr("active", "boolean"),
        _attr(
            "emails",
            "complex",
            multi=True,
            sub=[_attr("value", required=True), _attr("type"), _attr("primary", "boolean")],
        ),
        _attr("id", mutability="readOnly", uniqueness="server", case_exact=True),
    ]
    group_attrs = [
        _attr("displayName", required=True, uniqueness="server"),
        _attr("externalId", case_exact=True),
        _attr(
            "members",
            "complex",
            multi=True,
            sub=[
                _attr("value", mutability="immutable"),
                _attr("display", mutability="readOnly"),
                _attr("$ref", "reference", mutability="immutable"),
            ],
        ),
        _attr("id", mutability="readOnly", uniqueness="server", case_exact=True),
    ]
    enterprise_attrs = [_attr("employeeNumber"), _attr("department")]
    return [
        {
            "schemas": [SCHEMA_SCHEMA],
            "id": SCHEMA_USER,
            "name": "User",
            "description": "User account",
            "attributes": user_attrs,
            "meta": {"resourceType": "Schema"},
        },
        {
            "schemas": [SCHEMA_SCHEMA],
            "id": SCHEMA_GROUP,
            "name": "Group",
            "description": "Group",
            "attributes": group_attrs,
            "meta": {"resourceType": "Schema"},
        },
        {
            "schemas": [SCHEMA_SCHEMA],
            "id": SCHEMA_ENTERPRISE,
            "name": "EnterpriseUser",
            "description": "Enterprise user attributes (accepted and ignored)",
            "attributes": enterprise_attrs,
            "meta": {"resourceType": "Schema"},
        },
    ]
