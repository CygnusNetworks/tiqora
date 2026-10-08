"""Request/response models of ``/api/v1/admin/identity-providers`` (D16)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from tiqora.domain.schemas import UtcDateTime
from tiqora.identity.config_models import AttributeMap, JitDefaults, RoleRule
from tiqora.identity.store import ProviderChanges

ProviderKindLit = Literal["oidc", "saml", "ldap"]
AudienceLit = Literal["agent", "customer", "both"]
UserKindLit = Literal["agent", "customer"]


class ProviderEndpointsOut(BaseModel):
    agent_redirect_uri: str | None
    customer_redirect_uri: str | None
    agent_acs_url: str | None
    customer_acs_url: str | None
    sp_entity_id: str | None
    sp_metadata_url: str | None
    slo_url: str | None
    backchannel_logout_url: str | None
    base_url_missing: bool


class IdentityProviderSummaryOut(BaseModel):
    id: int
    slug: str
    kind: ProviderKindLit
    source: Literal["db", "env"]
    name: str
    audience: AudienceLit
    enabled: bool
    sort: int
    button_label: str | None
    button_icon: str | None
    preset: str | None
    jit_enabled: bool
    read_only: bool
    metadata_error: str | None
    link_count: int


class IdentityProviderOut(IdentityProviderSummaryOut):
    # Secrets are SECRET_MASK when set, else None; env rows show the environment's values.
    config: dict[str, Any]
    secrets_set: list[str]
    match_by: Literal["login", "email"]
    require_sso_opt_in: bool
    attribute_map: AttributeMap
    jit_defaults: JitDefaults
    role_rules: list[RoleRule]
    sync_mode: Literal["managed", "authoritative"]
    metadata_fetched_at: UtcDateTime | None
    endpoints: ProviderEndpointsOut


class IdentityProviderCreate(BaseModel):
    preset: str
    name: str = Field(min_length=1, max_length=200)
    slug: str
    audience: AudienceLit = "agent"


class IdentityKindOut(BaseModel):
    kind: ProviderKindLit
    json_schema: dict[str, Any]
    i18n_prefix: str  # "admin.identity.config.<kind>"
    read_only_fields: list[str]
    hidden_fields: list[str]


class IdentityPresetOut(BaseModel):
    key: str
    kind: ProviderKindLit
    name_key: str
    button_icon: str | None
    audience_default: AudienceLit


class IdentityCapabilitiesOut(BaseModel):
    saml_available: bool
    saml_missing: Literal["package", "xmlsec1"] | None
    public_base_url_set: bool
    webauthn: bool


class NamedRefOut(BaseModel):
    id: int
    name: str


class GroupPermRefOut(BaseModel):
    group_id: int
    group_name: str
    permission_key: str


class LoginPlanOut(BaseModel):
    user_kind: UserKindLit
    decision: Literal["login", "create", "reject"]
    matched_by: Literal["link", "login", "email", "jit"] | None
    user_id: int | None
    login: str | None
    reject_code: str | None
    reject_message: str | None
    profile: dict[str, str | None]
    profile_changes: dict[str, list[str | None]]  # field -> [old, new]
    roles_add: list[NamedRefOut]
    roles_remove: list[NamedRefOut]
    groups_add: list[GroupPermRefOut]
    groups_remove: list[GroupPermRefOut]


class IdentityTestStartIn(BaseModel):
    audience: UserKindLit = "agent"
    draft: ProviderChanges | None = None


class IdentityTestStartOut(BaseModel):
    test_id: str
    login_url: str


class IdentityTestResultOut(BaseModel):
    status: Literal["started", "done", "failed"]
    error: str | None
    subject: str | None
    attributes: dict[str, list[str]]
    plan: LoginPlanOut | None


class MappingPreviewIn(BaseModel):
    audience: UserKindLit = "agent"
    subject: str = "preview"
    attributes: dict[str, list[str]]
    draft: ProviderChanges | None = None


class IdentityLinkOut(BaseModel):
    id: int
    user_kind: UserKindLit
    subject: str
    user_id: int
    user_login: str
    first_seen: UtcDateTime
    last_seen: UtcDateTime


class ManagedAssignmentOut(BaseModel):
    user_id: int
    login: str
    kind: Literal["role", "group"]
    target_id: int
    target_name: str
    permission_key: str
    created: UtcDateTime
