import type { Schemas } from "@tiqora/api-client";
import { ApiError, api } from "./api";

export type LoginProvider = Schemas["LoginProviderOut"];

function loginUrl(base: string, slug: string, next?: string): string {
  const url = `${base}/${encodeURIComponent(slug)}/login`;
  return next ? `${url}?next=${encodeURIComponent(next)}` : url;
}

/** Browser navigation target that starts an agent sign-in with a provider. */
export function agentSsoLoginUrl(slug: string, next?: string): string {
  return loginUrl("/api/v1/auth/sso", slug, next);
}

/** Browser navigation target that starts a customer sign-in with a provider. */
export function portalSsoLoginUrl(slug: string, next?: string): string {
  return loginUrl("/api/portal/auth/sso", slug, next);
}

// ── Admin: identity providers (`backend/src/tiqora/api/v1/admin/identity_providers.py`) ──

export type IdentityProviderSummary = Schemas["IdentityProviderSummaryOut"];
export type IdentityProvider = Schemas["IdentityProviderOut"];
export type IdentityKind = Schemas["IdentityKindOut"];
export type IdentityPreset = Schemas["IdentityPresetOut"];
export type IdentityCapabilities = Schemas["IdentityCapabilitiesOut"];
export type ProviderChanges = Schemas["ProviderChanges"];
export type ProviderCreate = Schemas["IdentityProviderCreate"];
export type IdentityTestStart = Schemas["IdentityTestStartOut"];
export type IdentityTestResult = Schemas["IdentityTestResultOut"];
export type LoginPlan = Schemas["LoginPlanOut"];
export type ProviderEndpoints = Schemas["ProviderEndpointsOut"];
export type AttributeMap = Schemas["AttributeMap"];
export type JitDefaults = Schemas["JitDefaults"];
export type RoleRule = Schemas["RoleRule"];
export type GroupGrant = Schemas["GroupGrant"];
export type GroupPermission = NonNullable<GroupGrant["permissions"]>[number];
export type ManagedAssignment = Schemas["ManagedAssignmentOut"];
export type IdentityLink = Schemas["IdentityLinkOut"];
export type MappingPreviewBody = Schemas["MappingPreviewIn"];
export type ScimStatus = Schemas["ScimStatusOut"];
export type ScimToken = Schemas["ScimTokenOut"];
export type ScimTokenCreate = Schemas["ScimTokenCreateIn"];
export type ScimTokenCreated = Schemas["ScimTokenCreatedOut"];
export type ScimAccountPage = Schemas["ScimAccountPageOut"];
export type ScimAccount = Schemas["ScimAccountOut"];
export type ScimGroupPage = Schemas["ScimGroupPageOut"];
export type ScimGroup = Schemas["ScimGroupOut"];
export type ScimEvent = Schemas["ScimEventOut"];
export type ProviderAudience = "agent" | "customer" | "both";
export type TestAudience = "agent" | "customer";

const BASE = "/api/v1/admin/identity-providers";

