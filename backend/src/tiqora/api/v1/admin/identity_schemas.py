"""Request/response models of ``/api/v1/admin/identity-providers`` (D16)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tiqora.domain.schemas import UtcDateTime
from tiqora.identity.config_models import (
    AttributeMap,
    JitDefaults,
    MfaFallback,
    MfaPolicy,
    RoleRule,
)
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


class MfaPolicyOut(MfaPolicy):
    """What counts as an IdP-side second factor (read model of :class:`MfaPolicy`)."""


class MfaPolicyIn(BaseModel):
    """Partial MFA policy: unset fields keep the stored value (merged by the route)."""

    model_config = ConfigDict(extra="forbid")

    fallback: MfaFallback | None = None
    oidc_amr_values: list[str] | None = None
    oidc_acr_values: list[str] | None = None
    oidc_request_acr_values: str | None = None
    saml_authn_context_classes: list[str] | None = None
    saml_mfa_attribute: str | None = None
    saml_mfa_attribute_values: list[str] | None = None
    saml_request_authn_context: bool | None = None


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
    require_mfa_claim: bool
    mfa_policy: MfaPolicyOut
    session_idle_seconds: int | None
    session_absolute_seconds: int | None
    scim_enabled: bool
    scim_adopt_existing: bool


class IdentityProviderPatch(ProviderChanges):
    """PATCH body: ``ProviderChanges`` with a partial ``mfa_policy`` (merged by the route)."""

    mfa_policy: MfaPolicyIn | None = None  # type: ignore[assignment]


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
    session_idle_max_seconds: int
    session_absolute_max_seconds: int
    scim_base_url: str | None


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


class IdentityTestMfaOut(BaseModel):
    required: bool
    satisfied: bool
    evidence: list[str]


MfaCheckOut = IdentityTestMfaOut  # plan name; the OpenAPI schema keeps its task-16 name


class IdentityTestResultOut(BaseModel):
    status: Literal["started", "done", "failed"]
    error: str | None
    subject: str | None
    attributes: dict[str, list[str]]
    plan: LoginPlanOut | None
    mfa: IdentityTestMfaOut | None = None


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


# --------------------------------------------------------------------------- SCIM administration


class ScimTokenOut(BaseModel):
    id: int
    name: str
    user_kind: UserKindLit
    token_hint: str
    created_at: UtcDateTime
    created_by_name: str | None
    expires_at: UtcDateTime
    revoked_at: UtcDateTime | None
    last_used_at: UtcDateTime | None
    active: bool


class ScimTokenCreateIn(BaseModel):
    name: str
    user_kind: UserKindLit
    expires_in_days: int = 365


class ScimTokenCreatedOut(BaseModel):
    token: str  # the only time the raw token is returned
    record: ScimTokenOut


class ScimStatusOut(BaseModel):
    base_url: str | None
    available: bool
    reason: str | None
    enabled: bool
    adopt_existing: bool
    tokens: list[ScimTokenOut]
    users: int
    active_users: int
    groups: int
    pending_sync: int
    last_activity_at: UtcDateTime | None


class ScimAccountOut(BaseModel):
    scim_id: str
    user_kind: UserKindLit
    user_id: int
    login: str | None
    user_name: str
    external_id: str | None
    active: bool
    deleted: bool
    changed: UtcDateTime


class ScimAccountPageOut(BaseModel):
    total: int
    items: list[ScimAccountOut]


class ScimGroupOut(BaseModel):
    scim_id: str
    display_name: str
    external_id: str | None
    member_count: int
    changed: UtcDateTime


class ScimGroupPageOut(BaseModel):
    total: int
    items: list[ScimGroupOut]


class ScimEventOut(BaseModel):
    id: int
    created: UtcDateTime
    method: str
    resource: str
    operation: str
    status: int
    scim_id: str | None
    user_kind: UserKindLit | None
    user_id: int | None
    login: str | None
    detail: str | None
