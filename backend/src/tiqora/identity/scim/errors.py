"""SCIM error type and RFC 7644 error body."""

from __future__ import annotations

from typing import Any, Final, Literal

ScimType = Literal[
    "invalidFilter",
    "tooMany",
    "uniqueness",
    "mutability",
    "invalidSyntax",
    "invalidPath",
    "noTarget",
    "invalidValue",
    "invalidVers",
    "sensitive",
]

SCHEMA_ERROR: Final = "urn:ietf:params:scim:api:messages:2.0:Error"


class ScimError(Exception):
    """Raised anywhere in the SCIM layer; the router turns it into an error body."""

    def __init__(self, status: int, scim_type: ScimType | None, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.scim_type = scim_type
        self.detail = detail


def scim_error_body(status: int, scim_type: ScimType | None, detail: str) -> dict[str, Any]:
    """RFC 7644 section 3.12 body. ``status`` is a string; ``scimType`` is omitted when None."""
    body: dict[str, Any] = {"schemas": [SCHEMA_ERROR], "status": str(status)}
    if scim_type is not None:
        body["scimType"] = scim_type
    body["detail"] = detail
    return body