function queryString(params: Record<string, string | number | boolean | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "" || v === false) continue;
    q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const identityApi = {
  list(signal?: AbortSignal) {
    return api.request<IdentityProviderSummary[]>("GET", BASE, { signal });
  },
  get(id: number, signal?: AbortSignal) {
    return api.request<IdentityProvider>("GET", `${BASE}/${id}`, { signal });
  },
  kinds(signal?: AbortSignal) {
    return api.request<IdentityKind[]>("GET", `${BASE}/kinds`, { signal });
  },
  presets(signal?: AbortSignal) {
    return api.request<IdentityPreset[]>("GET", `${BASE}/presets`, { signal });
  },
  capabilities(signal?: AbortSignal) {
    return api.request<IdentityCapabilities>("GET", `${BASE}/capabilities`, { signal });
  },
  create(body: ProviderCreate) {
    return api.request<IdentityProvider>("POST", BASE, { body });
  },
  update(id: number, body: ProviderChanges) {
    return api.request<IdentityProvider>("PATCH", `${BASE}/${id}`, { body });
  },
  remove(id: number) {
    return api.request<void>("DELETE", `${BASE}/${id}`);
  },
  refreshMetadata(id: number) {
    return api.request<IdentityProvider>("POST", `${BASE}/${id}/refresh-metadata`);
  },
  startTest(id: number, body: { audience: TestAudience; draft?: ProviderChanges | null }) {
    return api.request<IdentityTestStart>("POST", `${BASE}/${id}/tests`, { body });
  },
  preview(id: number, body: MappingPreviewBody, signal?: AbortSignal) {
    return api.request<LoginPlan>("POST", `${BASE}/${id}/preview`, { body, signal });
  },
  managedAssignments(id: number, signal?: AbortSignal) {
    return api.request<ManagedAssignment[]>("GET", `${BASE}/${id}/managed-assignments`, { signal });
  },
  links(id: number, signal?: AbortSignal) {
    return api.request<IdentityLink[]>("GET", `${BASE}/${id}/links`, { signal });
  },
  removeLink(id: number, linkId: number) {
    return api.request<void>("DELETE", `${BASE}/${id}/links/${linkId}`);
  },
  getScimStatus(id: number, signal?: AbortSignal) {
    return api.request<ScimStatus>("GET", `${BASE}/${id}/scim`, { signal });
  },
  createScimToken(id: number, body: ScimTokenCreate) {
    return api.request<ScimTokenCreated>("POST", `${BASE}/${id}/scim/tokens`, { body });
  },
  revokeScimToken(id: number, tokenId: number) {
    return api.request<void>("DELETE", `${BASE}/${id}/scim/tokens/${tokenId}`);
  },
  listScimAccounts(
    id: number,
    params: { offset?: number; limit?: number; q?: string; include_deleted?: boolean },
    signal?: AbortSignal,
  ) {
    return api.request<ScimAccountPage>("GET", `${BASE}/${id}/scim/accounts${queryString(params)}`, { signal });
  },
  listScimGroups(id: number, params: { offset?: number; limit?: number }, signal?: AbortSignal) {
    return api.request<ScimGroupPage>("GET", `${BASE}/${id}/scim/groups${queryString(params)}`, { signal });
  },
  listScimEvents(
    id: number,
    params: { before_id?: number; limit?: number; failures_only?: boolean },
    signal?: AbortSignal,
  ) {
    return api.request<ScimEvent[]>("GET", `${BASE}/${id}/scim/events${queryString(params)}`, { signal });
  },
  testResult(id: number, testId: string, signal?: AbortSignal) {
    return api.request<IdentityTestResult>("GET", `${BASE}/${id}/tests/${encodeURIComponent(testId)}`, {
      signal,
    });
  },
};

/** Field errors of a rejected save: `common` for name/slug/…, `config` for connection fields. */
export type ProviderFieldErrors = { common: Record<string, string>; config: Record<string, string> };

/**
 * Splits the `{"detail": {"errors": {"slug": …, "config.issuer": …}}}` of a 422
 * into errors for the common fields and for the connection (config) fields.
 */
export function providerFieldErrors(err: unknown): ProviderFieldErrors {
  const out: ProviderFieldErrors = { common: {}, config: {} };
  if (!(err instanceof ApiError)) return out;
  const body = err.detail as { detail?: { errors?: unknown } } | null;
  const errors = body && typeof body === "object" ? body.detail?.errors : null;
  if (!errors || typeof errors !== "object") return out;
  for (const [key, value] of Object.entries(errors as Record<string, unknown>)) {
    if (typeof value !== "string") continue;
    if (key.startsWith("config.")) out.config[key.slice("config.".length)] = value;
    else out.common[key] = value;
  }
  return out;
}

/** URL-safe suggestion for a provider slug: lowercase, umlauts transliterated, spaces to dashes. */
export function suggestSlug(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/ä/g, "ae")
    .replace(/ö/g, "oe")
    .replace(/ü/g, "ue")
    .replace(/ß/g, "ss")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63);
}
