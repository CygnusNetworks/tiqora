"""Pydantic config models per provider kind, plus attribute map / JIT / role rules.

Every kind model satisfies wave-0 ``validate_section_schema`` (flat, every field has
a default, supported types only) so the admin "Verbindung" tab is a generated form.
Secrets are ``SecretStr | None``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, Field, SecretStr

from tiqora.identity.types import GroupPermission, ProviderKind
from tiqora.settings_sections.section import SECRET_MASK, field_kind

# Row ids must fit a 32-bit INT column (otherwise PostgreSQL fails the bind with a 500).
DbId = Annotated[int, Field(ge=1, le=2_147_483_647)]


class OidcProviderConfig(BaseModel):
    issuer: str = ""  # https URL; "/.well-known/openid-configuration" is appended
    client_id: str = ""
    client_secret: SecretStr | None = None
    scopes: str = "openid profile email"
    redirect_uri: str = ""  # empty -> computed; env provider: TIQORA_OIDC_REDIRECT_URI
    pkce: Literal["auto", "always", "never"] = "auto"  # auto = only when S256 is advertised
    email_must_be_verified: bool = True
    allow_private_network: bool = False  # self-hosted Keycloak in RFC1918
    backchannel_logout: bool = True


NameIdFormat = Literal["unspecified", "emailAddress", "persistent", "transient"]


class SamlProviderConfig(BaseModel):
    idp_metadata_url: str = ""
    idp_metadata_xml: str = Field("", json_schema_extra={"x-widget": "textarea"})
    idp_entity_id: str = ""
    name_id_format: NameIdFormat = "unspecified"
    sign_requests: bool = True
    want_response_signed: bool = False  # assertions are ALWAYS required to be signed
    allow_idp_initiated: bool = False
    clock_skew_seconds: int = Field(120, ge=0, le=600)
    allow_private_network: bool = False
    sp_certificate: str = Field("", json_schema_extra={"x-widget": "textarea"})
    sp_private_key: SecretStr | None = None


class LdapProviderConfig(BaseModel):
    host: str = ""
    port: int = Field(389, ge=1, le=65535)
    use_ssl: bool = False
    use_starttls: bool = False
    base_dn: str = ""
    bind_dn: str = ""
    bind_password: SecretStr | None = None
    uid_attr: str = "uid"
    always_filter: str = ""
    group_dn: str = ""
    access_attr: str = "memberUid"
    user_attr: Literal["DN", "UID"] = "DN"
    subject_attr: str = "entryUUID"  # AD: objectGUID; empty/missing value -> entry DN


CONFIG_MODELS: Final[dict[ProviderKind, type[BaseModel]]] = {
    "oidc": OidcProviderConfig,
    "saml": SamlProviderConfig,
    "ldap": LdapProviderConfig,
}
# Read-only fields are ignored by merge_config; hidden fields are never rendered.
READ_ONLY_CONFIG_FIELDS: Final[dict[ProviderKind, frozenset[str]]] = {
    "saml": frozenset({"sp_certificate", "sp_private_key"}),
    "oidc": frozenset(),
    "ldap": frozenset(),
}
HIDDEN_CONFIG_FIELDS: Final[dict[ProviderKind, frozenset[str]]] = {
    "saml": frozenset({"sp_private_key"}),
    "oidc": frozenset(),
    "ldap": frozenset(),
}


class AttributeMap(BaseModel):
    """Ordered source attribute names per profile field; first non-empty wins.

    ``"_text"`` = fixed value ``"text"`` (Znuny UserSyncMap convention).
    """

    login: list[str] = Field(default_factory=list)
    email: list[str] = Field(default_factory=list)
    first_name: list[str] = Field(default_factory=list)
    last_name: list[str] = Field(default_factory=list)
    customer_id: list[str] = Field(default_factory=list)
    mobile: list[str] = Field(default_factory=list)


def _default_permissions() -> list[GroupPermission]:
    return ["rw"]


class GroupGrant(BaseModel):
    group_id: DbId
    permissions: list[GroupPermission] = Field(default_factory=_default_permissions)


class RoleRule(BaseModel):
    attribute: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=500)  # compared case-insensitively after strip
    role_ids: list[DbId] = Field(default_factory=list)
    groups: list[GroupGrant] = Field(default_factory=list)


class JitDefaults(BaseModel):
    initial_role_ids: list[DbId] = Field(default_factory=list)  # agents, first login only
    initial_groups: list[GroupGrant] = Field(default_factory=list)  # agents, first login only
    customer_id: str = ""  # customers: fixed CustomerID when not mapped
    update_profile_on_login: bool = True


def secret_fields(kind: ProviderKind) -> frozenset[str]:
    """Fields of the kind's config model typed ``SecretStr | None``."""
    return frozenset(
        name
        for name, info in CONFIG_MODELS[kind].model_fields.items()
        if field_kind(info.annotation) == "secret"
    )


def merge_config(kind: ProviderKind, current: BaseModel, changes: Mapping[str, Any]) -> BaseModel:
    """Apply admin ``changes`` onto ``current`` and re-validate.

    Secrets: absent or ``SECRET_MASK`` keeps the stored value, ``""``/``None`` removes it,
    anything else replaces it. Read-only and unknown fields are ignored. Raises
    ``pydantic.ValidationError`` for invalid values.
    """
    model = CONFIG_MODELS[kind]
    secrets = secret_fields(kind)
    read_only = READ_ONLY_CONFIG_FIELDS[kind]
    data: dict[str, Any] = {name: getattr(current, name) for name in model.model_fields}
    for name, value in changes.items():
        if name not in model.model_fields or name in read_only:
            continue
        if name in secrets:
            if value == SECRET_MASK:
                continue
            data[name] = None if value in ("", None) else SecretStr(str(value))
        else:
            data[name] = value
    return model.model_validate(data)
