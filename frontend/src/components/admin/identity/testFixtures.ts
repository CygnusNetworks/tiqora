import type {
  IdentityCapabilities,
  IdentityKind,
  IdentityPreset,
  IdentityProvider,
  IdentityProviderSummary,
} from "@/lib/identityApi";

export const oidcSchema = {
  type: "object",
  properties: {
    issuer: { type: "string", default: "", title: "Issuer" },
    client_id: { type: "string", default: "", title: "Client Id" },
    client_secret: {
      anyOf: [{ type: "string", format: "password" }, { type: "null" }],
      default: null,
      title: "Client Secret",
    },
    allow_private_network: { type: "boolean", default: false, title: "Allow Private Network" },
    allowed_tenant_ids: { type: "array", items: { type: "string" }, default: [], title: "Allowed Tenant Ids" },
  },
};

export const samlSchema = {
  type: "object",
  properties: {
    idp_metadata_url: { type: "string", default: "", title: "Idp Metadata Url" },
    allow_sha1_signatures: { type: "boolean", default: false, title: "Allow Sha1 Signatures" },
    sp_certificate: { type: "string", default: "", title: "Sp Certificate", "x-widget": "textarea" },
    sp_private_key: {
      anyOf: [{ type: "string", format: "password" }, { type: "null" }],
      default: null,
      title: "Sp Private Key",
    },
  },
};

export const ldapSchema = {
  type: "object",
  properties: {
    host: { type: "string", default: "", title: "Host" },
    use_ssl: { type: "boolean", default: false, title: "Use Ssl" },
    use_starttls: { type: "boolean", default: false, title: "Use Starttls" },
    tls_verify: { type: "boolean", default: true, title: "Tls Verify" },
  },
};

export const kinds: IdentityKind[] = [
  { kind: "oidc", json_schema: oidcSchema, i18n_prefix: "admin.identity.config.oidc", read_only_fields: [], hidden_fields: [] },
  {
    kind: "saml",
    json_schema: samlSchema,
    i18n_prefix: "admin.identity.config.saml",
    read_only_fields: ["sp_certificate", "sp_private_key"],
    hidden_fields: ["sp_private_key"],
  },
  { kind: "ldap", json_schema: ldapSchema, i18n_prefix: "admin.identity.config.ldap", read_only_fields: [], hidden_fields: [] },
];

export const capabilities: IdentityCapabilities = {
  saml_available: true,
  saml_missing: null,
  public_base_url_set: true,
  webauthn: true,
};

export const presets: IdentityPreset[] = [
  { key: "entra_id", kind: "oidc", name_key: "admin.identity.preset.entra_id", button_icon: "microsoft", audience_default: "agent" },
  { key: "adfs", kind: "saml", name_key: "admin.identity.preset.adfs", button_icon: "building", audience_default: "agent" },
  { key: "ldap", kind: "ldap", name_key: "admin.identity.preset.ldap", button_icon: "directory", audience_default: "agent" },
];

export const summaryRows: IdentityProviderSummary[] = [
  {
    id: 1, slug: "entra", kind: "oidc", source: "db", name: "Entra Büro", audience: "both", enabled: true, sort: 0,
    button_label: null, button_icon: "microsoft", preset: "entra_id", jit_enabled: false, read_only: false,
    metadata_error: null, link_count: 3,
  },
  {
    id: 2, slug: "env-oidc", kind: "oidc", source: "env", name: "Firmen-SSO", audience: "agent", enabled: true, sort: 1,
    button_label: null, button_icon: "key", preset: null, jit_enabled: false, read_only: true,
    metadata_error: null, link_count: 0,
  },
  {
    id: 3, slug: "adfs", kind: "saml", source: "db", name: "ADFS", audience: "agent", enabled: false, sort: 2,
    button_label: null, button_icon: "building", preset: "adfs", jit_enabled: false, read_only: false,
    metadata_error: null, link_count: 0,
  },
  {
    id: 4, slug: "ad", kind: "ldap", source: "db", name: "Verzeichnis", audience: "agent", enabled: true, sort: 3,
    button_label: null, button_icon: "directory", preset: "active_directory", jit_enabled: false, read_only: false,
    metadata_error: null, link_count: 0,
  },
];

export const endpoints = {
  agent_redirect_uri: "https://tickets.example.org/api/v1/auth/sso/entra/callback",
  customer_redirect_uri: "https://tickets.example.org/api/portal/auth/sso/entra/callback",
  agent_acs_url: "https://tickets.example.org/api/v1/auth/sso/adfs/acs",
  customer_acs_url: "https://tickets.example.org/api/portal/auth/sso/adfs/acs",
  sp_entity_id: "https://tickets.example.org/api/v1/auth/sso/adfs/metadata",
  sp_metadata_url: "https://tickets.example.org/api/v1/auth/sso/adfs/metadata",
  slo_url: "https://tickets.example.org/api/v1/auth/sso/adfs/slo",
  backchannel_logout_url: "https://tickets.example.org/api/v1/auth/sso/entra/backchannel-logout",
  base_url_missing: false,
};

export function makeProvider(over: Partial<IdentityProvider> = {}): IdentityProvider {
  return {
    id: 1, slug: "entra", kind: "oidc", source: "db", name: "Entra Büro", audience: "both", enabled: true, sort: 0,
    button_label: null, button_icon: "microsoft", preset: "entra_id", jit_enabled: false, read_only: false,
    metadata_error: null, link_count: 3,
    config: { issuer: "https://login.example.org/v2.0", client_id: "abc", allow_private_network: false },
    secrets_set: ["client_secret"],
    match_by: "login", require_sso_opt_in: true,
    attribute_map: { login: [], email: [], first_name: [], last_name: [], customer_id: [], mobile: [] },
    jit_defaults: {
      customer_id: "", initial_groups: [], initial_role_ids: [], update_profile_on_login: true, allowed_email_domains: [],
    },
    role_rules: [], sync_mode: "managed", metadata_fetched_at: null,
    endpoints: { ...endpoints },
    ...over,
  };
}
