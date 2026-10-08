import type { Schemas } from "@tiqora/api-client";

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
