"""Admin presets for common identity providers (applied once by ``create_provider``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from tiqora.identity.config_models import AttributeMap
from tiqora.identity.types import Audience, MatchBy, ProviderKind


@dataclass(frozen=True, slots=True)
class Preset:
    key: str
    kind: ProviderKind
    name_key: str
    button_icon: str
    audience_default: Audience
    config: dict[str, Any] = field(default_factory=dict)
    attribute_map: AttributeMap = field(default_factory=AttributeMap)
    match_by: MatchBy = "login"


_CLAIMS = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims"


def _oidc_map(login: str = "preferred_username") -> AttributeMap:
    return AttributeMap(
        login=[login], email=["email"], first_name=["given_name"], last_name=["family_name"]
    )


def _saml_map(login: list[str] | None = None) -> AttributeMap:
    return AttributeMap(
        login=login or ["saml:name_id"],
        email=[
            "mail",
            "email",
            "urn:oid:0.9.2342.19200300.100.1.3",
            f"{_CLAIMS}/emailaddress",
        ],
        first_name=["givenName", "urn:oid:2.5.4.42", f"{_CLAIMS}/givenname"],
        last_name=["sn", "urn:oid:2.5.4.4", f"{_CLAIMS}/surname"],
    )


def _ldap_map(uid_attr: str) -> AttributeMap:
    return AttributeMap(
        login=[uid_attr], email=["mail"], first_name=["givenName"], last_name=["sn"]
    )


def _p(key: str, kind: ProviderKind, icon: str, **kw: Any) -> Preset:
    return Preset(
        key=key,
        kind=kind,
        name_key=f"admin.identity.preset.{key}",
        button_icon=icon,
        audience_default=kw.pop("audience_default", "agent"),
        **kw,
    )


PRESETS: Final[tuple[Preset, ...]] = (
    _p(
        "entra_id",
        "oidc",
        "microsoft",
        config={"issuer": "https://login.microsoftonline.com/<Verzeichnis-ID>/v2.0"},
        attribute_map=_oidc_map(),
    ),
    _p(
        "google_workspace",
        "oidc",
        "google",
        config={"issuer": "https://accounts.google.com"},
        attribute_map=_oidc_map("email"),
    ),
    _p("keycloak", "oidc", "key", attribute_map=_oidc_map()),
    _p("authentik", "oidc", "shield", attribute_map=_oidc_map()),
    _p(
        "adfs",
        "saml",
        "building",
        config={"name_id_format": "unspecified"},
        attribute_map=_saml_map([f"{_CLAIMS}/upn", "saml:name_id"]),
    ),
    _p("generic_saml", "saml", "shield", attribute_map=_saml_map()),
    _p("generic_oidc", "oidc", "key", attribute_map=_oidc_map()),
    _p("ldap", "ldap", "directory", attribute_map=_ldap_map("uid")),
    _p(
        "active_directory",
        "ldap",
        "directory",
        config={
            "uid_attr": "sAMAccountName",
            "access_attr": "member",
            "subject_attr": "objectGUID",
        },
        attribute_map=_ldap_map("sAMAccountName"),
    ),
)

_BY_KEY: Final = {p.key: p for p in PRESETS}


def get_preset(key: str) -> Preset:
    """Return the preset ``key``; ``KeyError`` when unknown."""
    return _BY_KEY[key]
